/* Unit test for the HDSPe gain encoding, source numbering, card matching and meter decoding, and
 * for what the backends write to the card's mixer: a fake driver answers the ioctls and records
 * the writes (no card needed). */
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>

#include <sound/asound.h>
#include <sound/hdspm.h>

#include "../engine/backend.h"
#include "../engine/hdspe.h"
#include "../engine/shm_layout.h"

static int failed;

static void check(const char *what, long got, long want)
{
	if (got != want) {
		fprintf(stderr, "%s = %ld, want %ld\n", what, got, want);
		failed = 1;
	}
}

/* ---------- what the backends write, to a fake driver ---------- */

#define MAX_REC 4096

static struct { int src, dst, gain; } rec[MAX_REC];   /* writes to the Mixer control */
static int n_rec, n_read;
static uint16_t driver_mix[64][128];   /* the driver's copy of the card's mixer: [output][source] */
static int write_errno;                /* what a mixer write fails with (0 = works) */

static int fake_ioctl(int fd, unsigned long req, void *arg)
{
	if (req == SNDRV_CTL_IOCTL_ELEM_WRITE || req == SNDRV_CTL_IOCTL_ELEM_READ) {
		struct snd_ctl_elem_value *v = arg;
		long src = v->value.integer.value[0], dst = v->value.integer.value[1];
		if (src < 0 || src >= 128 || dst < 0 || dst >= 64) {
			errno = EINVAL;
			return -1;
		}
		if (req == SNDRV_CTL_IOCTL_ELEM_READ) {
			n_read++;
			v->value.integer.value[2] = driver_mix[dst][src];
			return 0;
		}
		if (write_errno) {
			errno = write_errno;
			return -1;
		}
		if (n_rec < MAX_REC) {
			rec[n_rec].src = (int)src;
			rec[n_rec].dst = (int)dst;
			rec[n_rec].gain = (int)v->value.integer.value[2];
		}
		n_rec++;
		driver_mix[dst][src] = (uint16_t)v->value.integer.value[2];
		return 0;
	}
	if (req == SNDRV_HDSPM_IOCTL_GET_CONFIG) {
		struct hdspm_config *cfg = arg;
		cfg->system_sample_rate = 96000;
		cfg->system_clock_mode = 1;
		cfg->autosync_ref = 3;
		return 0;
	}
	if (req == SNDRV_HDSPM_IOCTL_GET_PEAK_RMS) {
		struct hdspm_peak_rms *m = arg;
		memset(m, 0, sizeof(*m));
		m->input_peaks[1] = 0x7fffff03;       /* three full-scale samples in a row */
		m->input_rms[1] = (uint64_t)(0.0625 * 1125899638407184.0 * 8191.0);   /* RMS 0.25 */
		m->output_rms[15] = m->input_rms[1];  /* hdspmixer shows none for outputs */
		m->playback_peaks[2] = 0x40000000;
		m->output_peaks[15] = 0x7fffff00;
		m->output_peaks[40] = 0x7fffff00;     /* past every card's channels */
		return 0;
	}
	errno = ENOTTY;
	return -1;
}

/* True if the driver's mixer is exactly s, with each output's master folded into its sends. */
static int driver_has(const struct ofm_shm *s, int n_ch)
{
	for (int o = 0; o < 64; o++)
		for (int src = 0; src < 128; src++) {
			int k = src < HDSPE_PLAY_SRC ? src : OFM_N_IN + src - HDSPE_PLAY_SRC;
			int have = o < n_ch && src % HDSPE_PLAY_SRC < n_ch;
			int want = have ? hdspe_encode_gain(s->gain[o][k] * s->out_gain[o]) : 0;
			if (driver_mix[o][src] != want)
				return 0;
		}
	return 1;
}

static void check_writes(const struct ofm_backend *be, int n_ch)
{
	static struct ofm_shm s;
	const int all = n_ch * 2 * n_ch;
	bool reset = false;         /* as the engine calls it: status only ever sets it */
	void *h = be->create();

	be->attach(h, &s);
	memset(&s, 0, sizeof(s));
	memset(driver_mix, 0, sizeof(driver_mix));
	for (int o = 0; o < OFM_N_OUT; o++) {
		s.gain[o][OFM_N_IN + o] = 1.0f;      /* playback n to output n, on every channel */
		s.out_gain[o] = 1.0f;                /* the shared memory has, the card's or not */
	}
	s.gain[0][1] = 0.5f;                         /* input 2 to output 1 */
	s.gain[1][0] = 1.0f;
	s.gain[2][OFM_N_IN + 3] = 2.0f;
	s.out_gain[0] = 0.5f;
	s.out_gain[1] = 2.0f;                        /* +6 dB on unity: all the card can do */
	s.out_gain[2] = 2.0f;                        /* +6 dB on +6 dB: held at the maximum */
	s.out_gain[3] = 0.0f;                        /* a muted output */
	driver_mix[5][7] = 1234;                     /* what another program left in the mixer */

	/* the first sync after open writes every crosspoint, the silent ones too: the mixer is
	 * write only and may hold anything */
	n_rec = 0;
	check("first sync", be->sync(h, &s, true), 0);
	check("every crosspoint written", n_rec, all);
	check("output by output, inputs first", rec[1].dst << 16 | rec[1].src, 1);
	check("then playback, from source 64", rec[n_ch].dst << 16 | rec[n_ch].src, HDSPE_PLAY_SRC);
	check("last write", rec[all - 1].dst << 16 | rec[all - 1].src,
	      (n_ch - 1) << 16 | (HDSPE_PLAY_SRC + n_ch - 1));
	/* there is no output fader: the master is part of every send to its output */
	check("master folded into a send", driver_mix[0][1], 8192);
	check("and into playback", driver_mix[0][HDSPE_PLAY_SRC], 16384);
	check("+6 dB master on a unity send", driver_mix[1][0], HDSPE_GAIN_MAX);
	check("+12 dB is held at +6 dB", driver_mix[2][HDSPE_PLAY_SRC + 3], HDSPE_GAIN_MAX);
	check("a muted output is silent", driver_mix[3][HDSPE_PLAY_SRC + 3], 0);
	check("the driver has the mix, and nothing else", driver_has(&s, n_ch), 1);
	check("routes", be->routes(h), n_ch + 3 - 1);

	/* nothing changed: nothing is written */
	n_rec = 0;
	check("idle sync", be->sync(h, &s, false), 0);
	check("idle sync is silent", n_rec, 0);

	/* a changed gain is one write */
	s.gain[0][1] = 1.0f;
	check("gain change", be->sync(h, &s, false), 0);
	check("one write", n_rec, 1);
	check("the crosspoint", rec[0].dst << 24 | rec[0].src << 16 | rec[0].gain, 1 << 16 | 16384);

	/* a changed master rewrites that output's sends, and only those that are on */
	n_rec = 0;
	s.out_gain[0] = 1.0f;
	check("master change", be->sync(h, &s, false), 0);
	check("its two sends", n_rec, 2);
	check("both on output 1", rec[0].dst | rec[1].dst, 0);
	check("the input send", rec[0].src << 16 | rec[0].gain, 1 << 16 | 32768);
	check("the playback send", rec[1].src << 16 | rec[1].gain, HDSPE_PLAY_SRC << 16 | 32768);
	check("the driver follows", driver_has(&s, n_ch), 1);

	/* the status call reads one output back per call and compares it with what was sent */
	n_read = 0;
	check("status", be->status(h, &s, &reset), 0);
	check("rate", s.rate, 96000);
	check("clock state", s.dev_status, 1 | 3 << 8);
	check("no reset", reset, 0);
	check("one output read back", n_read, 2 * n_ch);
	for (int i = 1; i < n_ch; i++) {
		be->status(h, &s, &reset);
		check("no reset on any output", reset, 0);
	}

	/* the driver was reloaded (its mixer starts silent), or another program changed a send:
	 * the read-back finds it within one round, and the full resend puts the mix back */
	memset(driver_mix, 0, sizeof(driver_mix));
	int calls = 0;
	for (reset = false; !reset && calls < 2 * n_ch; calls++)
		check("status after a reload", be->status(h, &s, &reset), 0);
	check("found on the first output", calls, 1);
	n_rec = 0;
	check("full resend", be->sync(h, &s, true), 0);
	check("every crosspoint again", n_rec, all);
	check("the mix is back", driver_has(&s, n_ch), 1);
	driver_mix[n_ch - 1][3] = 999;               /* someone else's send into the last output */
	for (calls = 0, reset = false; !reset && calls < 2 * n_ch; calls++)
		be->status(h, &s, &reset);
	check("found when its output comes round", calls, n_ch - 1);   /* the round goes on from 2 */
	check("resend", be->sync(h, &s, true), 0);
	check("the foreign send is gone", driver_has(&s, n_ch), 1);

	/* the driver refuses mixer writes while playback and capture are open in two programs:
	 * the error reaches the engine, and what was not written is written the next time */
	n_rec = 0;
	s.gain[1][0] = 0.25f;
	write_errno = EBUSY;
	check("busy", be->sync(h, &s, false), -EBUSY);
	write_errno = 0;
	check("after the other program closed", be->sync(h, &s, false), 0);
	check("the write went out", n_rec, 1);
	check("with the master folded in", rec[0].gain, 16384);

	/* meters: the card's own channel numbers, playback after the inputs */
	memset(s.peak_src, 0, sizeof(s.peak_src));
	memset(s.peak_out, 0, sizeof(s.peak_out));
	check("meters", be->meters(h, &s), 0);
	check("input 2 at full scale", s.peak_src[1] == 1.0f, 1);
	check("playback 3 at half", fabsf(s.peak_src[OFM_N_IN + 2] - 0.5f) < 1e-3f, 1);
	check("output 16", s.peak_out[15] == 1.0f, 1);
	check("input 2 RMS", fabsf(s.rms_src[1] - 0.25f) < 1e-6f, 1);
	check("no RMS without signal", s.rms_src[0] == 0.0f && s.rms_src[OFM_N_IN + 2] == 0.0f, 1);
	check("no output RMS", s.rms_out[15] == 0.0f, 1);
	check("input 2 overs", s.over_src[1] == 3.0f, 1);
	check("no overs at half scale", s.over_src[OFM_N_IN + 2] == 0.0f, 1);
	be->destroy(h);
}

int main(void)
{
	hdspe_ioctl = fake_ioctl;
	check_writes(&ofm_backend_raydat, 36);
	check_writes(&ofm_backend_aio, 20);
	check_writes(&ofm_backend_aiopro, 20);
	check_writes(&ofm_backend_aes, 16);

	check("gain off", hdspe_encode_gain(0.0f), 0);
	check("gain negative", hdspe_encode_gain(-1.0f), 0);
	check("gain nan", hdspe_encode_gain(NAN), 0);
	check("gain unity", hdspe_encode_gain(1.0f), 32768);
	check("gain -6 dB", hdspe_encode_gain(0.5f), 16384);
	check("gain +6 dB clamps", hdspe_encode_gain(2.0f), 65535);
	check("gain way over", hdspe_encode_gain(10.0f), 65535);

	check("AES L", hdspe_mixer_source(0), 0);
	check("last input", hdspe_mixer_source(35), 35);
	check("playback 1", hdspe_mixer_source(OFM_N_IN), 64);
	check("playback 36", hdspe_mixer_source(OFM_N_IN + 35), 99);

	if (fabsf(hdspe_peak(0x7fffff00) - 1.0f) > 1e-6f || hdspe_peak(0x0000000f) != 0.0f ||
	    fabsf(hdspe_peak(0x40000003) - 0.5f) > 1e-3f) {
		fprintf(stderr, "peaks: %g %g %g\n", hdspe_peak(0x7fffff00), hdspe_peak(0xf),
			hdspe_peak(0x40000003));
		failed = 1;
	}

	check("card", hdspe_card_match(&hdspe_raydat, "HDSPM", "RME RayDAT_1a2b3c"), 1);
	check("card without serial", hdspe_card_match(&hdspe_raydat, "HDSPM", "RME RayDAT"), 1);
	check("AIO isn't a RayDAT", hdspe_card_match(&hdspe_raydat, "HDSPM", "RME AIO_1a2b3c"), 0);
	check("other driver", hdspe_card_match(&hdspe_raydat, "USB-Audio", "RME RayDAT"), 0);
	check("snd-hdspe card", hdspe_card_match(&hdspe_raydat, "HDSPe", "RME RayDAT_00012345"), 1);
	check("AIO", hdspe_card_match(&hdspe_aio, "HDSPM", "RME AIO_1a2b3c"), 1);
	check("AIO Pro isn't an AIO", hdspe_card_match(&hdspe_aio, "HDSPe", "RME AIO Pro_00012345"), 0);
	check("AIO Pro", hdspe_card_match(&hdspe_aio_pro, "HDSPe", "RME AIO Pro_00012345"), 1);
	check("AIO without serial", hdspe_card_match(&hdspe_aio, "HDSPe", "RME AIO"), 1);
	check("AES", hdspe_card_match(&hdspe_aes, "HDSPM", "RME AES32_1a2b3c"), 1);
	check("MADI isn't supported", hdspe_card_match(&hdspe_aes, "HDSPM", "RME MADI_1a2b3c"), 0);

	check("old card, snd-hdspm", hdspe_driver_usable("HDSPM", 0x10ee), 1);
	check("new card, snd-hdspm", hdspe_driver_usable("HDSPM", HDSPE_VENDOR_RME), 0);
	check("new card, snd-hdspe", hdspe_driver_usable("HDSPe", HDSPE_VENDOR_RME), 1);
	check("old card, snd-hdspe", hdspe_driver_usable("HDSPe", 0x10ee), 1);
	check("unknown vendor, snd-hdspm", hdspe_driver_usable("HDSPM", 0), 1);

	/* snd-hdspm: the measured clock is at single speed, the set rate holds the speed */
	check("measured 48k", hdspe_hdspm_rate(47998, 48000), 48000);
	check("slaved to 44.1k, set to 48k", hdspe_hdspm_rate(44101, 48000), 44100);
	check("96k", hdspe_hdspm_rate(48001, 96000), 96000);
	check("88.2k", hdspe_hdspm_rate(44099, 96000), 88200);
	check("192k", hdspe_hdspm_rate(47990, 192000), 192000);
	check("176.4k, set to 192k", hdspe_hdspm_rate(44100, 192000), 176400);
	check("a clock measured at double speed", hdspe_hdspm_rate(96003, 96000), 96000);
	check("and at quad speed", hdspe_hdspm_rate(191990, 48000), 192000);
	check("varispeed stays", hdspe_hdspm_rate(46000, 48000), 46000);
	check("varispeed at double speed", hdspe_hdspm_rate(46000, 88200), 92000);
	check("no clock", hdspe_hdspm_rate(0, 48000), 0);

	if (!failed)
		printf("test_hdspe: ok\n");
	return failed;
}
