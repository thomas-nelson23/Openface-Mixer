/*
 * RME Fireface 802 and UCX II over USB (Class Compliant mode) as engine backends: DSP mixer,
 * settings and meters as SysEx on the second USB MIDI port. See fireface_usb.h for the protocol.
 */
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#include <sound/asound.h>
#include <sound/asequencer.h>

#include "backend.h"
#include "digiface_usb.h"   /* DFU_STATE_* */
#include "fireface_fw.h"
#include "fireface_usb.h"

#define SEQ_DEVICE      "/dev/snd/seq"
#define CONTROL_PORT    1          /* the second MIDI port takes the DSP commands */
#define UNSENT          0xffffffffu
#define DEFAULT_BUDGET  384        /* bytes per tick when the MIDI buffer can't be read */
#define BUDGET_MARGIN   128        /* left free for other writers (PipeWire's MIDI bridge) */
#define LEVELS_TIMEOUT_MS 1000     /* meters count as live while level packets keep coming */
#define WRITE_TIMEOUT_MS 200
#define SEQ_EXT_MASK    0xc0000000u  /* flag bits the kernel keeps in ext.len */
#ifndef SNDRV_SEQ_PORT_CAP_UMP_ENDPOINT
#define SNDRV_SEQ_PORT_CAP_UMP_ENDPOINT (1 << 9)   /* kernel headers before 6.5 */
#endif
#ifndef SNDRV_SEQ_PORT_CAP_INACTIVE
#define SNDRV_SEQ_PORT_CAP_INACTIVE (1 << 8)
#endif

static const uint8_t SYSEX_HEAD[] = { 0xf0, 0x00, 0x20, 0x0d, 0x10 };

const struct ffusb_model ff802usb_model = {
	.card = "Fireface 802 (", .n_in = FF802_N_IN, .n_out = FF802_N_OUT,
	.n_src_in = FF802_PLAY_SRC, .vol_reg = 0x1e00, .vol_step = 0x100,
	.clock_reg = 0x3d20, .rate_reg = 0x3d21, .dump = 0x3f990812,
};
const struct ffusb_model ucx2usb_model = {
	.card = "Fireface UCX II", .n_in = 20, .n_out = 20,
	.n_src_in = 20, .vol_reg = 0x0500, .vol_step = 0x40,
	.clock_reg = 0x3064, .rate_reg = 0x3065, .dump = 0x3e0467cd,
};

/* ---------- encoding (no I/O) ---------- */

uint16_t ff802usb_encode_gain(float lin)
{
	/* oscmix: L * 0x1000 | 0x8000 above -6 dB, L * 0x8000 below (finer steps) */
	if (!(lin > 0.0f))
		return 0;
	if (lin > 0.5f) {
		long v = lround(lin * 4096.0);
		return (uint16_t)(0x8000 | (v > 0x2000 ? 0x2000 : v));
	}
	long v = lround(lin * 32768.0);
	return (uint16_t)(v < 1 ? 1 : v);
}

static void put_word(uint8_t *p, uint32_t w)
{
	for (int i = 0; i < 5; i++)
		p[i] = (uint8_t)((w >> (7 * i)) & 0x7f);
}

static uint32_t get_word(const uint8_t *p)
{
	uint32_t w = 0;
	for (int i = 0; i < 5; i++)
		w |= (uint32_t)(p[i] & 0x7f) << (7 * i);
	return w;
}

void ff802usb_reg_msg(uint32_t word, uint8_t msg[FF802USB_MSG_LEN])
{
	memcpy(msg, SYSEX_HEAD, sizeof(SYSEX_HEAD));
	msg[5] = 0;
	put_word(msg + 6, ff802_parity(word));
	msg[11] = 0xf7;
}

uint32_t ffusb_vol_cmd(const struct ffusb_model *m, int out, int16_t vol)
{
	return (uint32_t)(m->vol_reg + m->vol_step * out) << 16 | (uint16_t)vol;
}

uint32_t ff802usb_rate(unsigned int value)
{
	static const uint32_t rates[] = {
		32000, 44100, 48000, 64000, 88200, 96000, 128000, 176400, 192000,
	};
	return value < sizeof(rates) / sizeof(rates[0]) ? rates[value] : 0;
}

static void levels(const struct ffusb_model *m, int subid, const uint8_t *p, size_t n_words,
		   struct ofm_shm *s)
{
	float *dst;
	int n;

	switch (subid) {
	case 4: dst = s->peak_src; n = m->n_in; break;                 /* inputs, pre FX */
	case 2: dst = s->peak_src + OFM_N_IN; n = m->n_out; break;      /* playback */
	case 5: dst = s->peak_out; n = m->n_out; break;                 /* outputs, post fader */
	default: return;
	}
	for (int i = 0; i < n && 3 * (size_t)i + 2 < n_words; i++) {
		uint32_t peak = get_word(p + 5 * (3 * i + 2));
		float v = (float)((peak >> 4) & 0xffffff) / 8388608.0f;
		raise_peak(&dst[i], v > 1.0f ? 1.0f : v);
	}
}

int ff802usb_handle_sysex(const uint8_t *msg, size_t len, struct ff802usb_info *info,
			  struct ofm_shm *s)
{
	if (len < sizeof(SYSEX_HEAD) + 2 || memcmp(msg, SYSEX_HEAD, sizeof(SYSEX_HEAD)) != 0 ||
	    msg[len - 1] != 0xf7)
		return -1;
	int subid = msg[5];
	const uint8_t *p = msg + 6;
	size_t n_words = (len - 7) / 5;

	if (subid == 0) {
		for (size_t i = 0; i < n_words; i++) {
			uint32_t w = get_word(p + 5 * i);
			unsigned int reg = (w >> 16) & 0x7fff, val = w & 0xffff;
			if (reg == info->m->clock_reg)
				info->clock = (int)val;
			else if (reg == info->m->rate_reg)
				info->rate = (int)val;
		}
	} else if (subid >= 1 && subid <= 5) {
		info->level_packets++;
		levels(info->m, subid, p, n_words, s);
	}
	return subid;
}

void ff802usb_rx_feed(struct ff802usb_rx *rx, const uint8_t *data, size_t len,
		      struct ff802usb_info *info, struct ofm_shm *s)
{
	for (size_t i = 0; i < len; i++) {
		uint8_t b = data[i];
		if (b == 0xf0) {
			rx->in_sysex = 1;
			rx->len = 0;
		} else if (!rx->in_sysex || (b & 0x80 && b != 0xf7 && b < 0xf8)) {
			rx->in_sysex = 0;    /* not ours, or another message cut it short */
			continue;
		} else if (b >= 0xf8) {
			continue;            /* real-time bytes may sit inside SysEx */
		}
		if (rx->len < sizeof(rx->buf))
			rx->buf[rx->len++] = b;
		else
			rx->in_sysex = 0;
		if (b == 0xf7 && rx->in_sysex) {
			ff802usb_handle_sysex(rx->buf, rx->len, info, s);
			rx->in_sysex = 0;
		}
	}
}

long ff802usb_proc_avail(const char *text, int sub)
{
	char head[32];
	snprintf(head, sizeof(head), "\nOutput %d\n", sub);
	const char *p = strstr(text, head);
	if (p == NULL)
		return -1;
	p += strlen(head);
	const char *end = strstr(p, "\nInput ");
	const char *next = strstr(p, "\nOutput ");
	if (next && (!end || next < end))
		end = next;
	const char *a = strstr(p, "Avail");
	if (a == NULL || (end && a > end))
		return -1;     /* the substream isn't open */
	a = strchr(a, ':');
	return a ? strtol(a + 1, NULL, 10) : -1;
}

/* ---------- ALSA sequencer ---------- */

struct ff802usb {
	const struct ffusb_model *m;
	int fd;
	struct snd_seq_addr me, dev;
	int card;
	int sub;                    /* the control port's rawmidi substream */
	char dev_name[64];

	struct ff802usb_rx rx;
	struct ff802usb_info info;
	unsigned int seen_levels;
	struct timespec last_levels;
	unsigned int serial;

	long budget;                /* bytes this sync may still send */
	uint32_t sent_gain[FF802_N_OUT][FF802_N_SRC];   /* encoded, or UNSENT */
	uint32_t sent_vol[FF802_N_OUT];
	uint32_t sent_cmd[OFM_N_DEV_CMD];
	int routes;
};

static int seq_write_sysex(struct ff802usb *f, const uint8_t *msg, size_t len)
{
	uint8_t buf[sizeof(struct snd_seq_event) + 64];
	struct snd_seq_event ev = {
		.type = SNDRV_SEQ_EVENT_SYSEX,
		.flags = SNDRV_SEQ_EVENT_LENGTH_VARIABLE,
		.queue = SNDRV_SEQ_QUEUE_DIRECT,
		.source = f->me,
		.dest = { .client = SNDRV_SEQ_ADDRESS_SUBSCRIBERS, .port = 0 },
	};
	if (len > sizeof(buf) - sizeof(ev))
		return -EINVAL;
	ev.data.ext.len = (unsigned int)len;
	memcpy(buf, &ev, sizeof(ev));
	memcpy(buf + sizeof(ev), msg, len);
	for (;;) {
		ssize_t n = write(f->fd, buf, sizeof(ev) + len);
		if (n >= 0)
			return 0;
		if (errno == EINTR)
			continue;
		if (errno != EAGAIN)
			return -errno;
		struct pollfd p = { .fd = f->fd, .events = POLLOUT };
		if (poll(&p, 1, WRITE_TIMEOUT_MS) <= 0)
			return -ETIMEDOUT;
	}
}

static int write_reg(struct ff802usb *f, uint32_t word)
{
	uint8_t msg[FF802USB_MSG_LEN];
	ff802usb_reg_msg(word, msg);
	int r = seq_write_sysex(f, msg, sizeof(msg));
	if (r == 0)
		f->budget -= FF802USB_MSG_LEN;
	return r;
}

/* Reads every event waiting on the sequencer and handles the SysEx in it. */
static void seq_drain(struct ff802usb *f, struct ofm_shm *s)
{
	static uint8_t buf[16384];
	for (;;) {
		ssize_t n = read(f->fd, buf, sizeof(buf));
		if (n <= 0)
			return;
		size_t off = 0;
		while (off + sizeof(struct snd_seq_event) <= (size_t)n) {
			struct snd_seq_event ev;
			memcpy(&ev, buf + off, sizeof(ev));
			off += sizeof(ev);
			if ((ev.flags & SNDRV_SEQ_EVENT_LENGTH_MASK) != SNDRV_SEQ_EVENT_LENGTH_VARIABLE)
				continue;
			size_t len = ev.data.ext.len & ~SEQ_EXT_MASK;
			if (off + len > (size_t)n)
				return;
			if (ev.type == SNDRV_SEQ_EVENT_SYSEX && ev.source.client == f->dev.client)
				ff802usb_rx_feed(&f->rx, buf + off, len, &f->info, s);
			/* variable data is padded to whole event cells */
			off += (len + sizeof(ev) - 1) / sizeof(ev) * sizeof(ev);
		}
	}
}

/* Finds the interface's sequencer client and the address of its control port. */
static int find_device(struct ff802usb *f)
{
	struct snd_seq_client_info ci;
	memset(&ci, 0, sizeof(ci));
	ci.client = -1;
	while (ioctl(f->fd, SNDRV_SEQ_IOCTL_QUERY_NEXT_CLIENT, &ci) == 0) {
		if (!ofm_card_match(ci.name, f->m->card))
			continue;
		/* its MIDI ports in order; a MIDI 2.0 (UMP) client adds an endpoint port first */
		struct snd_seq_port_info pi;
		memset(&pi, 0, sizeof(pi));
		pi.addr.client = (unsigned char)ci.client;
		pi.addr.port = 255;    /* QUERY_NEXT_PORT starts after this */
		int idx = 0;
		while (ioctl(f->fd, SNDRV_SEQ_IOCTL_QUERY_NEXT_PORT, &pi) == 0) {
			unsigned int need = SNDRV_SEQ_PORT_CAP_READ | SNDRV_SEQ_PORT_CAP_WRITE |
					    SNDRV_SEQ_PORT_CAP_SUBS_READ | SNDRV_SEQ_PORT_CAP_SUBS_WRITE;
			if ((pi.capability & need) != need ||
			    (pi.capability & (SNDRV_SEQ_PORT_CAP_UMP_ENDPOINT |
					      SNDRV_SEQ_PORT_CAP_INACTIVE)))
				continue;
			if (idx++ == CONTROL_PORT) {
				f->dev = pi.addr;
				f->card = ci.card;
				f->sub = CONTROL_PORT;
				snprintf(f->dev_name, sizeof(f->dev_name), "%s", ci.name);
				return 0;
			}
		}
	}
	return -ENODEV;
}

static int subscribe(struct ff802usb *f, struct snd_seq_addr from, struct snd_seq_addr to)
{
	struct snd_seq_port_subscribe sub;
	memset(&sub, 0, sizeof(sub));
	sub.sender = from;
	sub.dest = to;
	return ioctl(f->fd, SNDRV_SEQ_IOCTL_SUBSCRIBE_PORT, &sub) == 0 ? 0 : -errno;
}

/* How many bytes the control port's MIDI output buffer can take now. */
static long output_room(struct ff802usb *f)
{
	char path[64], text[4096];
	if (f->card < 0)
		return DEFAULT_BUDGET;
	snprintf(path, sizeof(path), "/proc/asound/card%d/midi0", f->card);
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return DEFAULT_BUDGET;
	ssize_t n = read(fd, text, sizeof(text) - 1);
	close(fd);
	if (n <= 0)
		return DEFAULT_BUDGET;
	text[n] = '\0';
	long avail = ff802usb_proc_avail(text, f->sub);
	return avail < 0 ? DEFAULT_BUDGET : avail - BUDGET_MARGIN;
}

/* ---------- backend ---------- */

static void forget_sent(struct ff802usb *f)
{
	memset(f->sent_gain, 0xff, sizeof(f->sent_gain));
	memset(f->sent_vol, 0xff, sizeof(f->sent_vol));
	memset(f->sent_cmd, 0, sizeof(f->sent_cmd));
	f->routes = 0;
}

static void *create_model(const struct ffusb_model *m)
{
	struct ff802usb *f = calloc(1, sizeof(*f));
	if (f == NULL)
		return NULL;
	f->m = m;
	f->fd = -1;
	forget_sent(f);
	return f;
}

static void *create(void)
{
	return create_model(&ff802usb_model);
}

static void *create_ucx2(void)
{
	return create_model(&ucx2usb_model);
}

static void close_dev(void *h)
{
	struct ff802usb *f = h;
	if (f->fd >= 0)
		close(f->fd);   /* closing the client drops its port and subscriptions */
	f->fd = -1;
	forget_sent(f);
}

static void destroy(void *h)
{
	close_dev(h);
	free(h);
}

static bool is_open(void *h)
{
	return ((struct ff802usb *)h)->fd >= 0;
}

static int open_dev(void *h)
{
	struct ff802usb *f = h;
	if (f->fd >= 0)
		return DFU_STATE_ACTIVE;

	f->fd = open(SEQ_DEVICE, O_RDWR | O_NONBLOCK | O_CLOEXEC);
	if (f->fd < 0)
		return errno == EACCES || errno == EPERM ? DFU_STATE_NO_ACCESS :
		       errno == ENOENT ? DFU_STATE_NO_DEVICE : DFU_STATE_ERROR;

	int state = DFU_STATE_ERROR, me = -1, r;
	if (find_device(f) < 0) {
		state = DFU_STATE_NO_DEVICE;
		goto fail;
	}
	if (ioctl(f->fd, SNDRV_SEQ_IOCTL_CLIENT_ID, &me) < 0)
		goto fail;

	struct snd_seq_client_info ci;
	memset(&ci, 0, sizeof(ci));
	ci.client = me;
	if (ioctl(f->fd, SNDRV_SEQ_IOCTL_GET_CLIENT_INFO, &ci) == 0) {
		snprintf(ci.name, sizeof(ci.name), "Openface Mixer");
		ioctl(f->fd, SNDRV_SEQ_IOCTL_SET_CLIENT_INFO, &ci);
	}

	/* room for a full register dump between two meter reads */
	struct snd_seq_client_pool pool;
	memset(&pool, 0, sizeof(pool));
	pool.client = me;
	if (ioctl(f->fd, SNDRV_SEQ_IOCTL_GET_CLIENT_POOL, &pool) == 0 && pool.input_pool < 2000) {
		pool.input_pool = 2000;
		ioctl(f->fd, SNDRV_SEQ_IOCTL_SET_CLIENT_POOL, &pool);
	}

	struct snd_seq_port_info pi;
	memset(&pi, 0, sizeof(pi));
	pi.addr.client = (unsigned char)me;
	snprintf(pi.name, sizeof(pi.name), "Openface control");
	pi.capability = SNDRV_SEQ_PORT_CAP_READ | SNDRV_SEQ_PORT_CAP_WRITE |
			SNDRV_SEQ_PORT_CAP_NO_EXPORT;
	pi.type = SNDRV_SEQ_PORT_TYPE_MIDI_GENERIC | SNDRV_SEQ_PORT_TYPE_APPLICATION;
	if (ioctl(f->fd, SNDRV_SEQ_IOCTL_CREATE_PORT, &pi) < 0)
		goto fail;
	f->me = pi.addr;

	/* the device's replies to us, and our commands to the device */
	if ((r = subscribe(f, f->dev, f->me)) < 0 || (r = subscribe(f, f->me, f->dev)) < 0) {
		state = r == -EBUSY ? DFU_STATE_BUSY : r == -EPERM ? DFU_STATE_NO_ACCESS
								  : DFU_STATE_ERROR;
		goto fail;
	}

	memset(&f->rx, 0, sizeof(f->rx));
	f->info = (struct ff802usb_info){ .m = f->m, .clock = -1, .rate = -1 };
	f->seen_levels = 0;
	clock_gettime(CLOCK_MONOTONIC, &f->last_levels);
	forget_sent(f);
	/* ask for every register, for the clock source and sample rate */
	f->budget = FF802USB_MSG_LEN;
	write_reg(f, f->m->dump);
	return DFU_STATE_ACTIVE;

fail:
	close(f->fd);
	f->fd = -1;
	return state;
}

/* Is the interface's sequencer client still there (it goes away when unplugged)? */
static bool device_present(struct ff802usb *f)
{
	struct snd_seq_client_info ci;
	memset(&ci, 0, sizeof(ci));
	ci.client = f->dev.client;
	return ioctl(f->fd, SNDRV_SEQ_IOCTL_GET_CLIENT_INFO, &ci) == 0 &&
	       strcmp(ci.name, f->dev_name) == 0;
}

static int status(void *h, struct ofm_shm *s, bool *reset)
{
	struct ff802usb *f = h;
	if (!device_present(f))
		return -ENODEV;
	s->rate = f->info.rate >= 0 ? ff802usb_rate((unsigned int)f->info.rate) : 0;
	s->dev_status = f->info.rate >= 0 && f->info.clock >= 0
		? FF802USB_STATUS_VALID | (uint32_t)(f->info.rate & 0xff) << 8 |
		  (uint32_t)(f->info.clock & 0xff)
		: 0;
	*reset = false;
	return 0;
}

static int sync_mix(void *h, struct ofm_shm *s, bool full)
{
	struct ff802usb *f = h;
	const struct ffusb_model *m = f->m;
	int r, routes = 0;

	if (full)
		forget_sent(f);
	f->budget = output_room(f);
	bool room = true;

	/* the mix first, it's what is heard; then the channel settings */
	for (int o = 0; o < m->n_out; o++) {
		bool muted = !(s->out_gain[o] > 0.0f);   /* volume only goes down to -65 dB */
		for (int k = 0; k < FF802_PLAY_SRC + m->n_out; k++) {
			if (k == m->n_src_in)
				k = FF802_PLAY_SRC;      /* no sources between the inputs and playback */
			uint32_t g = muted ? 0 : ff802usb_encode_gain(s->gain[o][ff802_shm_source(k)]);
			if (g)
				routes++;
			if (g == f->sent_gain[o][k] || !(room = room && f->budget >= FF802USB_MSG_LEN))
				continue;
			if ((r = write_reg(f, ff802_mixer_cmd(o, k, (uint16_t)g))) < 0)
				return r;
			f->sent_gain[o][k] = g;
		}
		uint32_t v = (uint16_t)ff802_out_vol(s->out_gain[o]);
		if (v != f->sent_vol[o] && (room = room && f->budget >= FF802USB_MSG_LEN)) {
			if ((r = write_reg(f, ffusb_vol_cmd(m, o, (int16_t)v))) < 0)
				return r;
			f->sent_vol[o] = v;
		}
	}
	for (int i = 0; i < OFM_N_DEV_CMD; i++) {
		uint32_t c = s->dev_cmd[i];
		if (c == 0 || c == f->sent_cmd[i] || !(room = room && f->budget >= FF802USB_MSG_LEN))
			continue;
		if ((r = write_reg(f, c)) < 0)
			return r;
		f->sent_cmd[i] = c;
	}
	f->routes = routes;
	return 0;   /* what didn't fit goes out on the next ticks */
}

static int routes(void *h)
{
	return ((struct ff802usb *)h)->routes;
}

static long ms_since(const struct timespec *t)
{
	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	return (now.tv_sec - t->tv_sec) * 1000 + (now.tv_nsec - t->tv_nsec) / 1000000;
}

static int meters(void *h, struct ofm_shm *s)
{
	struct ff802usb *f = h;
	seq_drain(f, s);
	if (f->info.level_packets != f->seen_levels) {
		f->seen_levels = f->info.level_packets;
		clock_gettime(CLOCK_MONOTONIC, &f->last_levels);
	}

	/* keep-alive and the next level request, as TotalMix for iPad and oscmix send them */
	uint8_t req[] = { 0xf0, 0x00, 0x20, 0x0d, 0x10, 0x02, 0xf7 };
	int r = seq_write_sysex(f, req, sizeof(req));
	if (r == 0) {
		f->budget = FF802USB_MSG_LEN;
		r = write_reg(f, (uint32_t)FF802USB_REG_SERIAL << 16 | (f->serial++ & 0xf));
	}
	if (r < 0)
		return r;
	return ms_since(&f->last_levels) < LEVELS_TIMEOUT_MS ? 0 : -ETIMEDOUT;
}

/* snd-usb-audio names the card after the USB product string, "Fireface 802 (serial)" */
#define FFUSB_BACKEND(k, n, card_name, create_fn)	\
	{						\
		.key = k,				\
		.name = n,				\
		.out_node_card = card_name,		\
		.meter_us = 30000,			\
		.create = create_fn,			\
		.destroy = destroy,			\
		.open = open_dev,			\
		.close = close_dev,			\
		.is_open = is_open,			\
		.status = status,			\
		.sync = sync_mix,			\
		.routes = routes,			\
		.meters = meters,			\
	}

const struct ofm_backend ofm_backend_ff802usb =
	FFUSB_BACKEND("ff802usb", "Fireface 802 USB", "Fireface 802 (", create);
const struct ofm_backend ofm_backend_ucx2 =
	FFUSB_BACKEND("ucx2", "Fireface UCX II", "Fireface UCX II", create_ucx2);
