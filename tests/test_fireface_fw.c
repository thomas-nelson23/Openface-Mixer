/* Unit test for the Fireface 802, 800 and 400 encoding and meter decoding, and for what the
 * backends send: a fake bus records the transactions (no device needed). */
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include <linux/firewire-constants.h>

#include "../engine/backend.h"
#include "../engine/digiface_usb.h"   /* DFU_STATE_* */
#include "../engine/fireface_fw.h"

static int failed;

static void check(const char *what, long got, long want)
{
	if (got != want) {
		fprintf(stderr, "%s = 0x%lx, want 0x%lx\n", what, got, want);
		failed = 1;
	}
}

static void put32(uint8_t *p, uint32_t v)
{
	for (int i = 0; i < 4; i++)
		p[i] = (uint8_t)(v >> (8 * i));
}

static void check_meters(void)
{
	static struct ofm_shm s;
	uint8_t chunk[FF802_METER_CHUNK] = { 0 };

	put32(chunk + 256 + 4 * 2, 0x07fffff0 / 2);          /* input 3 at half scale */
	put32(chunk + 256 + 4 * 29, 0xffffffff);             /* B 8: saturated, masked to full */
	put32(chunk + FF802_METER_CHUNK - 4, 0x66666666);
	check("tag", ff802_decode_meter_chunk(chunk, &s), 0x66666666);
	if (fabsf(s.peak_src[2] - 0.5f) > 1e-6f || fabsf(s.peak_src[29] - 1.0f) > 1e-6f ||
	    s.peak_out[2] != 0.0f) {
		fprintf(stderr, "input meters: %g %g %g\n", s.peak_src[2], s.peak_src[29], s.peak_out[2]);
		failed = 1;
	}

	put32(chunk + FF802_METER_CHUNK - 4, 0x33333333);    /* playback lands after the inputs */
	ff802_decode_meter_chunk(chunk, &s);
	if (fabsf(s.peak_src[OFM_N_IN + 2] - 0.5f) > 1e-6f) {
		fprintf(stderr, "playback meter: %g\n", s.peak_src[OFM_N_IN + 2]);
		failed = 1;
	}

	put32(chunk + FF802_METER_CHUNK - 4, 0x55555555);    /* FX bus: ignored */
	memset(&s, 0, sizeof(s));
	ff802_decode_meter_chunk(chunk, &s);
	if (s.peak_src[2] != 0.0f || s.peak_out[2] != 0.0f) {
		fprintf(stderr, "an FX chunk changed the meters\n");
		failed = 1;
	}
}

static void check_former(void)
{
	static struct ofm_shm s;
	uint32_t block[FORMER_MAX_BLOCK];
	uint8_t raw[12 * 3 * FORMER_MAX_CH] = { 0 };

	/* gains: linear * 0x8000, +6 dB max (snd-firewire-ctl-services' former range) */
	check("former gain 0", former_encode_gain(0.0f), 0);
	check("former gain unity", former_encode_gain(1.0f), 0x8000);
	check("former gain -6 dB", former_encode_gain(0.5f), 0x4000);
	check("former gain +6 dB", former_encode_gain(2.0f), 0x10000);
	check("former gain clamp", former_encode_gain(4.0f), 0x10000);

	/* 800 mixer block: inputs from quadlet 0, playback from quadlet 32 */
	s.gain[3][0] = 1.0f;                 /* AN 1 -> AN 4 */
	s.gain[3][OFM_N_IN + 3] = 0.5f;      /* playback 4 -> AN 4 */
	s.gain[3][29] = 1.0f;                /* not an 800 input: ignored */
	check("800 routes", former_mixer_block(&ff800_model, &s, 3, block), 2);
	check("800 block in", block[0], 0x8000);
	check("800 block play", block[32 + 3], 0x4000);
	check("800 block gap", block[29], 0);
	check("800 meter length", former_meter_len(&ff800_model), 1008);

	/* 400 mixer block: playback from quadlet 18, nothing past 36 */
	s.gain[3][20] = 1.0f;                /* not a 400 input: ignored */
	block[40] = 0xdead;
	check("400 routes", former_mixer_block(&ff400_model, &s, 3, block), 2);
	check("400 block play", block[18 + 3], 0x4000);
	check("400 block end", block[40], 0xdead);
	check("400 meter length", former_meter_len(&ff400_model), 648);

	/* status quadlet 1 holds the configured rate */
	check("rate 48k", former_status_rate(0x00000007), 48000);
	check("rate 44.1k", former_status_rate(0x00000c00), 44100);
	check("rate 96k", former_status_rate(0x0000000e), 96000);

	/* 400 output amplifier: 0 at +6 dB, 0x3f at silence */
	check("amp +6 dB", former_amp_out(0x10000), 0);
	check("amp silent", former_amp_out(0), 0x3f);
	check("amp unity", former_amp_out(0x8000), 0x1f);

	/* 800 meters: after 84 octuples come inputs, playback and outputs */
	memset(&s, 0, sizeof(s));
	put32(raw + 672 + 4 * 2, 0x7fffff00 / 2);            /* AN 3 at half scale */
	put32(raw + 672 + 4 * (28 + 1), 0xffffffff);         /* playback 2: masked to full */
	put32(raw + 672 + 4 * (56 + 27), 0x7fffff00);        /* A2 8 out at full scale */
	put32(raw, 0x7fffff00);                              /* the octuples are ignored */
	former_decode_meters(&ff800_model, raw, &s);
	if (fabsf(s.peak_src[2] - 0.5f) > 1e-6f || fabsf(s.peak_src[OFM_N_IN + 1] - 1.0f) > 1e-6f ||
	    fabsf(s.peak_out[27] - 1.0f) > 1e-6f || s.peak_src[0] != 0.0f) {
		fprintf(stderr, "800 meters: %g %g %g %g\n", s.peak_src[2], s.peak_src[OFM_N_IN + 1],
			s.peak_out[27], s.peak_src[0]);
		failed = 1;
	}

	/* 400 meters: 54 octuples, then 18 of each */
	memset(&s, 0, sizeof(s));
	memset(raw, 0, sizeof(raw));
	put32(raw + 432 + 4 * (18 + 17), 0x7fffff00);        /* playback 18 */
	put32(raw + 432 + 4 * (36 + 6), 0x7fffff00);         /* phones L */
	former_decode_meters(&ff400_model, raw, &s);
	if (fabsf(s.peak_src[OFM_N_IN + 17] - 1.0f) > 1e-6f || fabsf(s.peak_out[6] - 1.0f) > 1e-6f) {
		fprintf(stderr, "400 meters: %g %g\n", s.peak_src[OFM_N_IN + 17], s.peak_out[6]);
		failed = 1;
	}
}

/* ---------- what the backends send, on a fake bus ---------- */

#define REG_CONFIG 0xffff00000014ull
#define REG_DSP    0xffff0000001cull
#define MAX_REC    8192

static struct {
	int tcode;
	uint64_t offset;
	uint32_t value;        /* first quadlet written */
	uint32_t generation;
} rec[MAX_REC];
static int n_rec;
static uint32_t bus_generation;   /* what the bus is at; requests in another one are refused */
static bool generation_stuck;     /* the node never catches up with the bus */
static int64_t fake_ms;           /* the fake clock */
static int write_ms;              /* how long the fake bus takes for one write */
static size_t short_by;           /* bytes missing from every read */

static uint32_t get32(const uint8_t *p)
{
	return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static int fake_open(struct fwdev *f, unsigned long unit_version)
{
	f->fd = dup(2);   /* only ever closed: every request goes through fake_transact */
	f->generation = bus_generation;
	return DFU_STATE_ACTIVE;
}

static int fake_generation(struct fwdev *f)
{
	if (!generation_stuck)
		f->generation = bus_generation;
	return 0;
}

static int fake_transact(struct fwdev *f, int tcode, uint64_t offset, void *data, size_t len,
			 size_t *got)
{
	bool is_read = tcode == TCODE_READ_QUADLET_REQUEST || tcode == TCODE_READ_BLOCK_REQUEST;
	if (n_rec < MAX_REC) {
		rec[n_rec].tcode = tcode;
		rec[n_rec].offset = offset;
		rec[n_rec].value = is_read ? 0 : get32(data);
		rec[n_rec].generation = f->generation;
	}
	n_rec++;
	if (f->generation != bus_generation)
		return RCODE_GENERATION;
	if (is_read) {
		memset(data, 0, len);
		*got = len - short_by;
	} else {
		fake_ms += write_ms;
	}
	return RCODE_COMPLETE;
}

static int64_t fake_now(void)
{
	return fake_ms;
}

static int count_writes(uint64_t offset)
{
	int n = 0;
	for (int i = 0; i < n_rec && i < MAX_REC; i++)
		n += rec[i].tcode != TCODE_READ_QUADLET_REQUEST &&
		     rec[i].tcode != TCODE_READ_BLOCK_REQUEST && rec[i].offset == offset;
	return n;
}

static void *open_fake(const struct ofm_backend *be)
{
	void *h = be->create();
	check("open", be->open(h), DFU_STATE_ACTIVE);
	n_rec = 0;
	return h;
}

static void check_bus_reset(void)
{
	static struct ofm_shm s;
	const struct ofm_backend *be = &ofm_backend_ff802;
	bool reset;

	bus_generation = 1;
	void *h = open_fake(be);
	check("status", be->status(h, &s, &reset), 0);
	check("one request", n_rec, 1);

	/* a bus reset: the request is refused in the old generation and retried in the new one */
	bus_generation = 2;
	n_rec = 0;
	check("status across a bus reset", be->status(h, &s, &reset), 0);
	check("retried once", n_rec, 2);
	check("first try in the old generation", rec[0].generation, 1);
	check("retry in the new generation", rec[1].generation, 2);

	/* a node that never comes back fails after a few tries instead of hanging */
	bus_generation = 3;
	generation_stuck = true;
	n_rec = 0;
	check("status on a lost node", be->status(h, &s, &reset), -EIO);
	check("gave up after three tries", n_rec, 3);
	generation_stuck = false;

	/* a short read is an error: the rest would be leftover memory */
	bus_generation = 1;
	check("reopen", (be->close(h), be->open(h)), DFU_STATE_ACTIVE);
	short_by = 1;
	check("short status read", be->status(h, &s, &reset), -EPROTO);
	check("short meter read", be->meters(h, &s), -EPROTO);
	short_by = 0;
	check("meter read", be->meters(h, &s), 0);
	be->destroy(h);

	char text[160];
	fw_failure_text(TCODE_WRITE_QUADLET_REQUEST, REG_DSP, RCODE_CANCELLED, text, sizeof(text));
	check("failure line", strcmp(text, "FireWire write at 0xffff0000001c failed: timeout "
				     "(rcode 0x11)"), 0);
	fw_failure_text(TCODE_READ_BLOCK_REQUEST, 0x801c0000, -EPROTO, text, sizeof(text));
	check("short read line", strcmp(text, "FireWire block read at 0x0000801c0000 failed: "
					"short answer"), 0);
}

/* The 802's full send: every crosspoint and volume (30 x 62 + 30) and each setting command. */
static void check_802_sync(void)
{
	static struct ofm_shm s;
	static uint8_t seen[0x800];
	const struct ofm_backend *be = &ofm_backend_ff802;
	const int full = 30 * 62 + 30 + 2;

	bus_generation = 1;
	void *h = open_fake(be);
	s.gain[0][OFM_N_IN] = 1.0f;          /* playback 1 -> AN 1 */
	s.out_gain[0] = s.out_gain[1] = 1.0f;
	s.dev_cmd[0] = 0x00060001;           /* two setting commands, with an unused slot between */
	s.dev_cmd[2] = 0x1e070001;

	/* the first connect: no configuration to send yet, so the unit's clock is left alone */
	write_ms = 1;
	check("first slice", be->sync(h, &s, true), 0);
	check("a slice stops when its time is up", n_rec, FW_SLICE_MS);
	int calls = 1;
	for (int before = 0; n_rec != before; calls++) {
		before = n_rec;
		check("next slice", be->sync(h, &s, false), 0);
	}
	check("the whole mix went out", n_rec, full);
	check("in slices, and a last call with nothing left", calls,
	      (full + FW_SLICE_MS - 1) / FW_SLICE_MS + 1);
	check("all to the DSP register", count_writes(REG_DSP), full);
	check("configuration left alone", count_writes(REG_CONFIG), 0);
	for (int i = 0; i < n_rec; i++) {    /* every crosspoint exactly once */
		uint32_t c = rec[i].value & 0x7fffffffu;
		if (c & 0x40000000u)
			seen[(c >> 16) & 0x7ff]++;
	}
	for (int o = 0; o < 30; o++)
		for (int k = 0; k < 62; k++)
			if (seen[0x40 * o + k] != 1) {
				check("crosspoint sent once", seen[0x40 * o + k], 1);
				o = 30;
				break;
			}
	check("routes", be->routes(h), 1);

	/* afterwards only changes go out */
	write_ms = 0;
	n_rec = 0;
	s.gain[1][OFM_N_IN + 1] = 0.5f;
	check("change", be->sync(h, &s, false), 0);
	check("one write", n_rec, 1);
	check("the changed crosspoint", rec[0].value, ff802_parity(ff802_mixer_cmd(1, 33, 0x8800)));

	/* the configuration goes out once the GUI has one, and again only when it changes */
	n_rec = 0;
	s.dev_config = 0x2440;
	check("config", be->sync(h, &s, false), 0);
	check("config again", be->sync(h, &s, false), 0);
	check("configuration written once", count_writes(REG_CONFIG), 1);
	check("nothing else", n_rec, 1);
	check("configuration word", rec[0].value, 0x2440);

	/* a full resend sends it all again */
	n_rec = 0;
	check("full", be->sync(h, &s, true), 0);
	check("everything again", n_rec, full + 1);
	be->destroy(h);
}

static void check_former_sync(void)
{
	static struct ofm_shm s;
	const struct ofm_backend *be = &ofm_backend_ff400;
	const int full = 18 + 18 * 2;        /* mixer blocks, then a volume and an amplifier each */

	bus_generation = 1;
	void *h = open_fake(be);
	memset(&s, 0, sizeof(s));
	s.gain[0][OFM_N_IN] = 1.0f;
	s.out_gain[0] = 1.0f;
	s.dev_cmd[0] = 0x00040410;           /* settings the GUI has not released yet */
	s.dev_cmd[3] = FORMER_AMP_VALID | 40;

	/* the first connect: mixer and volumes only, in slices */
	write_ms = 1;
	check("400 first slice", be->sync(h, &s, true), 0);
	check("400 slice stops", n_rec, FW_SLICE_MS);
	write_ms = 0;
	check("400 rest", be->sync(h, &s, false), 0);
	check("400 whole mix", n_rec, full);
	check("400 configuration left alone", count_writes(ff400_model.config), 0);
	check("400 input gains left alone", count_writes(ff400_model.amp), 18);   /* outputs only */
	check("400 quiet when unchanged", (be->sync(h, &s, false), n_rec), full);

	/* released: the configuration block and the input gain */
	n_rec = 0;
	s.dev_config = 1;
	check("400 config", be->sync(h, &s, false), 0);
	check("400 config again", be->sync(h, &s, false), 0);
	check("400 configuration written once", count_writes(ff400_model.config), 1);
	check("400 input gain written once", count_writes(ff400_model.amp), 1);
	check("400 input gain command", rec[1].value, 40);
	be->destroy(h);
}

int main(void)
{
	fw_bus = (struct fw_bus){ fake_open, fake_transact, fake_generation, fake_now };

	/* mixer gains: 0x9000 = unity, 0xa000 = +6 dB (snd-firewire-ctl-services' range) */
	check("gain 0", ff802_encode_gain(0.0f), 0);
	check("gain unity", ff802_encode_gain(1.0f), 0x9000);
	check("gain +6 dB", ff802_encode_gain(2.0f), 0xa000);
	check("gain clamp", ff802_encode_gain(4.0f), 0xa000);
	check("gain -6 dB", ff802_encode_gain(0.5f), 0x8800);

	/* output volume in 0.1 dB, -65..+6 */
	check("vol unity", ff802_out_vol(1.0f), 0);
	check("vol -6 dB", ff802_out_vol(0.5f), -60);
	check("vol muted", ff802_out_vol(0.0f), -650);
	check("vol floor", ff802_out_vol(1e-6f), -650);
	check("vol max", ff802_out_vol(4.0f), 60);

	/* commands, parity bit 31 makes the number of set bits odd */
	check("mixer play 1 -> AN 1", ff802_mixer_cmd(0, 32, 0x9000), 0x40209000);
	check("mixer last", ff802_mixer_cmd(29, 61, 0), 0x40000000 | ((0x40 * 29 + 61) << 16));
	check("vol cmd", ff802_vol_cmd(0, -60), 0x1e00ffc4);
	check("802 input source", ff802_shm_source(29), 29);
	check("802 FX return source", ff802_shm_source(31), 31);
	check("802 playback 1 source", ff802_shm_source(32), OFM_N_IN);
	check("802 playback 30 source", ff802_shm_source(61), OFM_N_IN + 29);
	check("parity even", ff802_parity(0x40209000), 0xc0209000);
	check("parity odd", ff802_parity(0x1e00ffc4), 0x1e00ffc4);
	for (uint32_t c = 0; c < 4096; c += 7) {
		uint32_t w = ff802_parity(c * 0x9e3779b1u);
		check("parity is odd", __builtin_popcount(w) % 2, 1);
	}

	/* sync status: 48 kHz, internal clock (the kernel's layout) */
	check("rate", ff802_status_rate(0x20000e00), 48000);
	check("rate 96k", ff802_status_rate(0x60000e00), 96000);

	check_meters();
	check_former();
	check_bus_reset();
	check_802_sync();
	check_former_sync();
	if (!failed)
		printf("test_fireface_fw: ok\n");
	return failed;
}
