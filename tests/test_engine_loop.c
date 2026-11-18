/* Unit test for the engine's own logic, with a fake backend in place of an interface: when it
 * opens, reads the status of and syncs the device (and sends everything again), how often it
 * reads the meters with and without a GUI (and each interface's two rates), the one-engine lock,
 * the PipeWire object cap, and with a stand-in for PipeWire (struct pw_ops): running without
 * it, connecting when it comes and again after it was lost (never sending the mix again), and
 * the desktop sinks and sources: sinks.conf, which of them exist at each speed, the ports
 * they are linked through and the loopback's arguments.
 * The engine's source is included as it is, so nothing in it is built differently for the test;
 * its main() is not run, and nothing here talks to PipeWire or to a device. The shared memory is
 * a segment of its own, named after this process, and the config a temporary directory. */
#include <stddef.h>
#include <sys/wait.h>

#define main engine_main
#include "../engine/openface-mixer-engine.c"
#undef main

static int failed;

static void check(const char *what, long got, long want)
{
	if (got != want) {
		fprintf(stderr, "%s = %ld, want %ld\n", what, got, want);
		failed = 1;
	}
}

/* ---------- the fake backend ---------- */

static struct {
	bool present, open, reset;
	int status_err, sync_err;
	int opens, closes, statuses, syncs, full_syncs, meters;
} fake;

static void *fake_create(void)
{
	return &fake;
}

static void fake_destroy(void *h)
{
}

static int fake_open(void *h)
{
	fake.opens++;
	fake.open = fake.present;
	return fake.present ? DFU_STATE_ACTIVE : DFU_STATE_NO_DEVICE;
}

static void fake_close(void *h)
{
	fake.closes++;
	fake.open = false;
}

static bool fake_is_open(void *h)
{
	return fake.open;
}

static int fake_status(void *h, struct ofm_shm *s, bool *reset)
{
	fake.statuses++;
	s->rate = 48000;
	*reset = fake.reset;
	fake.reset = false;
	return fake.status_err;
}

static int fake_sync(void *h, struct ofm_shm *s, bool full)
{
	fake.syncs++;
	fake.full_syncs += full;
	return fake.sync_err;
}

static int fake_routes(void *h)
{
	return 3;
}

static int fake_meters(void *h, struct ofm_shm *s)
{
	fake.meters++;
	return 0;
}

/* ---------- the stand-in for PipeWire ---------- */

static struct {
	bool present;
	int ups, downs, loads, unloads;
} fake_pw;
static char fake_module;

static int fake_up(struct data *d)
{
	fake_pw.ups++;
	if (!fake_pw.present)
		return -ECONNREFUSED;
	d->core = (struct pw_core *)&fake_pw;
	return 0;
}

static void fake_down(struct data *d)
{
	fake_pw.downs++;
	d->core = NULL;
}

static void fake_load(struct data *d, struct pw_node *n)
{
	fake_pw.loads++;
	n->module = (struct pw_impl_module *)&fake_module;
}

static void fake_unload(struct data *d, struct pw_node *n)
{
	fake_pw.unloads++;
	n->module = NULL;
}

static const struct pw_ops fake_pw_ops = {
	.up = fake_up, .down = fake_down, .load = fake_load, .unload = fake_unload,
};

static char fake_key[32];
static struct ofm_backend fake_backend = {
	.key = fake_key,
	.name = "test device",
	.meter_us = 2000,
	.meter_idle_us = 10000000,
	.create = fake_create,
	.destroy = fake_destroy,
	.open = fake_open,
	.close = fake_close,
	.is_open = fake_is_open,
	.status = fake_status,
	.sync = fake_sync,
	.routes = fake_routes,
	.meters = fake_meters,
};

static void engine_init(struct data *d, struct ofm_shm *shm)
{
	pthread_condattr_t monotonic;

	memset(d, 0, sizeof(*d));
	memset(&fake, 0, sizeof(fake));
	memset(&fake_pw, 0, sizeof(fake_pw));
	d->pw = &fake_pw_ops;
	d->pw_said = true;            /* that PipeWire is not there: only check_pipewire wants it */
	d->be = &fake_backend;
	d->hw = &fake;
	d->shm = shm;
	spa_list_init(&d->links);
	pthread_mutex_init(&d->usb_lock, NULL);
	pthread_condattr_init(&monotonic);
	pthread_condattr_setclock(&monotonic, CLOCK_MONOTONIC);
	pthread_cond_init(&d->meter_wake, &monotonic);
}

static void ticks(struct data *d, int n)
{
	while (n-- > 0)
		hw_tick(d, 1);
}

/* ---------- shared memory and the one-engine lock ---------- */

static void shm_name(const struct data *d, char *name, size_t len)
{
	snprintf(name, len, "/openface-mixer-%s-%u", d->be->key, (unsigned)getuid());
}

static struct ofm_shm *check_shm(void)
{
	static struct data first, second;
	char name[64];
	int ready[2];

	/* the layout the GUI's constants (openface_mixer/engine.py, tests/test_engine.py) mirror */
	check("header size", offsetof(struct ofm_shm, gain), 64);
	check("gui_heartbeat", offsetof(struct ofm_shm, gui_heartbeat), 4 * 13);
	check("out_gain", offsetof(struct ofm_shm, out_gain), 10432);
	check("peak_src", offsetof(struct ofm_shm, peak_src), 10576);
	check("peak_out", offsetof(struct ofm_shm, peak_out), 10864);
	check("dev_config", offsetof(struct ofm_shm, dev_config), 11008);
	check("dev_cmd", offsetof(struct ofm_shm, dev_cmd), 11012);
	check("pw_reload", offsetof(struct ofm_shm, pw_reload), 27396);
	check("pw_linked", offsetof(struct ofm_shm, pw_linked), 27408);
	check("segment size", sizeof(struct ofm_shm), 27412);

	first.be = second.be = &fake_backend;
	check("first engine", shm_setup(&first), 0);
	check("version", first.shm->version, OFM_SHM_VERSION);
	check("its process", first.shm->engine_pid, getpid());
	check("default routing with no matrix file", first.shm->gain[5][OFM_N_IN + 5] == 1.0f, 1);

	/* a second engine for the device gives up and leaves the segment alone */
	first.shm->engine_pid = 12345;
	first.shm->gain[5][OFM_N_IN + 5] = 0.5f;
	check("second engine", shm_setup(&second), -EBUSY);
	check("the segment still names the first", first.shm->engine_pid, 12345);
	check("and has its mix", first.shm->gain[5][OFM_N_IN + 5] == 0.5f, 1);

	/* An engine for another device is killed while it holds its lock. One that is started
	 * meanwhile waits a moment for it, then runs: a dead engine's lock blocks nobody. */
	snprintf(fake_key, sizeof(fake_key), "selftest%db", (int)getpid());
	if (pipe(ready) < 0)
		return first.shm;
	pid_t child = fork();
	if (child == 0) {
		char c = shm_setup(&second) == 0;
		if (write(ready[1], &c, 1) == 1 && c) {
			usleep(300000);
			kill(getpid(), SIGKILL);
		}
		_exit(1);
	}
	char locked = 0;
	check("the other engine started", read(ready[0], &locked, 1) == 1 && locked, 1);
	int started = shm_setup(&second), status = 0;
	check("start while it is on its way out", started, 0);
	waitpid(child, &status, 0);
	check("it was killed", WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL, 1);
	if (started == 0)
		check("the segment names the new engine", second.shm->engine_pid, getpid());
	shm_name(&second, name, sizeof(name));
	shm_unlink(name);
	close(ready[0]);
	close(ready[1]);

	snprintf(fake_key, sizeof(fake_key), "selftest%d", (int)getpid());
	shm_name(&first, name, sizeof(name));
	shm_unlink(name);
	return first.shm;
}

/* ---------- what the tick asks of the backend, and when ---------- */

static void check_tick(struct ofm_shm *s)
{
	static struct data d;

	engine_init(&d, s);
	s->hw_state = DFU_STATE_STARTING;

	/* no device: looked for on the first tick and then once a second */
	ticks(&d, 1);
	check("looked for the device", fake.opens, 1);
	check("no device", s->hw_state, DFU_STATE_NO_DEVICE);
	ticks(&d, HW_RETRY_TICKS - 1);
	check("not again within the second", fake.opens, 1);
	check("heartbeat moves with every tick", s->heartbeat, HW_RETRY_TICKS);
	check("nothing sent to a device that isn't there", fake.statuses + fake.syncs, 0);

	/* it appears: opened, status read, and the whole mix sent, in that one tick */
	fake.present = true;
	ticks(&d, 1);
	check("opened", fake.opens, 2);
	check("status read", fake.statuses, 1);
	check("first sync", fake.syncs, 1);
	check("sends everything", fake.full_syncs, 1);
	check("active", s->hw_state, DFU_STATE_ACTIVE);
	check("routes", s->hw_nodes, 3);
	check("rate", s->rate, 48000);

	/* from then on every tick sends what changed, and every 25th reads the status */
	ticks(&d, 2 * HW_STATUS_TICKS);
	check("a sync per tick", fake.syncs, 1 + 2 * HW_STATUS_TICKS);
	check("none of them full", fake.full_syncs, 1);
	check("status twice a second", fake.statuses, 1 + 2);
	check("not opened again", fake.opens, 2);

	/* the device says it lost the mix (replug, resume, driver reload): everything again */
	fake.reset = true;
	ticks(&d, HW_STATUS_TICKS);
	check("full resend after a reset", fake.full_syncs, 2);
	ticks(&d, HW_STATUS_TICKS);
	check("once", fake.full_syncs, 2);

	/* more routes than the device has: applied as far as they go, and it stays open */
	fake.sync_err = -ENOSPC;
	ticks(&d, 1);
	check("out of routes", s->hw_state, DFU_STATE_NO_NODES);
	check("still open", fake.closes, 0);
	fake.sync_err = 0;
	ticks(&d, 1);
	check("active again", s->hw_state, DFU_STATE_ACTIVE);

	/* a failed transfer: the device is closed, looked for again within a second, and after
	 * the reconnect the whole mix is sent again */
	int opens = fake.opens, syncs;
	fake.sync_err = -EIO;
	ticks(&d, 1);
	check("closed", fake.closes, 1);
	check("error", s->hw_state, DFU_STATE_ERROR);
	check("no routes", s->hw_nodes, 0);
	check("no rate", s->rate, 0);
	fake.sync_err = 0;
	syncs = fake.syncs;
	ticks(&d, HW_RETRY_TICKS);
	check("opened again", fake.opens, opens + 1);
	check("full resend after the reconnect", fake.full_syncs, 3);
	check("only one full send", fake.syncs - syncs <= HW_RETRY_TICKS, 1);
	check("active after the reconnect", s->hw_state, DFU_STATE_ACTIVE);

	/* a device that stops answering reads as that, not as an error */
	fake.status_err = -ENODATA;
	ticks(&d, HW_STATUS_TICKS);
	check("no reply", s->hw_state, DFU_STATE_NO_REPLY);
	fake.status_err = 0;
}

/* ---------- meter pacing ---------- */

static int meter_reads(struct data *d)
{
	pthread_mutex_lock(&d->usb_lock);
	int n = fake.meters;
	pthread_mutex_unlock(&d->usb_lock);
	return n;
}

static long ms_now(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

/* The fake device's meters would be read every 2 ms for a GUI and every 10 s without one. */
static void check_meters(struct ofm_shm *s)
{
	static struct data d;
	pthread_t thread;
	int n;

	engine_init(&d, s);
	fake.present = true;
	ticks(&d, 1);                           /* opens and syncs */
	d.gui_heartbeat = s->gui_heartbeat;
	pthread_create(&thread, NULL, meter_thread, &d);

	/* nobody looks: one read, then the idle wait */
	usleep(300000);
	check("idle reads", meter_reads(&d), 1);
	check("meters reported as live", s->hw_levels, 1);

	/* a GUI reads the meters: the next tick ends the wait at once */
	long t0 = ms_now();
	s->gui_heartbeat++;
	ticks(&d, 1);
	while (meter_reads(&d) < 2 && ms_now() - t0 < 2000)
		usleep(500);
	long woke = ms_now() - t0;
	check("woken by the tick", meter_reads(&d) >= 2, 1);
	check("without waiting out the idle interval", woke < 1000, 1);

	/* while it keeps reading (30 times a second here), the device is read at the full rate */
	n = meter_reads(&d);
	t0 = ms_now();
	for (int i = 0; i < 15; i++) {
		s->gui_heartbeat++;
		ticks(&d, 2);
		usleep(33000);
	}
	long watched_ms = ms_now() - t0;
	int watched = meter_reads(&d) - n;
	check("full rate while watched", watched >= 50, 1);

	/* the GUI hides: after half a second of ticks without a heartbeat the reads stop */
	s->gui_heartbeat++;
	ticks(&d, HW_WATCH_TICKS);
	check("still watched just before", d.watched, 1);
	ticks(&d, 1);
	check("idle after 500 ms", d.watched, 0);
	usleep(20000);                          /* a read that was under way */
	n = meter_reads(&d);
	usleep(300000);
	check("no reads while idle", meter_reads(&d) - n, 0);
	printf("test_engine_loop: fake device read %ld times a second while a GUI reads the "
	       "meters, woken %ld ms after it starts, 0 times in 300 ms without one\n",
	       watched * 1000L / (watched_ms ? watched_ms : 1), woke);

	/* during a full send the meters wait (the mix comes first) */
	s->gui_heartbeat++;
	pthread_mutex_lock(&d.usb_lock);
	d.hw_need_full = true;
	n = fake.meters;
	pthread_mutex_unlock(&d.usb_lock);
	pthread_mutex_lock(&d.usb_lock);
	watch_gui(&d);
	pthread_mutex_unlock(&d.usb_lock);
	usleep(50000);
	check("no meter reads during a full send", meter_reads(&d) - n, 0);

	/* stopping does not wait for the idle interval either */
	ticks(&d, HW_WATCH_TICKS + 1);
	usleep(20000);
	t0 = ms_now();
	pthread_mutex_lock(&d.usb_lock);
	d.meter_quit = true;
	pthread_cond_signal(&d.meter_wake);
	pthread_mutex_unlock(&d.usb_lock);
	pthread_join(thread, NULL);
	check("the meter thread stops at once", ms_now() - t0 < 1000, 1);
}

/* How often each interface's meters are read, in microseconds: for a GUI, and with none. The USB
 * Firefaces' meter request is also their keep-alive, so theirs idles at oscmix's 10 a second. */
static void check_meter_rates(void)
{
	static const struct {
		const struct ofm_backend *be;
		unsigned int us, idle_us;
	} want[] = {
		{ &ofm_backend_digiface, 10000, 1000000 },
		{ &ofm_backend_ff802, 30000, 1000000 },
		{ &ofm_backend_ff800, 30000, 1000000 },
		{ &ofm_backend_ff400, 30000, 1000000 },
		{ &ofm_backend_ff802usb, 30000, 100000 },
		{ &ofm_backend_ucx2, 30000, 100000 },
		{ &ofm_backend_raydat, 20000, 1000000 },
		{ &ofm_backend_aio, 20000, 1000000 },
		{ &ofm_backend_aiopro, 20000, 1000000 },
		{ &ofm_backend_aes, 20000, 1000000 },
	};

	for (size_t i = 0; i < sizeof(want) / sizeof(want[0]); i++) {
		check(want[i].be->key, want[i].be->meter_us, want[i].us);
		check(want[i].be->key, want[i].be->meter_idle_us, want[i].idle_us);
	}
}

/* ---------- PipeWire object tracking ---------- */

static void check_object_cap(struct ofm_shm *s)
{
	static struct data d;
	struct spa_dict_item item = SPA_DICT_ITEM_INIT(PW_KEY_NODE_NAME, "some node");
	struct spa_dict props = SPA_DICT_INIT(&item, 1);
	char text[4096] = "";
	FILE *log = tmpfile();
	int saved = dup(2);

	engine_init(&d, s);
	if (log == NULL || saved < 0)
		return;
	fflush(stderr);
	dup2(fileno(log), 2);
	for (uint32_t id = 0; id < MAX_OBJ + 50; id++)
		registry_global(&d, id, 0, PW_TYPE_INTERFACE_Node, 0, &props);
	registry_global_remove(&d, 7);
	registry_global(&d, 9000, 0, PW_TYPE_INTERFACE_Node, 0, &props);
	registry_global(&d, 9001, 0, PW_TYPE_INTERFACE_Node, 0, &props);
	fflush(stderr);
	dup2(saved, 2);
	close(saved);

	check("objects tracked up to the cap", d.n_objs, MAX_OBJ);
	check("a freed place is used again", obj_find(&d, 9000) != NULL, 1);
	check("the next one is left out", obj_find(&d, 9001) == NULL, 1);
	rewind(log);
	size_t len = fread(text, 1, sizeof(text) - 1, log);
	text[len] = '\0';
	fclose(log);
	const char *line = strstr(text, "more than 2048 nodes, ports and links");
	check("the cap is logged", line != NULL, 1);
	check("once", line && strstr(line + 1, "more than 2048") == NULL, 1);
}

/* ---------- PipeWire: without it, when it comes, and when it goes ---------- */

/* stderr into a file while f runs, then its text. A failed check() says so all the same. */
static void quietly(void (*f)(void *), void *arg, char *text, size_t len)
{
	FILE *log = tmpfile();
	int saved = dup(2);

	text[0] = '\0';
	if (log == NULL || saved < 0)
		return;
	fflush(stderr);
	dup2(fileno(log), 2);
	f(arg);
	fflush(stderr);
	dup2(saved, 2);
	close(saved);
	rewind(log);
	size_t n = fread(text, 1, len - 1, log);
	text[n] = '\0';
	fclose(log);
	for (char *line = text; *line; line = strchr(line, '\n') ? strchr(line, '\n') + 1 : "")
		if (strncmp(line, "openface-mixer: ", 16) != 0)
			fprintf(stderr, "%.*s\n", (int)strcspn(line, "\n"), line);
}

static int count_of(const char *text, const char *what)
{
	int n = 0;
	for (const char *p = text; (p = strstr(p, what)) != NULL; p++)
		n++;
	return n;
}

static void run_pipewire(void *arg)
{
	struct data *d = arg;
	struct ofm_shm *s = d->shm;

	/* no PipeWire (JACK or ALSA only): the device is opened and sent the mix all the same, and
	 * PipeWire is looked for once a second */
	fake.present = true;
	pw_connect(d);                /* as the engine starts */
	ticks(d, 1);
	check("opened without PipeWire", fake.opens, 1);
	check("the mix is sent without PipeWire", fake.full_syncs, 1);
	check("active", s->hw_state, DFU_STATE_ACTIVE);
	check("looked for PipeWire", fake_pw.ups, 1);
	check("not connected", s->pw_connected, 0);
	ticks(d, 3 * HW_RETRY_TICKS);
	check("once a second", fake_pw.ups, 4);
	check("nothing made without it", fake_pw.loads, 0);

	/* PipeWire starts: connected at the next try, and the sink made. The device is not sent
	 * the mix again */
	fake_pw.present = true;
	ticks(d, HW_RETRY_TICKS);
	check("connected", s->pw_connected, 1);
	check("tries", fake_pw.ups, 5);
	check("the sink made", fake_pw.loads, 1);
	check("made", s->pw_nodes, 1);
	ticks(d, 3 * HW_RETRY_TICKS);
	check("no more tries", fake_pw.ups, 5);

	/* PipeWire crashes and comes back: the engine lets go at the next tick, connects again a
	 * second later, and makes the sink again; the device keeps its mix */
	int syncs = fake.syncs;
	core_error(d, 31, 0, -EPIPE, "some other object's error");
	core_error(d, PW_ID_CORE, 0, -ENOENT, "an error that is not the end of the connection");
	ticks(d, 1);
	check("other errors leave the connection", fake_pw.downs, 0);
	core_error(d, PW_ID_CORE, 0, -EPIPE, "connection error");
	core_error(d, PW_ID_CORE, 0, -EPIPE, "and another one");
	ticks(d, 1);
	check("let go", fake_pw.downs, 1);
	check("its sink with it", fake_pw.unloads, 1);
	check("not connected", s->pw_connected, 0);
	check("nothing made", s->pw_nodes, 0);
	check("nothing linked", s->sink_linked, 0);
	ticks(d, HW_RETRY_TICKS - 1);
	check("not straight away", fake_pw.ups, 5);
	ticks(d, 1);
	check("again a second later", fake_pw.ups, 6);
	check("connected again", s->pw_connected, 1);
	check("the sink made again", fake_pw.loads, 2);
	ticks(d, 1);
	check("the mix was never sent again", fake.full_syncs, 1);
	check("but kept in line all along", fake.syncs - syncs, HW_RETRY_TICKS + 3);
	check("and the device never closed", fake.closes, 0);

	/* PipeWire stops for good: tried once a second, logged once */
	fake_pw.present = false;
	core_error(d, PW_ID_CORE, 0, -EPIPE, "connection error");
	ticks(d, 5 * HW_RETRY_TICKS + 1);
	check("tried again", fake_pw.ups, 11);
	check("still active", s->hw_state, DFU_STATE_ACTIVE);
}

static void check_pipewire(struct ofm_shm *s)
{
	static struct data d;
	char text[4096];

	engine_init(&d, s);
	d.pw_said = false;
	nodes_load(&d);               /* no sinks.conf: the playback sink */
	quietly(run_pipewire, &d, text, sizeof(text));
	check("not running, said once per outage", count_of(text, "PipeWire is not running"), 2);
	check("lost, said once", count_of(text, "lost the connection to PipeWire"), 2);
	check("connected, said each time", count_of(text, "connected to PipeWire\n"), 2);
	nodes_free(&d);
}

/* ---------- sinks.conf ---------- */

static int parse(const char *text, struct pw_node_cfg *cfg, int max)
{
	char buf[4096];
	snprintf(buf, sizeof(buf), "%s", text);
	return nodes_parse(buf, cfg, max);
}

static void check_nodes_parse(void)
{
	struct pw_node_cfg cfg[4];

	check("a sink and a mono source",
	      parse("# a comment\n\nsink openface_mixer_play_3_4 2 3 2 3 -1 -1 Openface Music\n"
		    "source openface_mixer_in_1 0 -1 0 -1 0 -1 Openface Mic \r\n", cfg, 4), 2);
	check("sink", cfg[0].source, 0);
	check("its name", strcmp(cfg[0].name, "openface_mixer_play_3_4"), 0);
	check("what the desktop shows", strcmp(cfg[0].desc, "Openface Music"), 0);
	check("left at 2x", cfg[0].chan[1][0], 2);
	check("not at 4x", cfg[0].chan[2][0], -1);
	check("source", cfg[1].source, 1);
	check("mono", cfg[1].chan[0][1], -1);
	check("trailing space and CR left out", strcmp(cfg[1].desc, "Openface Mic"), 0);
	check("utf-8 names", parse("sink a 0 1 0 1 0 1 Openface Müsik\n", cfg, 4), 1);
	check("empty: none at all", parse("# nothing\n", cfg, 4), 0);

	static const char *const bad[] = {
		"sink a 0 1 0 1 0 1\n",                          /* no description */
		"sink a 0 1 0 1 0\n",                            /* a channel short */
		"sinks a 0 1 0 1 0 1 x\n",
		"sink a 0 -1 0 1 0 1 x\n",                       /* a mono sink */
		"source a -1 1 0 1 0 1 x\n",                     /* a right without a left */
		"sink a 0 64 0 1 0 1 x\n",
		"sink a -2 1 0 1 0 1 x\n",
		"sink a\"b 0 1 0 1 0 1 x\n",                     /* would end the quoted name */
		"sink a 0 1 0 1 0 1 x\" y\n",
		"sink a 0 1 0 1 0 1 x\\\n",
		"sink a 0 1 0 1 0 1 x\ty\n",
		"sink a 0 1 0 1 0 1 x\nsink b 0 1 0 1 0 1 y\nsink c 0 1 0 1 0 1 z\n"
		"sink d 0 1 0 1 0 1 w\nsink e 0 1 0 1 0 1 v\n",  /* more than there is room for */
		"sink a 0 1 0 1 0 1 "
		"0123456789012345678901234567890123456789012345678901234567890123456789"
		"0123456789012345678901234567890\n",              /* description too long */
		"sink 0123456789012345678901234567890123456789012345678901234567890123456789 "
		"0 1 0 1 0 1 x\n",                                /* name too long */
	};
	for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
		char what[32];
		snprintf(what, sizeof(what), "bad file %zu", i);
		check(what, parse(bad[i], cfg, 4), -1);
	}
}

static void write_conf(const char *text)
{
	char path[512];
	FILE *f;
	snprintf(path, sizeof(path), "%s/openface-mixer", getenv("XDG_CONFIG_HOME"));
	mkdir(path, 0700);
	snprintf(path, sizeof(path), "%s/openface-mixer/%s", getenv("XDG_CONFIG_HOME"), fake_key);
	mkdir(path, 0700);
	snprintf(path, sizeof(path), "%s/openface-mixer/%s/" NODES_FILE, getenv("XDG_CONFIG_HOME"),
		 fake_key);
	if (text == NULL) {
		unlink(path);
		return;
	}
	f = fopen(path, "w");
	if (f == NULL) {
		perror(path);
		failed = 1;
		return;
	}
	fputs(text, f);
	fclose(f);
}

static void remove_conf(void)
{
	char path[512];
	write_conf(NULL);
	snprintf(path, sizeof(path), "%s/openface-mixer/%s", getenv("XDG_CONFIG_HOME"), fake_key);
	rmdir(path);
	snprintf(path, sizeof(path), "%s/openface-mixer", getenv("XDG_CONFIG_HOME"));
	rmdir(path);
}

static void run_nodes(void *arg)
{
	struct data *d = arg;
	struct ofm_shm *s = d->shm;
	char conf[1024], sink[160];

	snprintf(sink, sizeof(sink), "sink openface_mixer_playback_%s 0 1 0 1 0 1 Openface Mixer "
		 "Playback (test device)\n", fake_key);

	fake.present = fake_pw.present = true;
	nodes_load(d);
	pw_connect(d);
	ticks(d, 1);
	check("the default: one sink", d->n_nodes, 1);
	check("named as always", strncmp(sink + 5, d->nodes[0]->cfg.name, strlen(d->nodes[0]->cfg.name)), 0);
	check("described as always",
	      strcmp(d->nodes[0]->cfg.desc, "Openface Mixer Playback (test device)"), 0);
	check("on PCM 1/2", d->nodes[0]->cfg.chan[2][1], 1);
	check("made", fake_pw.loads, 1);

	/* the GUI writes a list and says so: the playback sink stays as it is, the rest is made */
	snprintf(conf, sizeof(conf), "%ssink openface_mixer_test_play_3_4 2 3 2 3 -1 -1 Openface "
		 "Music\nsource openface_mixer_test_in_1 0 -1 0 -1 0 -1 Openface Mic\n", sink);
	write_conf(conf);
	ticks(d, 1);
	check("read only when the GUI says", d->n_nodes, 1);
	s->pw_reload++;
	ticks(d, 1);
	check("three", d->n_nodes, 3);
	check("two more made", fake_pw.loads, 3);
	check("the first one kept", fake_pw.unloads, 0);
	check("all three made", s->pw_nodes, 3);

	/* renamed: made again under its new name; the others are left alone */
	snprintf(conf, sizeof(conf), "%ssink openface_mixer_test_play_3_4 2 3 2 3 -1 -1 Openface "
		 "Films\nsource openface_mixer_test_in_1 0 -1 0 -1 0 -1 Openface Mic\n", sink);
	write_conf(conf);
	s->pw_reload++;
	ticks(d, 1);
	check("one removed", fake_pw.unloads, 1);
	check("and made again", fake_pw.loads, 4);
	check("with its new name", strcmp(d->nodes[1]->cfg.desc, "Openface Films"), 0);

	/* at quad speed the interface has no 3/4 to feed: that sink goes, and comes back */
	s->rate = 192000;
	pw_tick(d);
	check("gone at 192 kHz", d->nodes[1]->module == NULL, 1);
	check("the others stay", d->nodes[0]->module != NULL && d->nodes[2]->module != NULL, 1);
	check("two made", s->pw_nodes, 2);
	s->rate = 48000;
	pw_tick(d);
	check("back at 48 kHz", d->nodes[1]->module != NULL, 1);
	s->rate = 0;                        /* device gone: the last speed stays */
	pw_tick(d);
	check("an unknown rate changes nothing", d->speed, 0);

	/* a damaged file: the playback sink only, as with none */
	write_conf("sink openface_mixer_test_play_3_4 2 3 2 3 -1 -1 Openface \"Music\"\n");
	s->pw_reload++;
	ticks(d, 1);
	check("damaged: the default", d->n_nodes, 1);
	check("which was there all along", fake_pw.loads, 5);

	/* none at all */
	write_conf("# none\n");
	s->pw_reload++;
	ticks(d, 1);
	check("an empty list", d->n_nodes, 0);
	check("nothing made", s->pw_nodes, 0);
	check("nothing linked", s->sink_linked, 0);
	remove_conf();
}

static void check_nodes(struct ofm_shm *s)
{
	static struct data d;
	char text[4096];

	engine_init(&d, s);
	quietly(run_nodes, &d, text, sizeof(text));
	check("the damaged file is said", count_of(text, "is damaged; ignoring it"), 1);
	nodes_free(&d);
}

/* ---------- the ports a sink or source is linked through ---------- */

static void add(struct data *d, uint32_t id, const char *type, const char *k1, const char *v1,
		const char *k2, const char *v2, const char *k3, const char *v3)
{
	struct spa_dict_item items[3] = {
		SPA_DICT_ITEM_INIT(k1, v1), SPA_DICT_ITEM_INIT(k2, v2), SPA_DICT_ITEM_INIT(k3, v3),
	};
	struct spa_dict props = SPA_DICT_INIT(items, k3 ? 3 : k2 ? 2 : 1);
	registry_global(d, id, 0, type, 0, &props);
}

static void add_port(struct data *d, uint32_t id, uint32_t node, const char *name, bool out)
{
	char node_id[16];
	snprintf(node_id, sizeof(node_id), "%u", node);
	add(d, id, PW_TYPE_INTERFACE_Port, PW_KEY_PORT_NAME, name, PW_KEY_NODE_ID, node_id,
	    PW_KEY_PORT_DIRECTION, out ? "out" : "in");
}

static void add_link(struct data *d, uint32_t id, uint32_t from, uint32_t to)
{
	char a[16], b[16];
	snprintf(a, sizeof(a), "%u", from);
	snprintf(b, sizeof(b), "%u", to);
	add(d, id, PW_TYPE_INTERFACE_Link, PW_KEY_LINK_OUTPUT_PORT, a, PW_KEY_LINK_INPUT_PORT, b,
	    NULL, NULL);
}

static void check_ports(struct ofm_shm *s)
{
	static struct data d;
	struct obj *from[2], *to[2];
	char name[32];

	engine_init(&d, s);
	fake_backend.out_node_prefix = "alsa_output.usb-test";
	fake_pw.present = true;
	d.no_autolink = 1;        /* no links are made here; the registry's are looked at */
	write_conf("sink openface_mixer_playback_test 0 1 0 1 0 1 Openface Mixer Playback\n"
		   "sink openface_mixer_test_play_3_4 2 3 2 3 -1 -1 Openface Music\n"
		   "source openface_mixer_test_in_2 1 -1 1 -1 1 -1 Openface Mic\n"
		   "source openface_mixer_test_in_3_4 2 3 2 3 -1 -1 Openface Line\n");
	nodes_load(&d);
	pw_connect(&d);
	check("four made", s->pw_nodes, 4);

	/* the interface's nodes, in Pro Audio: 4 playback and 4 capture channels */
	add(&d, 100, PW_TYPE_INTERFACE_Node, PW_KEY_NODE_NAME, "alsa_output.usb-test.pro-output-0",
	    NULL, NULL, NULL, NULL);
	add(&d, 101, PW_TYPE_INTERFACE_Node, PW_KEY_NODE_NAME, "alsa_input.usb-test.pro-input-0",
	    NULL, NULL, NULL, NULL);
	for (int c = 0; c < 4; c++) {
		snprintf(name, sizeof(name), "playback_AUX%d", c);
		add_port(&d, 110 + c, 100, name, false);
		snprintf(name, sizeof(name), "capture_AUX%d", c);
		add_port(&d, 120 + c, 101, name, true);
	}
	/* the loopbacks' ends the engine links */
	add(&d, 200, PW_TYPE_INTERFACE_Node, PW_KEY_NODE_NAME, "openface_mixer_playback_test.out",
	    NULL, NULL, NULL, NULL);
	add_port(&d, 201, 200, "output_FL", true);
	add_port(&d, 202, 200, "output_FR", true);
	add(&d, 210, PW_TYPE_INTERFACE_Node, PW_KEY_NODE_NAME, "openface_mixer_test_play_3_4.out",
	    NULL, NULL, NULL, NULL);
	add_port(&d, 211, 210, "output_FL", true);
	add_port(&d, 212, 210, "output_FR", true);
	add(&d, 220, PW_TYPE_INTERFACE_Node, PW_KEY_NODE_NAME, "openface_mixer_test_in_2.in",
	    NULL, NULL, NULL, NULL);
	add_port(&d, 221, 220, "input_MONO", false);
	add(&d, 230, PW_TYPE_INTERFACE_Node, PW_KEY_NODE_NAME, "openface_mixer_test_in_3_4.in",
	    NULL, NULL, NULL, NULL);
	add_port(&d, 231, 230, "input_FL", false);
	add_port(&d, 232, 230, "input_FR", false);

	check("the playback sink: two channels", node_ports(&d, d.nodes[0], from, to), 2);
	check("its left to playback 1", from[0]->id * 1000 + to[0]->id, 201110);
	check("its right to playback 2", from[1]->id * 1000 + to[1]->id, 202111);
	check("Music", node_ports(&d, d.nodes[1], from, to), 2);
	check("to playback 3/4", to[0]->id * 1000 + to[1]->id, 112113);
	check("a mono source", node_ports(&d, d.nodes[2], from, to), 1);
	check("from capture 2", from[0]->id * 1000 + to[0]->id, 121221);
	check("a stereo source", node_ports(&d, d.nodes[3], from, to), 2);
	check("from capture 3/4", from[0]->id * 1000 + from[1]->id, 122123);
	check("into its left and right", to[0]->id * 1000 + to[1]->id, 231232);
	check("none linked yet", s->pw_linked, 0);

	/* once PipeWire has the links, they count */
	add_link(&d, 300, 201, 110);
	add_link(&d, 301, 202, 111);
	check("one linked", s->pw_linked, 1);
	check("not all", s->sink_linked, 0);
	add_link(&d, 302, 211, 112);
	add_link(&d, 303, 212, 113);
	add_link(&d, 304, 121, 221);
	check("three linked", s->pw_linked, 3);
	add_link(&d, 305, 122, 231);
	add_link(&d, 306, 123, 232);
	check("all", s->sink_linked, 1);

	/* quad speed: Music and Line go, and the rest stay linked */
	s->rate = 192000;
	pw_tick(&d);
	check("two made at 192 kHz", s->pw_nodes, 2);
	check("both linked", s->sink_linked, 1);
	s->rate = 48000;
	pw_tick(&d);

	/* a stereo profile: the first two channels are FL and FR */
	registry_global_remove(&d, 110);
	add_port(&d, 140, 100, "playback_FL", false);
	add_port(&d, 141, 100, "playback_FR", false);
	check("stereo profile", node_ports(&d, d.nodes[0], from, to), 2);
	check("to FL and FR", to[0]->id * 1000 + to[1]->id, 140141);
	check("3/4 is not there", node_ports(&d, d.nodes[1], from, to), 2);
	check("but AUX2 and AUX3 are", to[0]->id, 112);

	/* the interface's node goes (unplugged): nothing linked, nothing made again */
	int loads = fake_pw.loads;
	registry_global_remove(&d, 100);
	check("not linked to a missing interface", node_ports(&d, d.nodes[0], from, to), 0);
	check("sinks unlinked", s->sink_linked, 0);
	check("and left as they are", fake_pw.loads, loads);

	remove_conf();
	fake_backend.out_node_prefix = NULL;
	nodes_free(&d);
}

/* ---------- the loopback's arguments ---------- */

static void check_args(void)
{
	static struct data d;
	struct pw_node_cfg cfg[3];
	static struct pw_node nodes[3];
	char args[1024];

	d.be = &ofm_backend_digiface;
	nodes_default(&d, &cfg[0]);
	check("the Digiface's sink", parse("source openface_mixer_in_1 0 -1 0 -1 0 -1 Openface Mic\n"
					   "sink openface_mixer_play_3_4 2 3 2 3 -1 -1 Music\n",
					   &cfg[1], 2), 2);
	for (int i = 0; i < 3; i++) {
		nodes[i].cfg = cfg[i];
		d.nodes[i] = &nodes[i];
	}
	d.n_nodes = 3;
	/* the first sink's, word for word what the engine always gave the playback sink */
	node_args(&d, &nodes[0], args, sizeof(args));
	check("playback sink", strcmp(args,
		"{ node.description = \"Openface Mixer Playback\""
		"  capture.props = { node.name = \"openface_mixer_playback\" media.class = Audio/Sink"
		"                    audio.position = [ FL FR ] priority.session = 100 priority.driver = 100 }"
		"  playback.props = { node.name = \"openface_mixer_playback.out\" node.autoconnect = false"
		"                     node.dont-reconnect = true audio.position = [ FL FR ] } }"), 0);
	node_args(&d, &nodes[1], args, sizeof(args));
	check("a mono source", strcmp(args,
		"{ node.description = \"Openface Mic\""
		"  capture.props = { node.name = \"openface_mixer_in_1.in\" node.autoconnect = false"
		"                    node.dont-reconnect = true audio.position = [ MONO ] }"
		"  playback.props = { node.name = \"openface_mixer_in_1\" media.class = Audio/Source"
		"                     audio.position = [ MONO ] } }"), 0);
	node_args(&d, &nodes[2], args, sizeof(args));
	check("a second sink is no default", strstr(args, "priority") == NULL, 1);
	check("named", strstr(args, "node.name = \"openface_mixer_play_3_4.out\"") != NULL, 1);
	nodes[0].cfg.source = true;
	node_args(&d, &nodes[2], args, sizeof(args));
	check("the first sink is", strstr(args, "priority.session = 100") != NULL, 1);
}

int main(void)
{
	char config[] = "/tmp/ofm-test-XXXXXX";

	if (mkdtemp(config) == NULL) {
		perror("mkdtemp");
		return 1;
	}
	setenv("XDG_CONFIG_HOME", config, 1);    /* where the engine looks for matrix.bin */
	snprintf(fake_key, sizeof(fake_key), "selftest%d", (int)getpid());

	struct ofm_shm *s = check_shm();
	check_tick(s);
	check_meters(s);
	check_meter_rates();
	check_object_cap(s);
	check_pipewire(s);
	check_nodes_parse();
	check_nodes(s);
	check_ports(s);
	check_args();
	rmdir(config);
	if (!failed)
		printf("test_engine_loop: ok\n");
	return failed;
}
