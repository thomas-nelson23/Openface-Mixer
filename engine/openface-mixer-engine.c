/*
 * openface-mixer-engine - software matrix mixer for the RME Digiface USB on PipeWire.
 *
 * Creates a PipeWire filter node "openface_mixer" with
 *   in_1..in_32      <- Digiface hardware inputs  (capture_AUX0..31, Pro Audio profile)
 *   play_1..play_34  <- software playback (the "Openface Mixer Playback" sink feeds play_1/2)
 *   out_1..out_34    -> Digiface hardware outputs (playback_AUX0..33)
 * and mixes every source into every output with a gain matrix that lives in
 * shared memory (see shm_layout.h), written by the GUI. Peak meters go back the same way.
 *
 * In hardware mode (mixer_mode = OFM_MODE_HARDWARE) the matrix is instead loaded into the
 * Digiface's own DSP mixer over USB (digiface_usb.c), so input monitoring has no PipeWire
 * round trip. The filter then only passes play_N through to out_N, i.e. to the Digiface's
 * playback channel N, which the hardware mixer treats as a source like any input.
 *
 * Links to the Digiface are created automatically and re-created on hotplug or
 * profile changes. The engine runs headless (systemd user service) so the mix keeps
 * working when the GUI is closed.
 *
 * Options:
 *   --no-sink       don't create the "Openface Mixer Playback" virtual sink
 *   --no-autolink   don't link to the Digiface (useful for testing with pw-link)
 *   --no-hardware   never touch the Digiface over USB (software mixing only)
 */
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <pipewire/pipewire.h>
#include <pipewire/filter.h>
#include <pipewire/impl.h>
#include <spa/utils/dict.h>

#include "digiface_usb.h"
#include "shm_layout.h"

#define N_IN    OFM_N_IN
#define N_PLAY  OFM_N_PLAY
#define N_SRC   OFM_N_SRC
#define N_OUT   OFM_N_OUT

#define NODE_NAME       "openface_mixer"
#define SINK_NAME       "openface_mixer_playback"
#define SINK_OUT_NAME   "openface_mixer_playback.out"
#define DIGI_OUT_PREFIX "alsa_output.usb-RME_Digiface_USB"
#define DIGI_IN_PREFIX  "alsa_input.usb-RME_Digiface_USB"

#define MATRIX_FILE_MAGIC 0x324d464fu /* "OFM2": mode, gain[][], out_gain[] */

#define HW_TICK_MS       20  /* how often GUI changes are sent to the hardware mixer */
#define HW_STATUS_TICKS  25  /* read device status every 500 ms */
#define HW_RETRY_TICKS   50  /* look for the device every second */

struct port {
	int index;
};

#define MAX_OBJ 2048

struct obj {
	uint32_t id;
	int type; /* 1 node, 2 port, 3 link */
	char name[160];     /* node.name or port.name */
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
	struct pw_filter *filter;
	struct spa_hook filter_listener;
	struct pw_impl_module *loopback;

	struct port *in[N_IN];
	struct port *play[N_PLAY];
	struct port *out[N_OUT];

	struct ofm_shm *shm;
	float cur[N_OUT][N_SRC];

	struct dfu *dfu;
	struct spa_source *hw_timer;
	unsigned int hw_ticks;
	bool hw_enabled;      /* we switched the DSP mixer on */
	bool hw_need_full;    /* rewrite every node, e.g. after the device was reset */
	bool hw_check_once;   /* in software mode, switch off a mixer left on by a previous run */

	int no_autolink;
	struct obj objs[MAX_OBJ];
	int n_objs;
	struct spa_list links;
};

/* ---------- realtime processing ---------- */

static float peak_of(const float *b, uint32_t n)
{
	float p = 0.0f;
	for (uint32_t i = 0; i < n; i++) {
		float a = fabsf(b[i]);
		if (a > p)
			p = a;
	}
	return p;
}

static void on_process(void *userdata, struct spa_io_position *position)
{
	struct data *d = userdata;
	struct ofm_shm *s = d->shm;
	uint32_t n = position->clock.duration;
	const float *src[N_SRC];
	uint32_t in_c = 0, play_c = 0, out_c = 0;
	bool hw = s->mixer_mode == OFM_MODE_HARDWARE;

	for (int i = 0; i < N_IN; i++) {
		src[i] = pw_filter_get_dsp_buffer(d->in[i], n);
		if (src[i]) {
			in_c++;
			float p = peak_of(src[i], n);
			if (p > s->peak_src[i])
				s->peak_src[i] = p;
		}
	}
	for (int i = 0; i < N_PLAY; i++) {
		const float *b = pw_filter_get_dsp_buffer(d->play[i], n);
		src[N_IN + i] = b;
		if (b) {
			play_c++;
			float p = peak_of(b, n);
			if (p > s->peak_src[N_IN + i])
				s->peak_src[N_IN + i] = p;
		}
	}

	for (int o = 0; o < N_OUT; o++) {
		float *dst = pw_filter_get_dsp_buffer(d->out[o], n);
		if (dst == NULL)
			continue;
		out_c++;
		memset(dst, 0, n * sizeof(float));
		for (int k = 0; k < N_SRC; k++) {
			/* hardware mode: the Digiface mixes; just pass play_N to its playback N */
			float t = hw ? (k == N_IN + o ? 1.0f : 0.0f) : s->gain[o][k] * s->out_gain[o];
			float c = d->cur[o][k];
			if (!isfinite(t) || t < 0.0f)
				t = 0.0f;
			if (t == 0.0f && c == 0.0f)
				continue;
			const float *b = src[k];
			if (b == NULL) {
				d->cur[o][k] = t;
				continue;
			}
			if (t == c) {
				for (uint32_t i = 0; i < n; i++)
					dst[i] += t * b[i];
			} else {
				/* linear ramp over one period to avoid zipper noise */
				float step = (t - c) / (float)n;
				for (uint32_t i = 0; i < n; i++) {
					c += step;
					dst[i] += c * b[i];
				}
				d->cur[o][k] = t;
			}
		}
		float p = peak_of(dst, n);
		if (p > s->peak_out[o])
			s->peak_out[o] = p;
	}

	s->in_connected = in_c;
	s->play_connected = play_c;
	s->out_connected = out_c;
	s->rate = position->clock.rate.denom;
	s->quantum = n;
	s->heartbeat++;
}

static const struct pw_filter_events filter_events = {
	PW_VERSION_FILTER_EVENTS,
	.process = on_process,
};

/* ---------- shared memory ---------- */

static void matrix_path(char *buf, size_t len)
{
	const char *xdg = getenv("XDG_CONFIG_HOME");
	if (xdg && *xdg)
		snprintf(buf, len, "%s/openface-mixer/matrix.bin", xdg);
	else
		snprintf(buf, len, "%s/.config/openface-mixer/matrix.bin", getenv("HOME") ? getenv("HOME") : "/tmp");
}

/*
 * matrix.bin is "OFM2", the mixer mode, gain[][] and out_gain[]. Version 1 files hold only a
 * gain matrix with the output masters folded in; they load as software mode at unity masters.
 */
static int load_matrix(struct ofm_shm *s, const char *path)
{
	FILE *f = fopen(path, "rb");
	if (f == NULL)
		return -1;
	uint32_t hdr[2];
	int ret = -1;
	if (fread(hdr, sizeof(hdr), 1, f) == 1 && hdr[0] == MATRIX_FILE_MAGIC) {
		if (fread(s->gain, sizeof(s->gain), 1, f) == 1 &&
		    fread(s->out_gain, sizeof(s->out_gain), 1, f) == 1) {
			s->mixer_mode = hdr[1];
			ret = 0;
		}
	} else {
		rewind(f);
		if (fread(s->gain, sizeof(s->gain), 1, f) == 1) {
			for (int o = 0; o < N_OUT; o++)
				s->out_gain[o] = 1.0f;
			s->mixer_mode = OFM_MODE_SOFTWARE;
			ret = 0;
		}
	}
	fclose(f);
	return ret;
}

static int shm_setup(struct data *d)
{
	char name[64];
	snprintf(name, sizeof(name), "/openface-mixer-%u", (unsigned)getuid());
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
		matrix_path(path, sizeof(path));
		if (load_matrix(s, path) < 0) {
			/* default: transparent, playback n -> output n, on the hardware mixer */
			memset(s->gain, 0, sizeof(s->gain));
			for (int o = 0; o < N_OUT; o++) {
				s->gain[o][N_IN + o] = 1.0f;
				s->out_gain[o] = 1.0f;
			}
			s->mixer_mode = OFM_MODE_HARDWARE;
			fprintf(stderr, "openface-mixer: using default passthrough matrix\n");
		} else {
			fprintf(stderr, "openface-mixer: loaded matrix from %s\n", path);
		}
		s->n_src = N_SRC;
		s->n_out = N_OUT;
		s->version = OFM_SHM_VERSION;
		s->magic = OFM_SHM_MAGIC;
	}
	s->engine_pid = (uint32_t)getpid();
	memcpy(d->cur, s->gain, sizeof(d->cur));
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

static void relink(struct data *d)
{
	struct obj *me = node_by_prefix(d, NODE_NAME, 1);
	if (me == NULL || d->no_autolink)
		return;
	struct obj *dout = node_by_prefix(d, DIGI_OUT_PREFIX, 0);
	struct obj *din = node_by_prefix(d, DIGI_IN_PREFIX, 0);
	struct obj *sink = node_by_prefix(d, SINK_OUT_NAME, 1);
	char a[64], b[64];

	if (dout) {
		for (int i = 0; i < N_OUT; i++) {
			snprintf(a, sizeof(a), "out_%d", i + 1);
			snprintf(b, sizeof(b), "playback_AUX%d", i);
			make_link(d, port_find(d, me->id, a, 1), port_find(d, dout->id, b, 0));
		}
	}
	if (din) {
		for (int i = 0; i < N_IN; i++) {
			snprintf(a, sizeof(a), "capture_AUX%d", i);
			snprintf(b, sizeof(b), "in_%d", i + 1);
			make_link(d, port_find(d, din->id, a, 1), port_find(d, me->id, b, 0));
		}
	}
	if (sink) {
		make_link(d, port_find(d, sink->id, "output_FL", 1), port_find(d, me->id, "play_1", 0));
		make_link(d, port_find(d, sink->id, "output_FR", 1), port_find(d, me->id, "play_2", 0));
	}
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
	if (o.type != 3)
		relink(d);
}

static void registry_global_remove(void *data, uint32_t id)
{
	struct data *d = data;
	struct obj *o = obj_find(d, id);
	if (o == NULL)
		return;
	*o = d->objs[--d->n_objs];
}

static const struct pw_registry_events registry_events = {
	PW_VERSION_REGISTRY_EVENTS,
	.global = registry_global,
	.global_remove = registry_global_remove,
};

/* ---------- hardware mixer ---------- */

static void hw_lost(struct data *d, int err)
{
	fprintf(stderr, "openface-mixer: lost the Digiface mixer: %d\n", err);
	dfu_close(d->dfu);
	d->hw_enabled = false;
	d->shm->hw_state = DFU_STATE_ERROR;
	d->shm->hw_nodes = 0;
}

static void hw_switch_off(struct data *d)
{
	/* back to the driver's default: mixer off (1:1 passthrough), outputs at unity */
	static const float silent[N_OUT][N_SRC];
	float unity[N_OUT];
	for (int o = 0; o < N_OUT; o++)
		unity[o] = 1.0f;
	dfu_sync(d->dfu, silent, unity, true);
	dfu_set_mixer_enabled(d->dfu, false);
	d->hw_enabled = false;
	fprintf(stderr, "openface-mixer: hardware mixer off\n");
}

/* Runs on the main loop, never in the audio thread: USB transfers block. */
static void hw_tick(void *userdata, uint64_t expirations)
{
	struct data *d = userdata;
	struct ofm_shm *s = d->shm;
	bool want = s->mixer_mode == OFM_MODE_HARDWARE;
	uint32_t status[4];
	int r;

	d->hw_ticks++;

	if (!want) {
		if (dfu_is_open(d->dfu) || d->hw_check_once) {
			if (dfu_open(d->dfu) == DFU_STATE_ACTIVE &&
			    dfu_read_status(d->dfu, status) == 0 &&
			    (d->hw_enabled || dfu_status_mixer_enabled(status)))
				hw_switch_off(d);
			dfu_close(d->dfu);
			d->hw_check_once = false;
		}
		s->hw_state = DFU_STATE_OFF;
		s->hw_nodes = 0;
		s->hw_levels = 0;
		return;
	}

	if (!dfu_is_open(d->dfu)) {
		if (d->hw_ticks % HW_RETRY_TICKS != 1 && s->hw_state != DFU_STATE_OFF)
			return;
		s->hw_state = dfu_open(d->dfu);
		if (!dfu_is_open(d->dfu))
			return;
		d->hw_need_full = true;
		fprintf(stderr, "openface-mixer: opened the Digiface mixer interface\n");
	}

	if (d->hw_enabled && d->hw_ticks % HW_STATUS_TICKS == 0) {
		r = dfu_read_status(d->dfu, status);
		if (r < 0) {
			hw_lost(d, r);
			return;
		}
		if (!dfu_status_mixer_enabled(status)) {
			/* the driver re-initialised the device (replug, resume) */
			fprintf(stderr, "openface-mixer: hardware mixer was reset, restoring\n");
			d->hw_need_full = true;
		}
	}

	r = dfu_sync(d->dfu, s->gain, s->out_gain, d->hw_need_full);
	if (r < 0 && r != -ENOSPC) {
		hw_lost(d, r);
		return;
	}
	if (d->hw_need_full) {
		int e = dfu_set_mixer_enabled(d->dfu, true);
		if (e < 0) {
			hw_lost(d, e);
			return;
		}
		d->hw_enabled = true;
		d->hw_need_full = false;
		fprintf(stderr, "openface-mixer: hardware mixer on, %d nodes\n", dfu_nodes_used(d->dfu));
	}
	s->hw_state = r == -ENOSPC ? DFU_STATE_NO_NODES : DFU_STATE_ACTIVE;
	s->hw_nodes = (uint32_t)dfu_nodes_used(d->dfu);
}

/* ---------- setup ---------- */

static struct port *add_port(struct data *d, enum pw_direction dir, const char *fmt, int idx)
{
	char name[32];
	snprintf(name, sizeof(name), fmt, idx + 1);
	struct port *p = pw_filter_add_port(d->filter, dir, PW_FILTER_PORT_FLAG_MAP_BUFFERS,
			sizeof(struct port),
			pw_properties_new(PW_KEY_FORMAT_DSP, "32 bit float mono audio",
					PW_KEY_PORT_NAME, name, NULL),
			NULL, 0);
	if (p)
		p->index = idx;
	return p;
}

static void do_quit(void *userdata, int signal_number)
{
	struct data *d = userdata;
	pw_main_loop_quit(d->loop);
}

int main(int argc, char *argv[])
{
	struct data d = { 0 };
	int no_sink = 0, no_hardware = 0;

	for (int i = 1; i < argc; i++) {
		if (strcmp(argv[i], "--no-sink") == 0)
			no_sink = 1;
		else if (strcmp(argv[i], "--no-autolink") == 0)
			d.no_autolink = 1;
		else if (strcmp(argv[i], "--no-hardware") == 0)
			no_hardware = 1;
		else {
			fprintf(stderr, "usage: %s [--no-sink] [--no-autolink] [--no-hardware]\n", argv[0]);
			return 2;
		}
	}

	pw_init(&argc, &argv);
	spa_list_init(&d.links);

	if (shm_setup(&d) < 0)
		return 1;

	d.loop = pw_main_loop_new(NULL);
	pw_loop_add_signal(pw_main_loop_get_loop(d.loop), SIGINT, do_quit, &d);
	pw_loop_add_signal(pw_main_loop_get_loop(d.loop), SIGTERM, do_quit, &d);

	d.context = pw_context_new(pw_main_loop_get_loop(d.loop), NULL, 0);
	d.core = pw_context_connect(d.context, NULL, 0);
	if (d.core == NULL) {
		fprintf(stderr, "openface-mixer: cannot connect to PipeWire: %m\n");
		return 1;
	}

	d.filter = pw_filter_new(d.core, NODE_NAME,
			pw_properties_new(
				PW_KEY_MEDIA_TYPE, "Audio",
				PW_KEY_MEDIA_CATEGORY, "Filter",
				PW_KEY_MEDIA_ROLE, "DSP",
				PW_KEY_NODE_NAME, NODE_NAME,
				PW_KEY_NODE_DESCRIPTION, "Openface Mixer",
				PW_KEY_NODE_AUTOCONNECT, "false",
				"node.always-process", "true",
				NULL));
	pw_filter_add_listener(d.filter, &d.filter_listener, &filter_events, &d);

	for (int i = 0; i < N_IN; i++)
		d.in[i] = add_port(&d, PW_DIRECTION_INPUT, "in_%d", i);
	for (int i = 0; i < N_PLAY; i++)
		d.play[i] = add_port(&d, PW_DIRECTION_INPUT, "play_%d", i);
	for (int i = 0; i < N_OUT; i++)
		d.out[i] = add_port(&d, PW_DIRECTION_OUTPUT, "out_%d", i);

	if (pw_filter_connect(d.filter, PW_FILTER_FLAG_RT_PROCESS, NULL, 0) < 0) {
		fprintf(stderr, "openface-mixer: cannot connect filter\n");
		return 1;
	}

	if (!no_sink) {
		d.loopback = pw_context_load_module(d.context, "libpipewire-module-loopback",
			"{ node.description = \"Openface Mixer Playback\""
			"  capture.props = { node.name = \"" SINK_NAME "\" media.class = Audio/Sink"
			"                    audio.position = [ FL FR ] priority.session = 100 priority.driver = 100 }"
			"  playback.props = { node.name = \"" SINK_OUT_NAME "\" node.autoconnect = false"
			"                     node.dont-reconnect = true audio.position = [ FL FR ] } }",
			NULL);
		if (d.loopback == NULL)
			fprintf(stderr, "openface-mixer: could not create playback sink: %m\n");
	}

	if (!no_hardware && (d.dfu = dfu_new()) != NULL) {
		struct timespec interval = { 0, HW_TICK_MS * 1000000L };
		d.hw_check_once = true;
		d.hw_timer = pw_loop_add_timer(pw_main_loop_get_loop(d.loop), hw_tick, &d);
		pw_loop_update_timer(pw_main_loop_get_loop(d.loop), d.hw_timer, &interval, &interval, false);
	} else if (!no_hardware) {
		fprintf(stderr, "openface-mixer: libusb unavailable, hardware mixer disabled\n");
	}

	d.registry = pw_core_get_registry(d.core, PW_VERSION_REGISTRY, 0);
	pw_registry_add_listener(d.registry, &d.registry_listener, &registry_events, &d);

	fprintf(stderr, "openface-mixer: engine running\n");
	pw_main_loop_run(d.loop);

	/* the hardware mixer keeps running with the last mix, like TotalMix's stored state */
	if (d.hw_timer)
		pw_loop_destroy_source(pw_main_loop_get_loop(d.loop), d.hw_timer);
	dfu_free(d.dfu);

	struct link_req *r, *t;
	spa_list_for_each_safe(r, t, &d.links, link)
		pw_proxy_destroy(r->proxy);
	if (d.loopback)
		pw_impl_module_destroy(d.loopback);
	pw_filter_destroy(d.filter);
	pw_proxy_destroy((struct pw_proxy *)d.registry);
	pw_core_disconnect(d.core);
	pw_context_destroy(d.context);
	pw_main_loop_destroy(d.loop);
	d.shm->engine_pid = 0;
	munmap(d.shm, sizeof(struct ofm_shm));
	pw_deinit();
	return 0;
}
