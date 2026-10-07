/* Unit test for the Digiface USB gain encoding (no device needed). Run via `make test`. */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

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

int main(void)
{
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
