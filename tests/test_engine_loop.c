/* Unit test for the engine's own logic, with a fake backend in place of an interface: when it
 * opens, reads the status of and syncs the device (and sends everything again), how often it
 * reads the meters with and without a GUI, the one-engine lock, and the PipeWire object cap.
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
	check("segment size", sizeof(struct ofm_shm), 27396);

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
	check_object_cap(s);
	rmdir(config);
	if (!failed)
		printf("test_engine_loop: ok\n");
	return failed;
}
