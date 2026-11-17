/* Unit test for the Fireface 802 and UCX II USB (Class Compliant) SysEx encoding and decoding,
 * and for what the backends send: a fake port records the messages (no device needed). */
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../engine/backend.h"
#include "../engine/digiface_usb.h"   /* DFU_STATE_* */
#include "../engine/fireface_fw.h"
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

/* ---------- what the backends send, to a fake port ---------- */

#define MAX_SENT 8192

static struct {
	int subid;
	uint32_t word;         /* a register write's word, with its parity bit */
} sent[MAX_SENT];
static int n_sent;
static long port_room;         /* what the MIDI output buffer can take */
static int port_err;           /* what every write answers (0 = sent) */

static int fake_write(struct ff802usb *f, const uint8_t *msg, size_t len)
{
	if (port_err)
		return port_err;
	if (n_sent < MAX_SENT) {
		sent[n_sent].subid = msg[5];
		sent[n_sent].word = 0;
		for (int i = 0; len == FF802USB_MSG_LEN && i < 5; i++)
			sent[n_sent].word |= (uint32_t)msg[6 + i] << (7 * i);
	}
	n_sent++;
	return 0;
}

static long fake_room(struct ff802usb *f)
{
	return port_room;
}

/* Sends until a sync has nothing left, with room for per_tick messages each time. Returns the
 * number of sync calls. */
static int send_all(const struct ofm_backend *be, void *h, struct ofm_shm *s, bool full,
		    int per_tick)
{
	int calls = 0;
	port_room = per_tick * FF802USB_MSG_LEN + FF802USB_MSG_LEN - 1;
	for (int before = -1; n_sent != before && calls < MAX_SENT; calls++, full = false) {
		before = n_sent;
		check("sync", be->sync(h, s, full), 0);
		check("no more than the buffer takes", n_sent - before <= per_tick, 1);
	}
	check("the send comes to an end", calls < MAX_SENT, 1);
	return calls;
}

static void check_sends(const struct ofm_backend *be, const struct ffusb_model *m)
{
	static struct ofm_shm s;
	static uint32_t whole[MAX_SENT];
	const int n_src = m->n_src_in + m->n_out;
	const int full = m->n_out * n_src + m->n_out + 3;   /* crosspoints, volumes, 3 settings */
	void *h = be->create();

	memset(&s, 0, sizeof(s));
	s.gain[0][OFM_N_IN] = 1.0f;          /* playback 1 to output 1 */
	s.gain[1][2] = 0.5f;                 /* input 3 to output 2 */
	s.gain[2][0] = 1.0f;                 /* into a muted output */
	s.gain[3][OFM_N_IN - 1] = 1.0f;      /* a source the interface doesn't have */
	s.out_gain[0] = s.out_gain[1] = s.out_gain[3] = 1.0f;
	s.dev_cmd[0] = 0x3d200001;           /* three settings, with unused slots between */
	s.dev_cmd[7] = 0x00410123;
	s.dev_cmd[OFM_N_DEV_CMD - 1] = 0x00420001;

	/* the first sync after open: every crosspoint, each output's volume after its crosspoints,
	 * then the settings in slot order. The unit can't be asked what it has. */
	n_sent = 0;
	port_room = 1 << 20;
	check("first sync", be->sync(h, &s, true), 0);
	check("everything sent", n_sent, full);
	check("first: input 1 to output 1, off", sent[0].word, ff802_parity(ff802_mixer_cmd(0, 0, 0)));
	check("playback 1 to output 1", sent[m->n_src_in].word,
	      ff802_parity(ff802_mixer_cmd(0, FF802_PLAY_SRC, 0x9000)));
	check("output 1 volume after its crosspoints", sent[n_src].word,
	      ff802_parity(ffusb_vol_cmd(m, 0, 0)));
	check("input 3 to output 2", sent[n_src + 1 + 2].word,
	      ff802_parity(ff802_mixer_cmd(1, 2, 0x4000)));
	check("a muted output's crosspoints are off", sent[2 * (n_src + 1)].word,
	      ff802_parity(ff802_mixer_cmd(2, 0, 0)));
	check("and its volume at the bottom", sent[2 * (n_src + 1) + n_src].word,
	      ff802_parity(ffusb_vol_cmd(m, 2, -650)));
	check("settings last, in slot order", sent[full - 3].word, ff802_parity(0x3d200001));
	check("last setting", sent[full - 1].word, ff802_parity(0x00420001));
	check("routes", be->routes(h), 2);
	for (int i = 0; i < full; i++) {
		check("all register writes", sent[i].subid, 0);
		whole[i] = sent[i].word;
	}

	/* nothing changed: nothing is sent */
	n_sent = 0;
	check("idle sync", be->sync(h, &s, false), 0);
	check("idle sync is silent", n_sent, 0);

	/* a changed gain, a changed setting: just those */
	s.gain[1][2] = 1.0f;
	s.dev_cmd[7] = 0x00410124;
	check("change", be->sync(h, &s, false), 0);
	check("two writes", n_sent, 2);
	check("the crosspoint", sent[0].word, ff802_parity(ff802_mixer_cmd(1, 2, 0x9000)));
	check("the setting", sent[1].word, ff802_parity(0x00410124));

	/* muting an output switches its crosspoints off, then lowers its volume */
	n_sent = 0;
	s.out_gain[0] = 0.0f;
	check("mute", be->sync(h, &s, false), 0);
	check("crosspoint and volume", n_sent, 2);
	check("crosspoint off first", sent[0].word,
	      ff802_parity(ff802_mixer_cmd(0, FF802_PLAY_SRC, 0)));
	check("then the volume", sent[1].word, ff802_parity(ffusb_vol_cmd(m, 0, -650)));
	check("one route left", be->routes(h), 1);

	/* a write that fails is reported and sent again by the next sync */
	n_sent = 0;
	s.out_gain[0] = 1.0f;
	port_err = -ETIMEDOUT;
	check("port does not take it", be->sync(h, &s, false), -ETIMEDOUT);
	port_err = 0;
	check("retry", be->sync(h, &s, false), 0);
	check("both writes went out", n_sent, 2);
	s.gain[1][2] = 0.5f;
	s.dev_cmd[7] = 0x00410123;

	/* The MIDI output buffer drops what does not fit, so a sync sends only what it has room
	 * for and the next ones carry on: the full resend after a reconnect arrives whole, each
	 * message once and in the same order, however small the buffer is. */
	for (int per_tick = 1; per_tick <= 300; per_tick += 23) {
		n_sent = 0;
		int calls = send_all(be, h, &s, true, per_tick);
		check("the whole resend", n_sent, full);
		check("ticks it took", calls, (full + per_tick - 1) / per_tick + 1);
		for (int i = 0; i < full && i < n_sent; i++)
			if (sent[i].word != whole[i]) {
				check("same messages in the same order", sent[i].word, whole[i]);
				break;
			}
	}
	/* no room at all: nothing goes out, nothing is lost */
	n_sent = 0;
	port_room = FF802USB_MSG_LEN - 1;
	check("full buffer", be->sync(h, &s, true), 0);
	check("nothing sent into a full buffer", n_sent, 0);
	check("then everything", (send_all(be, h, &s, false, 500), n_sent), full);
	/* a change during a resend goes out in its place, not ahead of what waits before it */
	n_sent = 0;
	port_room = 10 * FF802USB_MSG_LEN;
	check("resend starts", be->sync(h, &s, true), 0);
	s.dev_cmd[0] = 0x3d200002;
	send_all(be, h, &s, false, 500);
	check("the resend with the change", n_sent, full);
	check("the changed setting in its slot", sent[full - 3].word, ff802_parity(0x3d200002));

	/* the meter read asks for the levels and sends the keep-alive, counting 0 to 15 */
	n_sent = 0;
	for (int i = 0; i < 17; i++)
		check("no levels have come", be->meters(h, &s), -ETIMEDOUT);
	check("two messages a read", n_sent, 34);
	check("the level request", sent[0].subid, 2);
	check("then the keep-alive", sent[1].word, ff802_parity((uint32_t)FF802USB_REG_SERIAL << 16));
	check("counting up", sent[31].word, ff802_parity((uint32_t)FF802USB_REG_SERIAL << 16 | 15));
	check("and around", sent[33].word, ff802_parity((uint32_t)FF802USB_REG_SERIAL << 16));
	be->destroy(h);
}

int main(void)
{
	uint8_t msg[FF802USB_MSG_LEN];

	ffusb_port = (struct ffusb_port){ fake_write, fake_room };
	check_sends(&ofm_backend_ff802usb, &ff802usb_model);
	check_sends(&ofm_backend_ucx2, &ucx2usb_model);

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
	/* the UCX II's input 1 mute off is register 0 = 0. The GUI sends it as 0x80000000 (with its
	 * parity bit) because a 0 word is an empty slot; on the wire it is the same message */
	static const uint8_t in1_mute[] = { 0xf0, 0x00, 0x20, 0x0d, 0x10, 0x00,
					    0x00, 0x00, 0x00, 0x00, 0x08, 0xf7 };
	ff802usb_reg_msg(0x80000000, msg);
	check_bytes("input 1 mute msg", msg, in1_mute, sizeof(in1_mute));
	ff802usb_reg_msg(0, msg);
	check_bytes("register 0 = 0 msg", msg, in1_mute, sizeof(in1_mute));
	check("parity of 0", ff802_parity(0), 0x80000000);
	check("parity is idempotent", ff802_parity(0x80c00000), 0x80c00000);

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
	struct ff802usb_info info = { .m = &ff802usb_model, .clock = -1, .rate = -1 };
	static struct ofm_shm s;
	uint8_t pkt[7 + 5 * 3 * 30];
	memcpy(pkt, (uint8_t[]){ 0xf0, 0x00, 0x20, 0x0d, 0x10, 0x00 }, 6);
	put_word(pkt + 6, 0x3d200003);
	put_word(pkt + 11, 0x3d210005);
	pkt[16] = 0xf7;
	/* link health: no reply until the unit sends something, however long that takes */
	check("link: opened", ffusb_link_state(&info, 0), DFU_STATE_NO_REPLY);
	check("link: never answered", ffusb_link_state(&info, 10 * FFUSB_REPLY_TIMEOUT_MS),
	      DFU_STATE_NO_REPLY);
	pkt[3] = 0x0e;   /* not RME's header: not a reply */
	check("foreign sysex", ff802usb_handle_sysex(pkt, 17, &info, &s), -1);
	check("foreign sysex is no reply", info.replies, 0);
	pkt[3] = 0x0d;
	check("subid", ff802usb_handle_sysex(pkt, 17, &info, &s), 0);
	check("clock", info.clock, 3);
	check("rate", info.rate, 5);
	check("replies", info.replies, 1);
	check("link: answered", ffusb_link_state(&info, 0), DFU_STATE_ACTIVE);
	/* it has sent no levels yet, so silence says nothing: no reopen and resend */
	check("link: no levels, silent", ffusb_link_state(&info, FFUSB_REPLY_TIMEOUT_MS),
	      DFU_STATE_ACTIVE);

	/* levels: subid 4 (inputs), channel 3 at half scale; delivered in two pieces */
	memset(pkt, 0, sizeof(pkt));
	memcpy(pkt, (uint8_t[]){ 0xf0, 0x00, 0x20, 0x0d, 0x10, 0x04 }, 6);
	put_word(pkt + 6 + 5 * (3 * 2 + 2), (1u << 22) << 4);   /* peak word of channel 3 */
	pkt[sizeof(pkt) - 1] = 0xf7;
	struct ff802usb_rx rx = { 0 };
	ff802usb_rx_feed(&rx, pkt, 100, &info, &s);
	ff802usb_rx_feed(&rx, pkt + 100, sizeof(pkt) - 100, &info, &s);
	check("level packets", info.level_packets, 1);
	check("level packets are replies", info.replies, 2);
	/* once levels have come, they come all the time: 3 s without any is a dead link */
	check("link: levels", ffusb_link_state(&info, FFUSB_REPLY_TIMEOUT_MS - 1), DFU_STATE_ACTIVE);
	check("link: levels stopped", ffusb_link_state(&info, FFUSB_REPLY_TIMEOUT_MS), -ENODATA);
	if (fabsf(s.peak_src[2] - 0.5f) > 1e-6f || s.peak_src[1] != 0.0f) {
		fprintf(stderr, "input meters: %g %g\n", s.peak_src[1], s.peak_src[2]);
		failed = 1;
	}

	/* output volume: the 802's channel commands; the UCX II's 0x0500 + 0x40 * output */
	check("802 vol", ffusb_vol_cmd(&ff802usb_model, 0, -60), 0x1e00ffc4);
	check("802 vol out 3", ffusb_vol_cmd(&ff802usb_model, 2, 0), 0x20000000);
	check("UCX II vol out 3", ffusb_vol_cmd(&ucx2usb_model, 2, -650), 0x0580fd76);

	/* UCX II: clock registers 0x3064/0x3065, 20 inputs */
	info = (struct ff802usb_info){ .m = &ucx2usb_model, .clock = -1, .rate = -1 };
	memset(&s, 0, sizeof(s));
	memset(pkt, 0, sizeof(pkt));
	memcpy(pkt, (uint8_t[]){ 0xf0, 0x00, 0x20, 0x0d, 0x10, 0x00 }, 6);
	put_word(pkt + 6, 0x30640004);
	put_word(pkt + 11, 0x30650001);
	pkt[16] = 0xf7;
	ff802usb_handle_sysex(pkt, 17, &info, &s);
	check("UCX II clock", info.clock, 4);
	check("UCX II rate", info.rate, 1);
	memset(pkt, 0, sizeof(pkt));
	memcpy(pkt, (uint8_t[]){ 0xf0, 0x00, 0x20, 0x0d, 0x10, 0x04 }, 6);
	put_word(pkt + 6 + 5 * (3 * 25 + 2), (1u << 22) << 4);  /* past the UCX II's inputs */
	pkt[sizeof(pkt) - 1] = 0xf7;
	ff802usb_handle_sysex(pkt, sizeof(pkt), &info, &s);
	check("UCX II ignores channel 26", s.peak_src[25] != 0.0f, 0);

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
