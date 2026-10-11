/* Unit test for the Fireface 802 USB (Class Compliant) SysEx encoding and decoding. */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../engine/fireface_usb.h"

static int failed;

static void check(const char *what, long got, long want)
{
	if (got != want) {
		fprintf(stderr, "%s = 0x%lx, want 0x%lx\n", what, got, want);
		failed = 1;
	}
}

static void check_bytes(const char *what, const uint8_t *got, const uint8_t *want, size_t n)
{
	if (memcmp(got, want, n) != 0) {
		fprintf(stderr, "%s:", what);
		for (size_t i = 0; i < n; i++)
			fprintf(stderr, " %02x", got[i]);
		fprintf(stderr, "\n");
		failed = 1;
	}
}

static void put_word(uint8_t *p, uint32_t w)
{
	for (int i = 0; i < 5; i++)
		p[i] = (uint8_t)((w >> (7 * i)) & 0x7f);
}

int main(void)
{
	uint8_t msg[FF802USB_MSG_LEN];

	/* oscmix's protocol notes: mute output 3 (register 0x0582 = 1), no parity bit */
	static const uint8_t mute[] = { 0xf0, 0x00, 0x20, 0x0d, 0x10, 0x00,
					0x01, 0x00, 0x08, 0x2c, 0x00, 0xf7 };
	ff802usb_reg_msg(0x05820001, msg);
	check_bytes("mute msg", msg, mute, sizeof(mute));
	/* a packet captured from an 802: register 0x3f00 = 0x0900, with the parity bit */
	static const uint8_t serial[] = { 0xf0, 0x00, 0x20, 0x0d, 0x10, 0x00,
					  0x00, 0x12, 0x00, 0x78, 0x0b, 0xf7 };
	ff802usb_reg_msg(0x3f000900, msg);
	check_bytes("serial msg", msg, serial, sizeof(serial));

	check("gain off", ff802usb_encode_gain(0.0f), 0);
	check("gain unity", ff802usb_encode_gain(1.0f), 0x9000);
	check("gain +6 dB", ff802usb_encode_gain(2.0f), 0xa000);
	check("gain clamp", ff802usb_encode_gain(8.0f), 0xa000);
	check("gain -6 dB", ff802usb_encode_gain(0.5f), 0x4000);
	check("gain -40 dB", ff802usb_encode_gain(0.01f), 328);

	check("rate 48k", ff802usb_rate(2), 48000);
	check("rate 192k", ff802usb_rate(8), 192000);
	check("rate bad", ff802usb_rate(9), 0);

	/* register notifications: clock source and rate */
	struct ff802usb_info info = { .clock = -1, .rate = -1 };
	static struct ofm_shm s;
	uint8_t pkt[7 + 5 * 3 * 30];
	memcpy(pkt, (uint8_t[]){ 0xf0, 0x00, 0x20, 0x0d, 0x10, 0x00 }, 6);
	put_word(pkt + 6, 0x3d200003);
	put_word(pkt + 11, 0x3d210005);
	pkt[16] = 0xf7;
	check("subid", ff802usb_handle_sysex(pkt, 17, &info, &s), 0);
	check("clock", info.clock, 3);
	check("rate", info.rate, 5);

	/* levels: subid 4 (inputs), channel 3 at half scale; delivered in two pieces */
	memset(pkt, 0, sizeof(pkt));
	memcpy(pkt, (uint8_t[]){ 0xf0, 0x00, 0x20, 0x0d, 0x10, 0x04 }, 6);
	put_word(pkt + 6 + 5 * (3 * 2 + 2), (1u << 22) << 4);   /* peak word of channel 3 */
	pkt[sizeof(pkt) - 1] = 0xf7;
	struct ff802usb_rx rx = { 0 };
	ff802usb_rx_feed(&rx, pkt, 100, &info, &s);
	ff802usb_rx_feed(&rx, pkt + 100, sizeof(pkt) - 100, &info, &s);
	check("level packets", info.level_packets, 1);
	if (fabsf(s.peak_src[2] - 0.5f) > 1e-6f || s.peak_src[1] != 0.0f) {
		fprintf(stderr, "input meters: %g %g\n", s.peak_src[1], s.peak_src[2]);
		failed = 1;
	}

	/* the rawmidi proc file */
	const char *proc =
		"Fireface 802 (23735948)\n\n"
		"Output 0\n  Tx bytes     : 0\n"
		"Output 1\n  Tx bytes     : 120\n  Owner PID    : 1234\n  Mode         : native\n"
		"  Buffer size  : 4096\n  Avail        : 4000\n"
		"Input 0\n  Rx bytes     : 0\n";
	check("avail 1", ff802usb_proc_avail(proc, 1), 4000);
	check("avail 0 (closed)", ff802usb_proc_avail(proc, 0), -1);
	check("avail 2 (none)", ff802usb_proc_avail(proc, 2), -1);

	if (!failed)
		printf("test_fireface_usb: ok\n");
	return failed;
}
