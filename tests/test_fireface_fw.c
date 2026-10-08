/* Unit test for the Fireface 802 command encoding and meter decoding (no device needed). */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

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

int main(void)
{
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
	if (!failed)
		printf("test_fireface_fw: ok\n");
	return failed;
}
