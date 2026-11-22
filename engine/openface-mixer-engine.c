/*
 * openface-mixer-engine - keeps an RME interface's DSP mixer in line with the GUI's mix.
 *
 * The mix (a gain matrix plus output faders, and device settings) lives in shared memory (see
 * shm_layout.h), written by the GUI. A 20 ms main-loop timer loads it into the interface's own
 * DSP mixer, sending only what changed, so monitoring happens inside the interface with no
 * added latency, as with TotalMix. The device's level meters come back the same way, read at
 * full rate only while a GUI looks at them (it counts up gui_heartbeat as it reads). The
 * device is a backend (backend.h): the Digiface USB over USB (digiface_usb.c), the
 * Fireface 802, 800 or 400 over FireWire (fireface_fw.c), the Fireface 802 or UCX II over USB
 * in Class Compliant mode (fireface_usb.c), or an HDSPe RayDAT, AIO, AIO Pro or AES PCIe card
 * through its kernel driver (hdspe.c).
 *
 * The engine also makes the desktop's sinks and sources on the interface's channels (sinks.conf,
 * written by the GUI; without one the "Openface Mixer Playback" sink on playback 1/2) and links
 * them to the interface's PipeWire nodes, re-linking on hotplug or profile changes. The hardware
 * mixer does not need PipeWire: without it the engine runs all the same and connects once it
 * is there, and after PipeWire restarts it connects again in place, so the interface is not
 * sent the mix again. It runs headless (systemd user service), one engine per device (a lock
 * on the shared memory file), and the interface keeps mixing with the last mix when it exits.
 * It exits with 3 when another engine has the device.
 *
 * Options:
 *   --device KEY    digiface (default), ff802, ff802usb, ucx2, ff800, ff400, raydat, aio,
 *                   aiopro or aes
 *   --no-sink       don't create the desktop sinks and sources
 *   --no-autolink   don't link them to the interface (useful for testing with pw-link)
 */
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
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
#define NODES_FILE      "sinks.conf"   /* the desktop sinks and sources, see nodes_parse() */
#define MAX_NODES       32

#define HW_TICK_MS       20  /* how often GUI changes are sent to the hardware mixer */
#define HW_STATUS_TICKS  25  /* read device status every 500 ms */
#define HW_RETRY_TICKS   50  /* look for the device every second */
#define HW_LOG_EVERY     60  /* a device that keeps failing is logged once in this many rounds */
#define HW_STABLE_TICKS  500 /* and counts as working again once it has stayed open for 10 s */
#define HW_WATCH_TICKS   25  /* the meters count as watched for 500 ms after a GUI last read them */

#define LOCK_TRIES       20  /* an engine on its way out gets a second to let go of the device */
#define EXIT_RUNNING     3   /* another engine has the device; the service units tell systemd
                              * not to try again (RestartPreventExitStatus) */

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

/* A desktop sink or source: a loopback with one end the desktop sees (node.name, an Audio/Sink
 * or Audio/Source) and one the engine links to the interface's own PipeWire node, on these of
 * its PCM channels (playback_AUXn for a sink, capture_AUXn for a source). */
struct pw_node_cfg {
	bool source;
	char name[64];
	char desc[96];          /* node.description: what the desktop's sound settings show */
	int8_t chan[3][2];      /* left and right PCM channel at 1x, 2x and 4x speed; -1: none
	                         * (right of a mono source; both where the interface lacks it) */
};

struct pw_node {
	struct pw_node_cfg cfg;
	struct pw_impl_module *module;   /* the loopback, while it is there */
	struct spa_hook module_listener;
	bool linked;
};

struct link_req {
	struct spa_list link;
	struct pw_proxy *proxy;
	struct spa_hook listener;
	uint32_t out_port, in_port;
	struct data *d;
};

struct data;

/* What the engine asks of PipeWire: connect and disconnect, make and remove a sink or source.
 * tests/test_engine_loop.c puts stand-ins here. */
struct pw_ops {
	int (*up)(struct data *d);
	void (*down)(struct data *d);
	void (*load)(struct data *d, struct pw_node *n);
	void (*unload)(struct data *d, struct pw_node *n);
};

struct data {
	struct pw_main_loop *loop;
	struct pw_context *context;
	const struct pw_ops *pw;
	struct pw_core *core;   /* NULL while not connected to PipeWire */
	struct spa_hook core_listener;
	bool pw_lost;         /* the connection to PipeWire is gone: dropped at the next tick */
	bool pw_said;         /* that PipeWire is not there was logged */
	unsigned int pw_tried;  /* hw_ticks at the last try to connect */
	struct pw_registry *registry;
	struct spa_hook registry_listener;
	struct pw_node *nodes[MAX_NODES];
	int n_nodes;
	int speed;            /* 0, 1, 2: single, double or quad speed, as last reported */
	uint32_t pw_reload;   /* the GUI's pw_reload as last seen */
	bool no_sink;

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
	struct obj objs[MAX_OBJ];
	int n_objs;
	bool objs_full;       /* said once */
	struct spa_list links;
};

/* ---------- shared memory ---------- */

static bool is_digiface(const struct data *d)
{
	return d->be == &ofm_backend_digiface;
}

/* The Digiface's files sit in the config directory itself, other devices' in a subdirectory. */
static void config_path(const struct data *d, const char *file, char *buf, size_t len)
{
	const char *xdg = getenv("XDG_CONFIG_HOME");
	const char *sub = is_digiface(d) ? "" : d->be->key;
	const char *sep = is_digiface(d) ? "" : "/";
	if (xdg && *xdg)
		snprintf(buf, len, "%s/openface-mixer/%s%s%s", xdg, sub, sep, file);
	else
		snprintf(buf, len, "%s/.config/openface-mixer/%s%s%s",
			 getenv("HOME") ? getenv("HOME") : "/tmp", sub, sep, file);
}

/* Returns 0, -EBUSY if another engine runs for the device, or another error < 0. */
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
	/* One engine per device: a lock on the segment, held through the descriptor for as long
	 * as this process lives. The kernel drops it however the process ends, so an engine
	 * that was killed leaves nothing behind. */
	for (int tries = LOCK_TRIES; flock(fd, LOCK_EX | LOCK_NB) < 0;) {
		bool busy = errno == EWOULDBLOCK;
		if (busy && --tries > 0) {
			usleep(50000);
			continue;
		}
		if (busy)
			fprintf(stderr, "openface-mixer: an engine for the %s is already running; "
				"this one stops\n", d->be->name);
		else
			perror("flock");
		close(fd);
		return busy ? -EBUSY : -1;
	}
	if (ftruncate(fd, sizeof(struct ofm_shm)) < 0) {
		perror("ftruncate");
		close(fd);
		return -1;
	}
	d->shm = mmap(NULL, sizeof(struct ofm_shm), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (d->shm == MAP_FAILED) {
		perror("mmap");
		close(fd);
		return -1;
	}

	struct ofm_shm *s = d->shm;
	if (s->magic != OFM_SHM_MAGIC || s->version != OFM_SHM_VERSION) {
		memset(s, 0, sizeof(*s));
		char path[512];
		config_path(d, "matrix.bin", path, sizeof(path));
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

/* kind: "alsa_output." for the interface's playback node, "alsa_input." for its capture node */
static struct obj *node_by_card(struct data *d, const char *kind, const char *prefix)
{
	for (int i = 0; i < d->n_objs; i++) {
		struct obj *o = &d->objs[i];
		if (o->type == 1 && strncmp(o->name, kind, strlen(kind)) == 0 &&
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

/* The interface's playback node (source false) or capture node (source true), if PipeWire has
 * it: found by the node name's prefix ("alsa_output.usb-RME_Digiface_USB", capture
 * "alsa_input.usb-RME_Digiface_USB") or by ALSA card name. */
static struct obj *device_node(struct data *d, bool source)
{
	const char *prefix = d->be->out_node ? d->be->out_node(d->hw) : d->be->out_node_prefix;
	const char *card = d->be->out_node_card;
	const char *kind = source ? "alsa_input." : "alsa_output.";
	char in_prefix[160];

	if (card)
		return node_by_card(d, kind, prefix ? prefix : card);
	if (prefix == NULL)
		return NULL;
	if (source && strncmp(prefix, "alsa_output.", 12) == 0) {
		snprintf(in_prefix, sizeof(in_prefix), "alsa_input.%s", prefix + 12);
		prefix = in_prefix;
	}
	return node_by_prefix(d, prefix, 0);
}

/* The interface's port for PCM channel ch: playback_AUXn, which a sink feeds, or capture_AUXn,
 * which a source records. In a stereo profile instead of Pro Audio, channels 1 and 2 are only
 * there as FL and FR (stereo true). */
static struct obj *device_port(struct data *d, struct obj *dev, bool source, int ch, bool stereo)
{
	char name[32];
	if (stereo)
		snprintf(name, sizeof(name), "%s_%s", source ? "capture" : "playback",
			 ch ? "FR" : "FL");
	else
		snprintf(name, sizeof(name), "%s_AUX%d", source ? "capture" : "playback", ch);
	return port_find(d, dev->id, name, source);
}

/* The ports a sink or source is linked through, per channel: from[i] (an output port) to
 * to[i]. Returns its channel count at the current speed, or 0 while a node or port is missing. */
static int node_ports(struct data *d, const struct pw_node *n, struct obj *from[2],
		      struct obj *to[2])
{
	static const char *const pos[2][2] = { { "MONO", NULL }, { "FL", "FR" } };
	const struct pw_node_cfg *c = &n->cfg;
	const int8_t *chan = c->chan[d->speed];
	int chans = chan[1] < 0 ? 1 : 2;
	struct obj *dp[2];
	char name[80];

	snprintf(name, sizeof(name), "%s.%s", c->name, c->source ? "in" : "out");
	struct obj *loop = node_by_prefix(d, name, 1), *dev = device_node(d, c->source);
	if (loop == NULL || dev == NULL || chan[0] < 0)
		return 0;
	for (int i = 0; i < chans; i++)
		dp[i] = device_port(d, dev, c->source, chan[i], false);
	if (chan[0] < 2 && chan[chans - 1] < 2 && (dp[0] == NULL || dp[chans - 1] == NULL))
		for (int i = 0; i < chans; i++)
			dp[i] = device_port(d, dev, c->source, chan[i], true);
	for (int i = 0; i < chans; i++) {
		char lp_name[16];
		snprintf(lp_name, sizeof(lp_name), "%s_%s", c->source ? "input" : "output",
			 pos[chans - 1][i]);
		struct obj *lp = port_find(d, loop->id, lp_name, !c->source);
		if (lp == NULL || dp[i] == NULL)
			return 0;
		from[i] = c->source ? dp[i] : lp;
		to[i] = c->source ? lp : dp[i];
	}
	return chans;
}

/* Every sink and source feeds (or records) the interface's channels directly. */
static void relink(struct data *d)
{
	int made = 0, linked = 0;

	for (int i = 0; i < d->n_nodes; i++) {
		struct pw_node *n = d->nodes[i];
		struct obj *from[2], *to[2];
		if (n->module == NULL)
			continue;
		made++;
		int chans = node_ports(d, n, from, to);
		for (int c = 0; c < chans && !d->no_autolink && d->core; c++)
			make_link(d, from[c], to[c]);
		n->linked = chans > 0;
		for (int c = 0; c < chans; c++)
			n->linked = n->linked && link_exists(d, from[c]->id, to[c]->id);
		linked += n->linked;
	}
	d->shm->pw_nodes = (uint32_t)made;
	d->shm->pw_linked = (uint32_t)linked;
	d->shm->sink_linked = made > 0 && linked == made;
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

	if (d->n_objs >= MAX_OBJ) {
		if (!d->objs_full)
			fprintf(stderr, "openface-mixer: PipeWire has more than %d nodes, ports and "
				"links; the engine does not see the rest, so the playback sink "
				"may stay unlinked\n", MAX_OBJ);
		d->objs_full = true;
		return;
	}
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

/* ---------- desktop sinks and sources ---------- */

static bool node_name_ok(const char *s)
{
	for (; *s; s++)
		if (!isalnum((unsigned char)*s) && *s != '_' && *s != '.' && *s != '-')
			return false;
	return true;
}

/* Text that can go between the quotes of the module's arguments: no quote, backslash or
 * control character. */
static bool node_desc_ok(const char *s)
{
	if (*s == '\0')
		return false;
	for (; *s; s++)
		if ((unsigned char)*s < 0x20 || *s == 0x7f || *s == '"' || *s == '\\')
			return false;
	return true;
}

/* sinks.conf, which the GUI writes: one line per sink or source,
 *	sink|source NAME L1 R1 L2 R2 L4 R4 DESCRIPTION
 * NAME the node name, Ln and Rn the left and right PCM channel at speed n (see pw_node_cfg),
 * DESCRIPTION the rest of the line. Empty lines and lines starting with # are skipped. Returns
 * how many there are, or -1 if the text is not such a file. */
static int nodes_parse(char *text, struct pw_node_cfg *cfg, int max)
{
	int n = 0;
	char *save = NULL;

	memset(cfg, 0, sizeof(*cfg) * max);
	for (char *line = strtok_r(text, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
		char kind[8], *desc;
		int ch[6], off = -1;
		size_t len;

		if (line[0] == '#' || line[strspn(line, " \t\r")] == '\0')
			continue;
		if (n == max)
			return -1;
		struct pw_node_cfg *c = &cfg[n++];
		if (sscanf(line, "%7s %63s %d %d %d %d %d %d %n", kind, c->name, &ch[0], &ch[1],
			   &ch[2], &ch[3], &ch[4], &ch[5], &off) != 8 || off < 0)
			return -1;
		desc = line + off;
		len = strlen(desc);
		while (len && (desc[len - 1] == '\r' || desc[len - 1] == ' '))
			desc[--len] = '\0';
		c->source = strcmp(kind, "source") == 0;
		if ((!c->source && strcmp(kind, "sink") != 0) || !node_name_ok(c->name) ||
		    !node_desc_ok(desc) || len >= sizeof(c->desc))
			return -1;
		memcpy(c->desc, desc, len + 1);
		for (int m = 0; m < 3; m++) {
			int l = ch[2 * m], r = ch[2 * m + 1];
			if (l < -1 || l > 63 || r < -1 || r > 63 || (l < 0 && r >= 0) ||
			    (!c->source && (l < 0) != (r < 0)))
				return -1;
			c->chan[m][0] = (int8_t)l;
			c->chan[m][1] = (int8_t)r;
		}
	}
	return n;
}

/* Without a sinks.conf (the GUI has not written one yet), or with one that is damaged: the
 * playback sink on PCM channels 1/2, as before the list. */
static int nodes_default(const struct data *d, struct pw_node_cfg *cfg)
{
	memset(cfg, 0, sizeof(*cfg));
	if (is_digiface(d)) {
		snprintf(cfg->name, sizeof(cfg->name), "%s", SINK_NAME);
		snprintf(cfg->desc, sizeof(cfg->desc), "Openface Mixer Playback");
	} else {
		snprintf(cfg->name, sizeof(cfg->name), "%s_%s", SINK_NAME, d->be->key);
		snprintf(cfg->desc, sizeof(cfg->desc), "Openface Mixer Playback (%s)", d->be->name);
	}
	for (int m = 0; m < 3; m++)
		cfg->chan[m][0] = 0, cfg->chan[m][1] = 1;
	return 1;
}

/* What the sinks and sources should be now: sinks.conf, or the default. */
static int nodes_read(const struct data *d, struct pw_node_cfg *cfg)
{
	char path[512], text[16384];
	config_path(d, NODES_FILE, path, sizeof(path));
	FILE *f = fopen(path, "r");
	if (f == NULL) {
		if (errno != ENOENT)
			fprintf(stderr, "openface-mixer: cannot read %s: %m\n", path);
		return nodes_default(d, cfg);
	}
	size_t len = fread(text, 1, sizeof(text), f);
	bool whole = feof(f) && !ferror(f);
	fclose(f);
	int n = -1;
	if (whole && len < sizeof(text)) {
		text[len] = '\0';
		n = nodes_parse(text, cfg, MAX_NODES);
	}
	if (n < 0) {
		fprintf(stderr, "openface-mixer: %s is damaged; ignoring it and making the playback "
			"sink only\n", path);
		return nodes_default(d, cfg);
	}
	return n;
}

static bool node_wanted(const struct data *d, const struct pw_node *n)
{
	return d->core && !d->pw_lost && !d->no_sink && n->cfg.chan[d->speed][0] >= 0;
}

/* Makes the sinks and sources that should be there (connected to PipeWire, on channels the
 * interface has at this speed) and removes the others. */
static void nodes_apply(struct data *d)
{
	for (int i = 0; i < d->n_nodes; i++) {
		struct pw_node *n = d->nodes[i];
		if (node_wanted(d, n) && n->module == NULL)
			d->pw->load(d, n);
		else if (!node_wanted(d, n) && n->module)
			d->pw->unload(d, n);
	}
}

/* Reads the list again (the GUI changed it). Sinks and sources that are still the same keep
 * their loopback, so the programs playing to them carry on. */
static void nodes_load(struct data *d)
{
	struct pw_node_cfg cfg[MAX_NODES];
	struct pw_node *now[MAX_NODES] = { NULL };
	int n = nodes_read(d, cfg);

	for (int i = 0; i < n; i++) {
		for (int j = 0; j < d->n_nodes && now[i] == NULL; j++)
			if (d->nodes[j] && memcmp(&d->nodes[j]->cfg, &cfg[i], sizeof(cfg[i])) == 0)
				now[i] = d->nodes[j], d->nodes[j] = NULL;
		if (now[i] == NULL && (now[i] = calloc(1, sizeof(*now[i]))) != NULL)
			now[i]->cfg = cfg[i];
	}
	for (int j = 0; j < d->n_nodes; j++) {
		if (d->nodes[j] && d->nodes[j]->module)
			d->pw->unload(d, d->nodes[j]);
		free(d->nodes[j]);
	}
	d->n_nodes = 0;
	for (int i = 0; i < n; i++)
		if (now[i])
			d->nodes[d->n_nodes++] = now[i];
}

static void nodes_free(struct data *d)
{
	for (int i = 0; i < d->n_nodes; i++)
		free(d->nodes[i]);
	d->n_nodes = 0;
}

static void node_destroyed(void *data)
{
	struct pw_node *n = data;
	spa_hook_remove(&n->module_listener);
	n->module = NULL;
	n->linked = false;
}

static const struct pw_impl_module_events node_module_events = {
	PW_VERSION_IMPL_MODULE_EVENTS,
	.destroy = node_destroyed,
};

/* The loopback's arguments. The first sink in the list gets the priorities the playback sink
 * always had, so the session manager picks it as the default output; the others don't. */
static void node_args(const struct data *d, const struct pw_node *n, char *args, size_t len)
{
	const struct pw_node_cfg *c = &n->cfg;
	const char *pos = c->chan[d->speed][1] < 0 ? "MONO" : "FL FR";
	bool first = true;

	for (int i = 0; i < d->n_nodes && d->nodes[i] != n; i++)
		first = first && d->nodes[i]->cfg.source;
	if (c->source)
		snprintf(args, len,
			"{ node.description = \"%s\""
			"  capture.props = { node.name = \"%s.in\" node.autoconnect = false"
			"                    node.dont-reconnect = true audio.position = [ %s ] }"
			"  playback.props = { node.name = \"%s\" media.class = Audio/Source"
			"                     audio.position = [ %s ] } }",
			c->desc, c->name, pos, c->name, pos);
	else
		snprintf(args, len,
			"{ node.description = \"%s\""
			"  capture.props = { node.name = \"%s\" media.class = Audio/Sink"
			"                    audio.position = [ FL FR ]%s }"
			"  playback.props = { node.name = \"%s.out\" node.autoconnect = false"
			"                     node.dont-reconnect = true audio.position = [ FL FR ] } }",
			c->desc, c->name, first ? " priority.session = 100 priority.driver = 100" : "",
			c->name);
}

static void node_load(struct data *d, struct pw_node *n)
{
	char args[1024];
	node_args(d, n, args, sizeof(args));
	n->module = pw_context_load_module(d->context, "libpipewire-module-loopback", args, NULL);
	if (n->module == NULL) {
		fprintf(stderr, "openface-mixer: could not create %s: %m\n", n->cfg.desc);
		return;
	}
	pw_impl_module_add_listener(n->module, &n->module_listener, &node_module_events, n);
}

static void node_unload(struct data *d, struct pw_node *n)
{
	pw_impl_module_destroy(n->module);   /* node_destroyed() lets go of it */
}

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

/* ---------- PipeWire ---------- */

static int speed_index(uint32_t rate)
{
	return rate > 100000 ? 2 : rate > 50000 ? 1 : 0;   /* as the GUI's model.speed_mode() */
}

/* PipeWire went away (it crashed or was restarted): the sinks, sources and links went with it.
 * The next tick lets go of the connection and connects again once PipeWire is back. The
 * interface is not told anything and keeps mixing. */
static void core_error(void *userdata, uint32_t id, int seq, int res, const char *message)
{
	struct data *d = userdata;
	if (id != PW_ID_CORE || res != -EPIPE || d->pw_lost)
		return;
	fprintf(stderr, "openface-mixer: lost the connection to PipeWire; the hardware mixer "
		"carries on, and the engine connects again when PipeWire is back\n");
	d->pw_lost = true;
}

static const struct pw_core_events core_events = {
	PW_VERSION_CORE_EVENTS,
	.error = core_error,
};

static int pw_up(struct data *d)
{
	d->core = pw_context_connect(d->context, NULL, 0);
	if (d->core == NULL)
		return errno ? -errno : -EIO;
	pw_core_add_listener(d->core, &d->core_listener, &core_events, d);
	d->registry = pw_core_get_registry(d->core, PW_VERSION_REGISTRY, 0);
	pw_registry_add_listener(d->registry, &d->registry_listener, &registry_events, d);
	return 0;
}

static void pw_down(struct data *d)
{
	struct link_req *r, *t;
	spa_list_for_each_safe(r, t, &d->links, link)
		pw_proxy_destroy(r->proxy);
	pw_proxy_destroy((struct pw_proxy *)d->registry);
	pw_core_disconnect(d->core);
	d->registry = NULL;
	d->core = NULL;
}

static const struct pw_ops pw_real = {
	.up = pw_up,
	.down = pw_down,
	.load = node_load,
	.unload = node_unload,
};

/* Lets go of the connection, with every sink, source and link made through it. */
static void pw_drop(struct data *d)
{
	d->pw_lost = true;
	nodes_apply(d);
	d->pw->down(d);
	d->pw_lost = false;
	d->n_objs = 0;
	d->objs_full = false;
	d->shm->pw_connected = 0;
	relink(d);
}

static void pw_connect(struct data *d)
{
	d->pw_tried = d->hw_ticks;
	if (d->pw->up(d) < 0) {
		if (!d->pw_said)
			fprintf(stderr, "openface-mixer: PipeWire is not running; the hardware mixer "
				"runs without it, and the sinks and sources come once it starts\n");
		d->pw_said = true;
		return;
	}
	fprintf(stderr, "openface-mixer: connected to PipeWire\n");
	d->pw_said = false;
	d->shm->pw_connected = 1;
	nodes_apply(d);
	relink(d);
}

/* Runs after every hardware tick: connects to PipeWire while it isn't (once a second), and
 * follows the GUI's list and the interface's speed. */
static void pw_tick(struct data *d)
{
	struct ofm_shm *s = d->shm;

	if (d->pw_lost) {
		pw_drop(d);
		d->pw_tried = d->hw_ticks;   /* the next try in a second: PipeWire takes a moment */
	} else if (d->core == NULL && d->hw_ticks - d->pw_tried >= HW_RETRY_TICKS)
		pw_connect(d);
	if (s->pw_reload != d->pw_reload) {
		d->pw_reload = s->pw_reload;
		nodes_load(d);
		nodes_apply(d);
		relink(d);
	}
	if (s->rate && speed_index(s->rate) != d->speed) {
		d->speed = speed_index(s->rate);
		nodes_apply(d);
		relink(d);
	}
}

/* Runs on the main loop: USB transfers block. */
static void hw_tick(void *userdata, uint64_t expirations)
{
	struct data *d = userdata;
	pthread_mutex_lock(&d->usb_lock);
	hw_tick_locked(d);
	pthread_mutex_unlock(&d->usb_lock);
	pw_tick(d);
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
			d.no_sink = true;
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
	d.pw = &pw_real;

	int shm = shm_setup(&d);
	if (shm < 0)
		return shm == -EBUSY ? EXIT_RUNNING : 1;

	/* The signals go to the main loop. Set up before any thread exists (libusb starts one in
	 * create), or SIGTERM would end the process through that thread with nothing cleaned up. */
	d.loop = pw_main_loop_new(NULL);
	pw_loop_add_signal(pw_main_loop_get_loop(d.loop), SIGINT, do_quit, &d);
	pw_loop_add_signal(pw_main_loop_get_loop(d.loop), SIGTERM, do_quit, &d);

	d.hw = d.be->create();
	if (d.hw == NULL) {
		fprintf(stderr, "openface-mixer: cannot initialise the %s backend\n", d.be->name);
		return 1;
	}
	if (d.be->attach)
		d.be->attach(d.hw, d.shm);
	d.gui_heartbeat = d.shm->gui_heartbeat;   /* where a GUI that has gone left it */
	d.pw_reload = d.shm->pw_reload;
	d.speed = speed_index(d.shm->rate);
	d.shm->pw_connected = 0;
	pthread_mutex_init(&d.usb_lock, NULL);
	pthread_condattr_t monotonic;
	pthread_condattr_init(&monotonic);
	pthread_condattr_setclock(&monotonic, CLOCK_MONOTONIC);
	pthread_cond_init(&d.meter_wake, &monotonic);

	/* The hardware mixer comes first and needs no PipeWire: the sinks and sources follow
	 * whenever PipeWire is there (pw_tick) */
	d.context = pw_context_new(pw_main_loop_get_loop(d.loop), NULL, 0);
	nodes_load(&d);
	relink(&d);
	pw_connect(&d);

	struct timespec interval = { 0, HW_TICK_MS * 1000000L };
	d.hw_timer = pw_loop_add_timer(pw_main_loop_get_loop(d.loop), hw_tick, &d);
	pw_loop_update_timer(pw_main_loop_get_loop(d.loop), d.hw_timer, &interval, &interval, false);
	d.meter_thread_started = pthread_create(&d.meter_thread, NULL, meter_thread, &d) == 0;

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
	d.shm->engine_pid = 0;

	if (d.core)
		pw_drop(&d);
	nodes_free(&d);
	pw_context_destroy(d.context);
	pw_main_loop_destroy(d.loop);
	munmap(d.shm, sizeof(struct ofm_shm));
	pw_deinit();
	return 0;
}
