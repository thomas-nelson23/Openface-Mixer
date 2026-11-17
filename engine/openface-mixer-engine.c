/*
 * openface-mixer-engine - keeps an RME interface's DSP mixer in line with the GUI's mix.
 *
 * The mix (a gain matrix plus output faders, and device settings) lives in shared memory (see
 * shm_layout.h), written by the GUI. A 20 ms main-loop timer loads it into the interface's own
 * DSP mixer, sending only what changed, so monitoring happens inside the interface with no
 * added latency, as with TotalMix. The device's level meters come back the same way. The
 * device is a backend (backend.h): the Digiface USB over USB (digiface_usb.c), the
 * Fireface 802, 800 or 400 over FireWire (fireface_fw.c), the Fireface 802 or UCX II over USB
 * in Class Compliant mode (fireface_usb.c), or an HDSPe RayDAT, AIO, AIO Pro or AES PCIe card
 * through its kernel driver (hdspe.c).
 *
 * The engine also creates the "Openface Mixer Playback" stereo sink and links it to the
 * interface's playback channels 1/2, re-linking on hotplug or profile changes. It runs headless
 * (systemd user service), and the interface keeps mixing with the last mix when it exits.
 *
 * Options:
 *   --device KEY    digiface (default), ff802, ff802usb, ucx2, ff800, ff400, raydat, aio,
 *                   aiopro or aes
 *   --no-sink       don't create the "Openface Mixer Playback" virtual sink
 *   --no-autolink   don't link the sink to the interface (useful for testing with pw-link)
 */
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <pipewire/pipewire.h>
#include <pipewire/impl.h>
#include <spa/utils/dict.h>

#include "backend.h"
#include "digiface_usb.h"
#include "matrix_file.h"
#include "shm_layout.h"

#define N_IN    OFM_N_IN
#define N_PLAY  OFM_N_PLAY
#define N_SRC   OFM_N_SRC
#define N_OUT   OFM_N_OUT

#define SINK_NAME       "openface_mixer_playback"  /* other devices than the Digiface: _<key> added */

#define HW_TICK_MS       20  /* how often GUI changes are sent to the hardware mixer */
#define HW_STATUS_TICKS  25  /* read device status every 500 ms */
#define HW_RETRY_TICKS   50  /* look for the device every second */
#define HW_LOG_EVERY     60  /* a device that keeps failing is logged once in this many rounds */
#define HW_STABLE_TICKS  500 /* and counts as working again once it has stayed open for 10 s */
#define HW_WATCH_TICKS   25  /* the meters count as watched for 500 ms after a GUI last read them */

#define MAX_OBJ 2048

struct obj {
	uint32_t id;
	int type; /* 1 node, 2 port, 3 link */
	char name[160];     /* node.name or port.name */
	char card[64];      /* nodes: ALSA card name, if any */
	uint32_t node_id;   /* ports */
	int dir_out;        /* ports: 1 = output */
	uint32_t out_port, in_port; /* links */
};

struct link_req {
	struct spa_list link;
	struct pw_proxy *proxy;
	struct spa_hook listener;
	uint32_t out_port, in_port;
	struct data *d;
};

struct data {
	struct pw_main_loop *loop;
	struct pw_context *context;
	struct pw_core *core;
	struct pw_registry *registry;
	struct spa_hook registry_listener;
	struct pw_impl_module *loopback;

	struct ofm_shm *shm;

	const struct ofm_backend *be;
	void *hw;
	struct spa_source *hw_timer;
	unsigned int hw_ticks;
	bool hw_need_full;    /* rewrite every node, e.g. after the device was reset */
	unsigned int hw_fails;  /* times the device was lost since it last kept working */
	unsigned int hw_opened; /* hw_ticks when it was opened */

	/* hardware meters are read on their own thread; usb_lock keeps device I/O on the main loop
	 * and the meter reads apart. Between reads the thread waits on meter_wake: the backend's
	 * meter interval while a GUI reads the meters (its heartbeat moves), the idle one otherwise */
	pthread_mutex_t usb_lock;
	pthread_cond_t meter_wake;
	pthread_t meter_thread;
	bool meter_thread_started;
	bool meter_quit;
	uint32_t gui_heartbeat;  /* the GUI's heartbeat as last seen */
	unsigned int gui_seen;   /* hw_ticks when it last moved */
	bool watched;

	int no_autolink;
	char sink_out_name[96];
	struct obj objs[MAX_OBJ];
	int n_objs;
	struct spa_list links;
};

/* ---------- shared memory ---------- */

static bool is_digiface(const struct data *d)
{
	return d->be == &ofm_backend_digiface;
}

/* The Digiface's files sit in the config directory itself, other devices' in a subdirectory. */
static void matrix_path(const struct data *d, char *buf, size_t len)
{
	const char *xdg = getenv("XDG_CONFIG_HOME");
	const char *sub = is_digiface(d) ? "" : d->be->key;
	const char *sep = is_digiface(d) ? "" : "/";
	if (xdg && *xdg)
		snprintf(buf, len, "%s/openface-mixer/%s%smatrix.bin", xdg, sub, sep);
	else
		snprintf(buf, len, "%s/.config/openface-mixer/%s%smatrix.bin",
			 getenv("HOME") ? getenv("HOME") : "/tmp", sub, sep);
}

static int shm_setup(struct data *d)
{
	char name[64];
	if (is_digiface(d))
		snprintf(name, sizeof(name), "/openface-mixer-%u", (unsigned)getuid());
	else
		snprintf(name, sizeof(name), "/openface-mixer-%s-%u", d->be->key, (unsigned)getuid());
	int fd = shm_open(name, O_CREAT | O_RDWR, 0600);
	if (fd < 0) {
		perror("shm_open");
		return -1;
	}
	if (ftruncate(fd, sizeof(struct ofm_shm)) < 0) {
		perror("ftruncate");
		close(fd);
		return -1;
	}
	d->shm = mmap(NULL, sizeof(struct ofm_shm), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	close(fd);
	if (d->shm == MAP_FAILED) {
		perror("mmap");
		return -1;
	}

	struct ofm_shm *s = d->shm;
	if (s->magic != OFM_SHM_MAGIC || s->version != OFM_SHM_VERSION) {
		memset(s, 0, sizeof(*s));
		char path[512];
		matrix_path(d, path, sizeof(path));
		int r = ofm_matrix_load(s, path);
		if (r < 0)
			ofm_matrix_default(s);
		if (r == -ENOENT)
			fprintf(stderr, "openface-mixer: no %s yet, using the default routing\n", path);
		else if (r < 0)
			fprintf(stderr, "openface-mixer: %s is damaged or not a format this engine "
				"knows; ignoring it and using the default routing "
				"(playback n to output n)\n", path);
		else
			fprintf(stderr, "openface-mixer: loaded matrix from %s\n", path);
		s->n_src = N_SRC;
		s->n_out = N_OUT;
		s->version = OFM_SHM_VERSION;
		s->magic = OFM_SHM_MAGIC;
	}
	s->engine_pid = (uint32_t)getpid();
	return 0;
}

/* ---------- registry tracking & auto-linking ---------- */

static struct obj *obj_find(struct data *d, uint32_t id)
{
	for (int i = 0; i < d->n_objs; i++)
		if (d->objs[i].id == id)
			return &d->objs[i];
	return NULL;
}

static struct obj *node_by_prefix(struct data *d, const char *prefix, int exact)
{
	for (int i = 0; i < d->n_objs; i++) {
		struct obj *o = &d->objs[i];
		if (o->type != 1)
			continue;
		if (exact ? strcmp(o->name, prefix) == 0 : strncmp(o->name, prefix, strlen(prefix)) == 0)
			return o;
	}
	return NULL;
}

static struct obj *node_by_card(struct data *d, const char *prefix)
{
	for (int i = 0; i < d->n_objs; i++) {
		struct obj *o = &d->objs[i];
		if (o->type == 1 && strncmp(o->name, "alsa_output.", 12) == 0 &&
		    ofm_card_match(o->card, prefix))
			return o;
	}
	return NULL;
}

static struct obj *port_find(struct data *d, uint32_t node_id, const char *name, int dir_out)
{
	for (int i = 0; i < d->n_objs; i++) {
		struct obj *o = &d->objs[i];
		if (o->type == 2 && o->node_id == node_id && o->dir_out == dir_out && strcmp(o->name, name) == 0)
			return o;
	}
	return NULL;
}

static int link_exists(struct data *d, uint32_t out_port, uint32_t in_port)
{
	for (int i = 0; i < d->n_objs; i++) {
		struct obj *o = &d->objs[i];
		if (o->type == 3 && o->out_port == out_port && o->in_port == in_port)
			return 1;
	}
	struct link_req *r;
	spa_list_for_each(r, &d->links, link)
		if (r->out_port == out_port && r->in_port == in_port)
			return 1;
	return 0;
}

static void link_req_free(struct link_req *r)
{
	spa_list_remove(&r->link);
	spa_hook_remove(&r->listener);
	free(r);
}

static void link_proxy_removed(void *data)
{
	struct link_req *r = data;
	pw_proxy_destroy(r->proxy);
}

static void link_proxy_destroy(void *data)
{
	link_req_free(data);
}

static void link_proxy_error(void *data, int seq, int res, const char *message)
{
	struct link_req *r = data;
	fprintf(stderr, "openface-mixer: link %u->%u failed: %s\n", r->out_port, r->in_port, message);
}

static const struct pw_proxy_events link_proxy_events = {
	PW_VERSION_PROXY_EVENTS,
	.removed = link_proxy_removed,
	.destroy = link_proxy_destroy,
	.error = link_proxy_error,
};

static void make_link(struct data *d, struct obj *op, struct obj *ip)
{
	if (op == NULL || ip == NULL || link_exists(d, op->id, ip->id))
		return;

	struct pw_properties *p = pw_properties_new(NULL, NULL);
	pw_properties_setf(p, PW_KEY_LINK_OUTPUT_NODE, "%u", op->node_id);
	pw_properties_setf(p, PW_KEY_LINK_OUTPUT_PORT, "%u", op->id);
	pw_properties_setf(p, PW_KEY_LINK_INPUT_NODE, "%u", ip->node_id);
	pw_properties_setf(p, PW_KEY_LINK_INPUT_PORT, "%u", ip->id);
	struct pw_proxy *proxy = pw_core_create_object(d->core, "link-factory",
			PW_TYPE_INTERFACE_Link, PW_VERSION_LINK, &p->dict, 0);
	pw_properties_free(p);
	if (proxy == NULL)
		return;

	struct link_req *r = calloc(1, sizeof(*r));
	r->proxy = proxy;
	r->out_port = op->id;
	r->in_port = ip->id;
	r->d = d;
	spa_list_append(&d->links, &r->link);
	pw_proxy_add_listener(proxy, &r->listener, &link_proxy_events, r);
}

/* The playback sink feeds the interface's playback channels 1/2 directly. */
static void relink(struct data *d)
{
	struct obj *sink = node_by_prefix(d, d->sink_out_name, 1);
	const char *prefix = d->be->out_node ? d->be->out_node(d->hw) : d->be->out_node_prefix;
	const char *card = d->be->out_node_card;
	struct obj *dout = card ? node_by_card(d, prefix ? prefix : card) :
			   prefix ? node_by_prefix(d, prefix, 0) : NULL;
	struct obj *fl = NULL, *fr = NULL, *a0 = NULL, *a1 = NULL;

	if (sink && dout) {
		fl = port_find(d, sink->id, "output_FL", 1);
		fr = port_find(d, sink->id, "output_FR", 1);
		a0 = port_find(d, dout->id, "playback_AUX0", 0);
		a1 = port_find(d, dout->id, "playback_AUX1", 0);
		if (a0 == NULL || a1 == NULL) {   /* a stereo profile instead of Pro Audio */
			a0 = port_find(d, dout->id, "playback_FL", 0);
			a1 = port_find(d, dout->id, "playback_FR", 0);
		}
	}
	if (!d->no_autolink) {
		make_link(d, fl, a0);
		make_link(d, fr, a1);
	}
	d->shm->sink_linked = fl && fr && a0 && a1 && link_exists(d, fl->id, a0->id) &&
			      link_exists(d, fr->id, a1->id);
}

static void registry_global(void *data, uint32_t id, uint32_t permissions,
		const char *type, uint32_t version, const struct spa_dict *props)
{
	struct data *d = data;
	struct obj o = { .id = id };
	const char *v;

	if (props == NULL)
		return;

	if (strcmp(type, PW_TYPE_INTERFACE_Node) == 0) {
		o.type = 1;
		if ((v = spa_dict_lookup(props, PW_KEY_NODE_NAME)) == NULL)
			return;
		snprintf(o.name, sizeof(o.name), "%s", v);
		if ((v = spa_dict_lookup(props, "alsa.card_name")) != NULL ||
		    (v = spa_dict_lookup(props, "api.alsa.card.name")) != NULL)
			snprintf(o.card, sizeof(o.card), "%s", v);
	} else if (strcmp(type, PW_TYPE_INTERFACE_Port) == 0) {
		o.type = 2;
		if ((v = spa_dict_lookup(props, PW_KEY_PORT_NAME)) == NULL)
			return;
		snprintf(o.name, sizeof(o.name), "%s", v);
		if ((v = spa_dict_lookup(props, PW_KEY_NODE_ID)) == NULL)
			return;
		o.node_id = (uint32_t)atoi(v);
		v = spa_dict_lookup(props, PW_KEY_PORT_DIRECTION);
		o.dir_out = v && strcmp(v, "out") == 0;
	} else if (strcmp(type, PW_TYPE_INTERFACE_Link) == 0) {
		o.type = 3;
		if ((v = spa_dict_lookup(props, PW_KEY_LINK_OUTPUT_PORT)) == NULL)
			return;
		o.out_port = (uint32_t)atoi(v);
		if ((v = spa_dict_lookup(props, PW_KEY_LINK_INPUT_PORT)) == NULL)
			return;
		o.in_port = (uint32_t)atoi(v);
	} else {
		return;
	}

	if (d->n_objs >= MAX_OBJ)
		return;
	d->objs[d->n_objs++] = o;
	relink(d);
}

static void registry_global_remove(void *data, uint32_t id)
{
	struct data *d = data;
	struct obj *o = obj_find(d, id);
	if (o == NULL)
		return;
	*o = d->objs[--d->n_objs];
	relink(d);
}

static const struct pw_registry_events registry_events = {
	PW_VERSION_REGISTRY_EVENTS,
	.global = registry_global,
	.global_remove = registry_global_remove,
};

/* ---------- hardware mixer ---------- */

/* The device is retried every second, so one that is lost again right after each open is logged
 * for the first two rounds and then once in HW_LOG_EVERY. */
static bool hw_log(const struct data *d)
{
	return d->hw_fails < 2 || d->hw_fails % HW_LOG_EVERY == 0;
}

static void hw_lost(struct data *d, int err)
{
	if (hw_log(d))
		fprintf(stderr, "openface-mixer: lost the %s mixer: %d%s\n", d->be->name, err,
			d->hw_fails ? " (still failing)" : "");
	d->hw_fails++;
	d->be->close(d->hw);
	d->shm->hw_state = err == -ENODATA ? DFU_STATE_NO_REPLY : DFU_STATE_ERROR;
	d->shm->hw_nodes = 0;
	d->shm->rate = 0;
}

static void *meter_thread(void *userdata)
{
	struct data *d = userdata;
	struct ofm_shm *s = d->shm;

	pthread_mutex_lock(&d->usb_lock);
	while (!d->meter_quit) {
		int r = -1;
		if (d->be->is_open(d->hw) && !d->hw_need_full)
			r = d->be->meters(d->hw, s);
		s->hw_levels = r == 0;

		/* the wait lets go of usb_lock; hw_tick ends it early when a GUI starts to look */
		unsigned int us = d->watched ? d->be->meter_us : d->be->meter_idle_us;
		struct timespec until;
		clock_gettime(CLOCK_MONOTONIC, &until);
		until.tv_sec += us / 1000000;
		until.tv_nsec += us % 1000000 * 1000L;
		if (until.tv_nsec >= 1000000000L) {
			until.tv_sec++;
			until.tv_nsec -= 1000000000L;
		}
		pthread_cond_timedwait(&d->meter_wake, &d->usb_lock, &until);
	}
	pthread_mutex_unlock(&d->usb_lock);
	return NULL;
}

/* A GUI that reads the meters moves its heartbeat with every read. While it does, the meter
 * thread reads the device at the full rate, and it is woken at once when the reading starts. */
static void watch_gui(struct data *d)
{
	if (d->shm->gui_heartbeat != d->gui_heartbeat) {
		d->gui_heartbeat = d->shm->gui_heartbeat;
		d->gui_seen = d->hw_ticks;
		if (!d->watched)
			pthread_cond_signal(&d->meter_wake);
		d->watched = true;
	} else if (d->hw_ticks - d->gui_seen >= HW_WATCH_TICKS) {
		d->watched = false;
	}
}

static void hw_tick_locked(struct data *d)
{
	struct ofm_shm *s = d->shm;
	const struct ofm_backend *be = d->be;
	bool reset = false;
	int r;

	d->hw_ticks++;
	s->heartbeat++;
	watch_gui(d);

	if (!be->is_open(d->hw)) {
		if (d->hw_ticks % HW_RETRY_TICKS != 1)
			return;
		s->hw_state = be->open(d->hw);
		if (be->out_node)
			relink(d);   /* its PipeWire node is known by name once the unit is found */
		if (!be->is_open(d->hw))
			return;
		d->hw_need_full = true;
		d->hw_opened = d->hw_ticks;
		if (hw_log(d))
			fprintf(stderr, "openface-mixer: opened the %s mixer\n", be->name);
	}

	if (d->hw_need_full || d->hw_ticks % HW_STATUS_TICKS == 0) {
		r = be->status(d->hw, s, &reset);
		if (r < 0) {
			hw_lost(d, r);
			return;
		}
		if (d->hw_fails && d->hw_ticks - d->hw_opened >= HW_STABLE_TICKS) {
			if (!hw_log(d))   /* the lines that would say so were left out */
				fprintf(stderr, "openface-mixer: the %s mixer works again\n",
					be->name);
			d->hw_fails = 0;
		}
		if (reset) {
			fprintf(stderr, "openface-mixer: hardware mixer was reset, restoring\n");
			d->hw_need_full = true;
		}
	}

	r = be->sync(d->hw, s, d->hw_need_full);
	if (r < 0 && r != -ENOSPC) {
		hw_lost(d, r);
		return;
	}
	if (d->hw_need_full) {
		d->hw_need_full = false;
		if (hw_log(d))
			fprintf(stderr, "openface-mixer: hardware mixer on, %d routes\n",
				be->routes(d->hw));
	}
	s->hw_state = r == -ENOSPC ? DFU_STATE_NO_NODES :
		      be->answered && !be->answered(d->hw) ? DFU_STATE_NO_REPLY : DFU_STATE_ACTIVE;
	s->hw_nodes = (uint32_t)be->routes(d->hw);
}

/* Runs on the main loop: USB transfers block. */
static void hw_tick(void *userdata, uint64_t expirations)
{
	struct data *d = userdata;
	pthread_mutex_lock(&d->usb_lock);
	hw_tick_locked(d);
	pthread_mutex_unlock(&d->usb_lock);
}

/* ---------- setup ---------- */

static void do_quit(void *userdata, int signal_number)
{
	struct data *d = userdata;
	pw_main_loop_quit(d->loop);
}

int main(int argc, char *argv[])
{
	struct data d = { 0 };
	int no_sink = 0;

	static const struct ofm_backend *const backends[] = {
		&ofm_backend_digiface, &ofm_backend_ff802, &ofm_backend_ff802usb, &ofm_backend_ucx2,
		&ofm_backend_ff800, &ofm_backend_ff400, &ofm_backend_raydat, &ofm_backend_aio,
		&ofm_backend_aiopro, &ofm_backend_aes,
	};
	d.be = &ofm_backend_digiface;
	for (int i = 1; i < argc; i++) {
		const struct ofm_backend *be = NULL;
		if (strcmp(argv[i], "--device") == 0 && i + 1 < argc)
			for (size_t b = 0; b < sizeof(backends) / sizeof(backends[0]); b++)
				if (strcmp(argv[i + 1], backends[b]->key) == 0)
					be = backends[b];
		if (strcmp(argv[i], "--no-sink") == 0)
			no_sink = 1;
		else if (strcmp(argv[i], "--no-autolink") == 0)
			d.no_autolink = 1;
		else if (be != NULL)
			d.be = be, i++;
		else {
			fprintf(stderr, "usage: %s [--device KEY] [--no-sink] "
				"[--no-autolink]\n", argv[0]);
			return 2;
		}
	}

	pw_init(&argc, &argv);
	spa_list_init(&d.links);

	if (shm_setup(&d) < 0)
		return 1;

	d.hw = d.be->create();
	if (d.hw == NULL) {
		fprintf(stderr, "openface-mixer: cannot initialise the %s backend\n", d.be->name);
		return 1;
	}
	if (d.be->attach)
		d.be->attach(d.hw, d.shm);
	d.gui_heartbeat = d.shm->gui_heartbeat;   /* where a GUI that has gone left it */
	pthread_mutex_init(&d.usb_lock, NULL);
	pthread_condattr_t monotonic;
	pthread_condattr_init(&monotonic);
	pthread_condattr_setclock(&monotonic, CLOCK_MONOTONIC);
	pthread_cond_init(&d.meter_wake, &monotonic);

	d.loop = pw_main_loop_new(NULL);
	pw_loop_add_signal(pw_main_loop_get_loop(d.loop), SIGINT, do_quit, &d);
	pw_loop_add_signal(pw_main_loop_get_loop(d.loop), SIGTERM, do_quit, &d);

	d.context = pw_context_new(pw_main_loop_get_loop(d.loop), NULL, 0);
	d.core = pw_context_connect(d.context, NULL, 0);
	if (d.core == NULL) {
		fprintf(stderr, "openface-mixer: cannot connect to PipeWire: %m\n");
		return 1;
	}

	/* one sink per device, so several devices' engines can run side by side */
	char sink_name[80], sink_desc[96], args[1024];
	if (is_digiface(&d)) {
		snprintf(sink_name, sizeof(sink_name), "%s", SINK_NAME);
		snprintf(sink_desc, sizeof(sink_desc), "Openface Mixer Playback");
	} else {
		snprintf(sink_name, sizeof(sink_name), "%s_%s", SINK_NAME, d.be->key);
		snprintf(sink_desc, sizeof(sink_desc), "Openface Mixer Playback (%s)", d.be->name);
	}
	snprintf(d.sink_out_name, sizeof(d.sink_out_name), "%s.out", sink_name);

	if (!no_sink) {
		snprintf(args, sizeof(args),
			"{ node.description = \"%s\""
			"  capture.props = { node.name = \"%s\" media.class = Audio/Sink"
			"                    audio.position = [ FL FR ] priority.session = 100 priority.driver = 100 }"
			"  playback.props = { node.name = \"%s\" node.autoconnect = false"
			"                     node.dont-reconnect = true audio.position = [ FL FR ] } }",
			sink_desc, sink_name, d.sink_out_name);
		d.loopback = pw_context_load_module(d.context, "libpipewire-module-loopback", args, NULL);
		if (d.loopback == NULL)
			fprintf(stderr, "openface-mixer: could not create playback sink: %m\n");
	}

	struct timespec interval = { 0, HW_TICK_MS * 1000000L };
	d.hw_timer = pw_loop_add_timer(pw_main_loop_get_loop(d.loop), hw_tick, &d);
	pw_loop_update_timer(pw_main_loop_get_loop(d.loop), d.hw_timer, &interval, &interval, false);
	d.meter_thread_started = pthread_create(&d.meter_thread, NULL, meter_thread, &d) == 0;

	d.registry = pw_core_get_registry(d.core, PW_VERSION_REGISTRY, 0);
	pw_registry_add_listener(d.registry, &d.registry_listener, &registry_events, &d);

	fprintf(stderr, "openface-mixer: engine running for the %s\n", d.be->name);
	pw_main_loop_run(d.loop);

	/* the hardware mixer keeps running with the last mix, like TotalMix's stored state */
	if (d.meter_thread_started) {
		pthread_mutex_lock(&d.usb_lock);
		d.meter_quit = true;
		pthread_cond_signal(&d.meter_wake);
		pthread_mutex_unlock(&d.usb_lock);
		pthread_join(d.meter_thread, NULL);
	}
	pw_loop_destroy_source(pw_main_loop_get_loop(d.loop), d.hw_timer);
	d.be->destroy(d.hw);
	d.shm->hw_levels = 0;
	d.shm->hw_state = DFU_STATE_NO_DEVICE;

	struct link_req *r, *t;
	spa_list_for_each_safe(r, t, &d.links, link)
		pw_proxy_destroy(r->proxy);
	if (d.loopback)
		pw_impl_module_destroy(d.loopback);
	pw_proxy_destroy((struct pw_proxy *)d.registry);
	pw_core_disconnect(d.core);
	pw_context_destroy(d.context);
	pw_main_loop_destroy(d.loop);
	d.shm->engine_pid = 0;
	munmap(d.shm, sizeof(struct ofm_shm));
	pw_deinit();
	return 0;
}
