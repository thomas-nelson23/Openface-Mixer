/* Unit test for the Digiface USB backend (no device needed). Run via `make test`.
 *
 * Besides the encoders, it checks what the backend sends: the test is linked without libusb and
 * has the few libusb functions the backend calls itself, in front of a model of the Digiface's
 * mixer that records every transfer. With OFM_RECORD=file the transfers are also written to
 * that file, one per line, to compare two versions of the backend byte for byte. */
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libusb.h>

#include "../engine/backend.h"
#include "../engine/digiface_usb.h"

static int failed;

static void check(float lin, unsigned int want)
{
	unsigned int got = dfu_encode_gain(lin);
	if (got != want) {
		fprintf(stderr, "dfu_encode_gain(%g) = 0x%04x, want 0x%04x\n", lin, got, want);
		failed = 1;
	}
}

static void put64(uint8_t *frame, int word, uint64_t v)
{
	for (int i = 0; i < 8; i++)
		frame[8 * word + i] = (uint8_t)(v >> (8 * i));
}

static void check_close(const char *what, float got, float want)
{
	if (fabsf(got - want) > want * 1e-4f + 1e-9f) {
		fprintf(stderr, "%s = %g, want %g\n", what, got, want);
		failed = 1;
	}
}

/* A playback frame as captured from a Digiface: a -20 dBFS sine on channel 1. */
static void check_meter_frame(void)
{
	uint8_t frame[1024] = { 0 };
	struct dfu_meters m;

	put64(frame, 0, (uint64_t)(0.005 * 0x1p55));       /* mean square of a 0.1 sine */
	put64(frame, 64, 0xccccccull | 0x0199999aull << 32); /* ch 1: 0.1, ch 2: 0.2 */
	for (int w = 100; w < 128; w++)
		put64(frame, w, 0xFFFFFFF1FFFFFFF1ull);
	if (dfu_decode_meter_frame(frame, &m) != DFU_METER_PLAYBACK) {
		fprintf(stderr, "playback frame not recognised\n");
		failed = 1;
		return;
	}
	check_close("peak ch1", m.peak[DFU_METER_PLAYBACK][0], 0.1f);
	check_close("peak ch2", m.peak[DFU_METER_PLAYBACK][1], 0.2f);
	check_close("rms ch1", m.rms[DFU_METER_PLAYBACK][0], 0.0707107f);

	put64(frame, 127, 0);
	if (dfu_decode_meter_frame(frame, &m) != -1) {
		fprintf(stderr, "garbage accepted as a meter frame\n");
		failed = 1;
	}
}

static void check_rate(uint32_t idx, uint32_t want)
{
	uint32_t status[4] = { 0, idx << 20 | 0x000f0000u, 0, 0 };
	uint32_t got;

	/* other bits of halfword 1H must not leak in */
	status[1] = (status[1] & ~0x000f0000u) | 0x000a0000u;
	got = dfu_status_rate(status);
	if (got != want) {
		fprintf(stderr, "dfu_status_rate(index %u) = %u, want %u\n", idx, got, want);
		failed = 1;
	}
}

/* ---------- what the backend sends, to a model of the device behind fake libusb ---------- */

#define N_NODES   DFU_MAX_NODES
#define MAX_CTL   256
#define MAX_WORDS 16384

static struct {
	bool present;              /* on the bus */
	int open_err, claim_err;   /* what libusb_open and libusb_claim_interface answer */
	int transfer_err;          /* and every transfer (0 = works) */
	bool claimed;
	uint16_t reg1, reg2;       /* control registers: the mixer is off after power-up */
	uint16_t fader[DFU_N_OUT];
	struct { bool routed; unsigned int src, dst, gain; } node[N_NODES];
	int hot_routes;            /* nodes that were routed while their gain was not 0 */
	int meter_type;            /* next frame of the level endpoint */
} dev;

static struct { unsigned int req, val, idx; } ctl[MAX_CTL];    /* control writes */
static uint32_t words[MAX_WORDS];                               /* mixer commands */
static int n_ctl, n_words, n_bulk;
static FILE *record;

static void plug_in(void)
{
	memset(&dev, 0, sizeof(dev));
	dev.present = true;
	dev.reg1 = 0x0400;
	dev.reg2 = 0x0100;
}

static void forget(void)
{
	n_ctl = n_words = n_bulk = 0;
}

int libusb_init(libusb_context **ctx)
{
	*ctx = NULL;
	return 0;
}

void libusb_exit(libusb_context *ctx)
{
}

/* two devices on the bus: a hub and, while it is plugged in, the Digiface */
static char hub, digiface;

ssize_t libusb_get_device_list(libusb_context *ctx, libusb_device ***list)
{
	static libusb_device *found[3];
	found[0] = (libusb_device *)&hub;
	found[1] = dev.present ? (libusb_device *)&digiface : NULL;
	*list = found;
	return dev.present ? 2 : 1;
}

void libusb_free_device_list(libusb_device **list, int unref_devices)
{
}

int libusb_get_device_descriptor(libusb_device *d, struct libusb_device_descriptor *desc)
{
	bool is_hub = d == (libusb_device *)&hub;
	memset(desc, 0, sizeof(*desc));
	desc->idVendor = is_hub ? 0x1d6b : 0x2a39;
	desc->idProduct = is_hub ? 0x0002 : 0x3f8c;
	return 0;
}

int libusb_open(libusb_device *d, libusb_device_handle **h)
{
	if (dev.open_err)
		return dev.open_err;
	*h = (libusb_device_handle *)&dev;
	return 0;
}

void libusb_close(libusb_device_handle *h)
{
}

int libusb_claim_interface(libusb_device_handle *h, int iface)
{
	if (dev.claim_err == 0)
		dev.claimed = iface == 1;
	return dev.claim_err;
}

int libusb_release_interface(libusb_device_handle *h, int iface)
{
	dev.claimed = false;
	return 0;
}

int libusb_control_transfer(libusb_device_handle *h, uint8_t type, uint8_t req, uint16_t val,
			    uint16_t idx, unsigned char *data, uint16_t len, unsigned int timeout)
{
	if (dev.transfer_err)
		return dev.transfer_err;
	if (type & LIBUSB_ENDPOINT_IN) {        /* request 17: the status words */
		uint32_t status[4] = { 0, 2u << 20, 0, dev.reg1 | (uint32_t)dev.reg2 << 16 };
		for (int i = 0; i < 16; i++)
			data[i] = (uint8_t)(status[i / 4] >> (8 * (i % 4)));
		return req == 17 && len == 16 ? 16 : LIBUSB_ERROR_PIPE;
	}
	if (record)
		fprintf(record, "ctl %u %04x %04x\n", req, val, idx);
	if (n_ctl < MAX_CTL) {
		ctl[n_ctl].req = req;
		ctl[n_ctl].val = val;
		ctl[n_ctl].idx = idx;
	}
	n_ctl++;
	if (req == 16)
		dev.reg1 = (dev.reg1 & ~idx) | val;
	else if (req == 18)
		dev.reg2 = (dev.reg2 & ~idx) | val;
	else if (req == 21 && idx >= 0x100 && idx < 0x100 + DFU_N_OUT)
		dev.fader[idx - 0x100] = val;
	else
		return LIBUSB_ERROR_PIPE;
	return 0;
}

static void mixer_command(uint32_t w)
{
	unsigned int n = (w >> 16) & 0x7ff;
	if ((w & 0xC000FFFFu) == 0xC000FFFFu) {
		memset(&dev.node[n], 0, sizeof(dev.node[n]));
	} else if (w & 0x40000000u) {
		dev.hot_routes += dev.node[n].gain != 0;
		dev.node[n].routed = true;
		dev.node[n].dst = (w >> 9) & 0x7f;
		dev.node[n].src = w & 0x1ff;
	} else {
		dev.node[n].gain = w & 0xffff;
	}
}

int libusb_bulk_transfer(libusb_device_handle *h, unsigned char ep, unsigned char *data,
			 int len, int *done, unsigned int timeout)
{
	if (dev.transfer_err)
		return dev.transfer_err;
	*done = len;
	if (ep == 0x84) {                       /* the level endpoint: one frame per read */
		memset(data, 0, (size_t)len);
		for (int c = 0; c < DFU_METER_CHANNELS; c += 2)   /* peak: (type + 1) / 8 on ch 1, 3, .. */
			put64(data, 64 + c / 2, (uint64_t)(dev.meter_type + 1) << 24);
		for (int w = 100; w < 128; w++)
			put64(data, w, 0xFFFFFFF0FFFFFFF0ull | (uint64_t)dev.meter_type << 32 |
				       (uint64_t)dev.meter_type);
		dev.meter_type = (dev.meter_type + 1) % 3;
		return len == 1024 ? 0 : LIBUSB_ERROR_OVERFLOW;
	}
	if (ep != 0x0b || len % 4 || len > 4096)
		return LIBUSB_ERROR_PIPE;
	n_bulk++;
	for (int i = 0; i < len; i += 4) {
		uint32_t w = data[i] | (uint32_t)data[i + 1] << 8 | (uint32_t)data[i + 2] << 16 |
			     (uint32_t)data[i + 3] << 24;
		if (record)
			fprintf(record, "cmd %08x\n", w);
		if (n_words < MAX_WORDS)
			words[n_words] = w;
		n_words++;
		mixer_command(w);
	}
	return 0;
}

static void expect(const char *what, long got, long want)
{
	if (got != want) {
		fprintf(stderr, "%s = %ld (0x%lx), want %ld (0x%lx)\n", what, got, got, want, want);
		failed = 1;
	}
}

/* True if the device mixes exactly what s asks for: one node per crosspoint in use, with its
 * gain, nothing else routed, and the output faders. */
static bool mixes(const struct ofm_shm *s)
{
	static unsigned int got[DFU_N_OUT][0x200];
	bool ok = dev.hot_routes == 0;
	memset(got, 0, sizeof(got));
	for (int n = 0; n < N_NODES; n++) {
		if (!dev.node[n].routed || dev.node[n].gain == 0)
			continue;
		if (dev.node[n].dst >= DFU_N_OUT || got[dev.node[n].dst][dev.node[n].src])
			ok = false;         /* no such output, or two nodes for one crosspoint */
		else
			got[dev.node[n].dst][dev.node[n].src] = dev.node[n].gain;
	}
	for (int o = 0; o < DFU_N_OUT; o++) {
		for (int k = 0; k < OFM_N_SRC; k++) {
			bool exists = k < DFU_N_IN || (k >= OFM_N_IN && k < OFM_N_IN + DFU_N_PLAY);
			unsigned int src = k < OFM_N_IN ? (unsigned int)k : 0x100u + (k - OFM_N_IN);
			unsigned int want = exists ? dfu_encode_gain(s->gain[o][k]) : 0;
			ok = ok && got[o][src] == want;
			got[o][src] = 0;
		}
		for (int src = 0; src < 0x200; src++)
			ok = ok && got[o][src] == 0;
		ok = ok && dev.fader[o] == dfu_encode_gain(s->out_gain[o]);
	}
	return ok;
}

/* A mix as the GUI writes it: playback n to output n, one input monitored, also on channels
 * the Digiface does not have (the shared memory is as wide as the largest interface). */
static void some_mix(struct ofm_shm *s)
{
	memset(s, 0, sizeof(*s));
	for (int o = 0; o < OFM_N_OUT; o++) {
		s->gain[o][OFM_N_IN + o] = 1.0f;
		s->out_gain[o] = 1.0f;
	}
	s->gain[0][0] = 0.5f;                /* input 1 to output 1 */
	s->gain[2][35] = 1.0f;               /* an input the Digiface doesn't have */
	s->out_gain[33] = 0.25f;
}

static void check_open(const struct ofm_backend *be, void *h)
{
	dev.present = false;
	expect("no device", be->open(h), DFU_STATE_NO_DEVICE);
	plug_in();
	dev.open_err = LIBUSB_ERROR_ACCESS;
	expect("no udev rule", be->open(h), DFU_STATE_NO_ACCESS);
	dev.open_err = LIBUSB_ERROR_IO;
	expect("open fails", be->open(h), DFU_STATE_ERROR);
	dev.open_err = 0;
	dev.claim_err = LIBUSB_ERROR_BUSY;
	expect("another program has the mixer", be->open(h), DFU_STATE_BUSY);
	expect("and it is not open", be->is_open(h), 0);
	dev.claim_err = 0;
	expect("open", be->open(h), DFU_STATE_ACTIVE);
	expect("claimed the mixer interface", dev.claimed, 1);
	expect("opening sends nothing", n_ctl + n_words, 0);
}

static void check_sync(const struct ofm_backend *be, void *h)
{
	static struct ofm_shm s;
	static uint32_t first[MAX_WORDS];
	bool reset = true;
	int n_first;

	some_mix(&s);
	expect("status before the first sync", be->status(h, &s, &reset), 0);
	expect("rate", s.rate, 48000);
	expect("a mixer that was never switched on is no reset", reset, 0);
	expect("no meters before the mixer is on", be->meters(h, &s), -EAGAIN);

	/* the first sync after open: every node reset, then one node per crosspoint in use (a
	 * route, then its gain), the output faders, and last the mixer switched on */
	expect("first sync", be->sync(h, &s, true), 0);
	expect("commands", n_words, N_NODES + 2 * 35);
	expect("in three transfers", n_bulk, 3);
	for (int n = 0; n < N_NODES; n++)
		if (words[n] != (0xC000FFFFu | (uint32_t)n << 16)) {
			expect("node reset first", words[n], 0xC000FFFFu | (uint32_t)n << 16);
			break;
		}
	expect("then input 1 routed to output 1, on node 0", words[N_NODES], 0x40000000u);
	expect("and its gain", words[N_NODES + 1], 0x8800);
	expect("then playback 1, on node 1", words[N_NODES + 2], 0x40000000u | 1 << 16 | 0x100);
	expect("control writes", n_ctl, DFU_N_OUT + 2);
	expect("output 1 fader", ctl[0].req << 24 | ctl[0].idx << 16 | ctl[0].val,
	       21 << 24 | 0x100 << 16 | 0x9000);
	expect("output 34 fader", ctl[33].idx << 16 | ctl[33].val, 0x121 << 16 | 0x2000);
	expect("mixer on in register 2", ctl[34].req << 24 | ctl[34].idx << 16 | ctl[34].val,
	       18 << 24 | 0x0100 << 16);
	expect("and in register 1", ctl[35].req << 24 | ctl[35].idx << 16 | ctl[35].val,
	       16 << 24 | 0x0400 << 16);
	expect("routes", be->routes(h), 35);
	expect("the device mixes it", mixes(&s), 1);
	n_first = n_words;
	memcpy(first, words, sizeof(first));

	/* nothing changed: nothing is sent */
	forget();
	expect("idle sync", be->sync(h, &s, false), 0);
	expect("idle sync is silent", n_ctl + n_bulk, 0);

	/* a changed gain sends that gain and nothing else */
	s.gain[0][0] = 0.25f;
	expect("gain change", be->sync(h, &s, false), 0);
	expect("one command", n_words, 1);
	expect("the node's new gain", words[0], 0x2000);
	expect("no control writes", n_ctl, 0);

	/* a new crosspoint takes a free node: route, then gain */
	forget();
	s.gain[5][3] = 1.0f;
	expect("new route", be->sync(h, &s, false), 0);
	expect("two commands", n_words, 2);
	expect("route input 4 to output 6", words[0], 0x40000000u | 35 << 16 | 5 << 9 | 3);
	expect("then its gain", words[1], 35 << 16 | 0x9000);
	expect("routes after adding one", be->routes(h), 36);

	/* a crosspoint that goes silent gives its node back, and the next one reuses it */
	forget();
	s.gain[0][0] = 0.0f;
	s.gain[7][OFM_N_IN + 1] = 2.0f;
	expect("swap", be->sync(h, &s, false), 0);
	expect("three commands", n_words, 3);
	expect("node 0's gain to 0 first", words[0], 0);
	expect("then the same node routed anew", words[1], 0x40000000u | 7 << 9 | 0x101);
	expect("and its gain, +6 dB", words[2], 0xa000);
	expect("routes after the swap", be->routes(h), 36);
	expect("the device mixes the changes", mixes(&s), 1);

	/* an output fader is one control write */
	forget();
	s.out_gain[1] = 0.0f;
	expect("mute an output", be->sync(h, &s, false), 0);
	expect("no mixer commands", n_bulk, 0);
	expect("one control write", n_ctl, 1);
	expect("output 2 fader to 0", ctl[0].req << 24 | ctl[0].idx << 16 | ctl[0].val,
	       21 << 24 | 0x101 << 16);

	/* channels the Digiface doesn't have are never sent */
	forget();
	s.gain[35][OFM_N_IN + 35] = 0.5f;
	s.gain[1][33] = 1.0f;
	s.out_gain[35] = 0.5f;
	expect("other interfaces' channels", be->sync(h, &s, false), 0);
	expect("send nothing", n_ctl + n_bulk, 0);

	/* the meters: one frame of each kind per read, into the shared memory's three ranges */
	expect("meters", be->meters(h, &s), 0);
	check_close("input peak", s.peak_src[0], 0.125f);
	check_close("playback peak", s.peak_src[OFM_N_IN], 0.25f);
	check_close("output peak", s.peak_out[32], 0.375f);
	expect("the other channel of the pair", s.peak_out[33] == 0.0f, 1);

	/* the driver re-initialised the device (replug, resume): the status says the mixer is off,
	 * and the full resend is the first sync over again */
	some_mix(&s);
	dev.reg1 |= 0x0400;
	dev.reg2 |= 0x0100;
	memset(dev.node, 0, sizeof(dev.node));
	expect("status after a reset", be->status(h, &s, &reset), 0);
	expect("reset seen", reset, 1);
	forget();
	expect("full resend", be->sync(h, &s, true), 0);
	expect("as many commands as the first sync", n_words, n_first);
	expect("the same commands", memcmp(words, first, sizeof(uint32_t) * (size_t)n_first), 0);
	expect("the device mixes it again", mixes(&s), 1);
	expect("mixer back on", dev.reg1 | dev.reg2, 0);
	be->status(h, &s, &reset);
	expect("no reset afterwards", reset, 0);
}

/* The mixer has 2048 nodes; a matrix that needs more is applied as far as they go. */
static void check_route_limit(const struct ofm_backend *be, void *h)
{
	static struct ofm_shm s, part;
	const int all = DFU_N_OUT * (DFU_N_IN + DFU_N_PLAY);     /* 2244 crosspoints */

	for (int o = 0; o < OFM_N_OUT; o++) {
		s.out_gain[o] = 1.0f;
		for (int k = 0; k < OFM_N_SRC; k++)
			s.gain[o][k] = 0.5f;
	}
	forget();
	expect("too many routes", be->sync(h, &s, true), -ENOSPC);
	expect("every node in use", be->routes(h), N_NODES);
	expect("a route and a gain for each", n_words, N_NODES + 2 * N_NODES);
	/* the first 2048 crosspoints, output by output, are the ones that play */
	part = s;
	for (int o = 0, n = 0; o < OFM_N_OUT; o++)
		for (int k = 0; k < OFM_N_SRC; k++)
			if (o < DFU_N_OUT && (k < DFU_N_IN || (k >= OFM_N_IN && k < OFM_N_IN + DFU_N_PLAY)) &&
			    n++ >= N_NODES)
				part.gain[o][k] = 0.0f;
	expect("the first 2048 are mixed", mixes(&part), 1);

	/* still over the limit: nothing more to send, and the answer stays */
	forget();
	expect("still too many", be->sync(h, &s, false), -ENOSPC);
	expect("nothing sent", n_ctl + n_bulk, 0);

	/* output 1 goes silent: its 66 nodes go to the crosspoints that were left out */
	for (int k = 0; k < OFM_N_SRC; k++)
		s.gain[0][k] = 0.0f;
	expect("still short of nodes", be->sync(h, &s, false), -ENOSPC);
	expect("all nodes in use again", be->routes(h), N_NODES);
	expect("66 freed, 66 routed with their gain", n_words, 66 + 2 * 66);
	/* and with outputs 2 and 3 silent as well, everything fits */
	for (int k = 0; k < OFM_N_SRC; k++)
		s.gain[1][k] = s.gain[2][k] = 0.0f;
	expect("fits", be->sync(h, &s, false), 0);
	expect("routes in use", be->routes(h), all - 3 * 66);
	expect("the device mixes all of it", mixes(&s), 1);
}

/* Unplugged: the error reaches the engine, which closes the device; after the next open the
 * first sync sends everything again. */
static void check_unplug(const struct ofm_backend *be, void *h)
{
	static struct ofm_shm s;
	bool reset;

	some_mix(&s);
	expect("sync before the unplug", be->sync(h, &s, true), 0);
	dev.transfer_err = LIBUSB_ERROR_NO_DEVICE;
	s.gain[0][0] = 1.0f;
	expect("sync on a lost device", be->sync(h, &s, false), LIBUSB_ERROR_NO_DEVICE);
	expect("status on a lost device", be->status(h, &s, &reset), LIBUSB_ERROR_NO_DEVICE);
	expect("meters on a lost device", be->meters(h, &s), LIBUSB_ERROR_NO_DEVICE);
	be->close(h);
	expect("closed", be->is_open(h), 0);
	expect("interface released", dev.claimed, 0);

	plug_in();
	expect("open again", be->open(h), DFU_STATE_ACTIVE);
	expect("no meters until the mix is back", be->meters(h, &s), -EAGAIN);
	forget();
	expect("sync after the replug", be->sync(h, &s, true), 0);
	expect("everything again", n_words, N_NODES + 2 * 35);
	expect("the device mixes it", mixes(&s), 1);
	expect("meters are back", be->meters(h, &s), 0);
}

static void check_backend(void)
{
	const struct ofm_backend *be = &ofm_backend_digiface;
	const char *path = getenv("OFM_RECORD");
	void *h = be->create();

	if (path)
		record = fopen(path, "w");
	check_open(be, h);
	check_sync(be, h);
	check_route_limit(be, h);
	check_unplug(be, h);
	be->destroy(h);
	if (record)
		fclose(record);
}

int main(void)
{
	check_backend();
	check_meter_frame();
	check_rate(2, 48000);
	check_rate(6, 96000);
	check_rate(10, 192000);
	check_rate(3, 0);
	check_rate(15, 0);
	check(0.0f, 0x0000);
	check(-1.0f, 0x0000);
	check(NAN, 0x0000);
	check(1.0f, 0x9000);          /* unity, as the kernel writes to the output faders */
	check(2.0f, 0xa000);          /* +6 dB, the maximum */
	check(4.0f, 0xa000);          /* clamped */
	check(0.25f, 0x2000);         /* below 0x4000: stored as is */
	check(0.5f, 0x8800);          /* 0x4000 >> 3 | 0x8000 */
	if (!failed)
		printf("test_digiface_usb: ok\n");
	return failed;
}
