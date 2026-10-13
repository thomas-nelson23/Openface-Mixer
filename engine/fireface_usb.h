/*
 * RME Fireface 802 and Fireface UCX II over USB, in Class Compliant mode.
 *
 * Over USB, Linux runs these only in Class Compliant (CC) mode, where their TotalMix FX DSP
 * listens to the second of their two USB MIDI ports, as RME's TotalMix FX for iPad uses it.
 * The commands are SysEx packets (protocol documented by oscmix, github.com/michaelforney/oscmix,
 * wiki pages "Protocol" (the UCX II, oscmix's own device) and "Devices/Fireface-802"):
 *
 *   F0 00 20 0D 10 <subid> <payload> F7
 *
 * The payload is a run of 32-bit words, each sent as five 7-bit bytes, least significant first.
 *
 *   subid 0  register write (host) or change notification (device): (reg << 16) | value, with
 *            bit 31 set when the other 31 bits have an even number of ones (odd parity overall)
 *   subid 2  host: request levels (empty payload). The device answers with subids 1-5, three
 *            words per channel: RMS low, RMS high, peak. Peak bits 4-27 are the 24-bit level.
 *            1 = inputs post FX, 2 = playback, 3 = outputs pre fader, 4 = inputs pre FX,
 *            5 = outputs post fader.
 *
 * The 802's registers in CC mode are its FireWire DSP command words (fireface_fw.h): register
 * (channel << 8) | command for channel settings, 0x4000 + 0x40 * output + source for the mixer.
 * So the engine sends the same words as over FireWire, wrapped in SysEx, plus the clock and
 * option registers 0x3d20-0x3d46, which the GUI puts in dev_cmd[] (fireface802.usb_commands()).
 *
 * The UCX II's mixer registers are the 802's (0x4000 + 0x40 * output + source, playback from
 * source 0x20, same gain encoding). Its channel settings are register (channel << 6) | setting,
 * channels 0-19 the inputs and 20-39 the outputs (output volume at 0x0500 + 0x40 * output, in
 * 0.1 dB like the 802's), and its clock and options sit at 0x3064-0x307b. The GUI's commands
 * for it come from openface_mixer/ucx2.py.
 *
 * The MIDI port is reached through the ALSA sequencer (/dev/snd/seq), which shares it with
 * PipeWire's MIDI bridge. The sequencer drops events that don't fit in the port's output buffer
 * instead of waiting, so the backend sends no more than the buffer has room for each tick (read
 * from /proc/asound/cardN/midi0) and carries the rest over.
 */
#ifndef OPENFACE_MIXER_FIREFACE_USB_H
#define OPENFACE_MIXER_FIREFACE_USB_H

#include <stddef.h>
#include <stdint.h>

#include "shm_layout.h"

#define FF802USB_MSG_LEN     12                 /* one register write */
#define FF802USB_REG_SERIAL  0x3f00             /* host keep-alive, cycling 0-15 */
#define FF802USB_STATUS_VALID 0x80000000u       /* dev_status: rate (8-15) and clock (0-7) known */

/* One interface. */
struct ffusb_model {
	const char *card;      /* start of the ALSA card and sequencer client name */
	int n_in, n_out;       /* physical inputs and outputs (playback = outputs) */
	int n_src_in;          /* mixer sources before playback (the 802's include its FX return) */
	uint16_t vol_reg;      /* output 1's volume register */
	uint16_t vol_step;     /* register step to the next output */
	uint16_t clock_reg, rate_reg;   /* clock source; sample rate (oscmix's enum on both) */
	uint32_t dump;         /* register write asking for every register */
};
extern const struct ffusb_model ff802usb_model, ucx2usb_model;

/* Exposed for tests. */
uint32_t ffusb_vol_cmd(const struct ffusb_model *m, int out, int16_t vol);
uint16_t ff802usb_encode_gain(float lin);
/* Writes the SysEx for one register write (FF802USB_MSG_LEN bytes). word: (reg << 16) | value,
 * parity is added here. */
void ff802usb_reg_msg(uint32_t word, uint8_t msg[FF802USB_MSG_LEN]);
/* Sample rate of a rate register value (0 if unknown). */
uint32_t ff802usb_rate(unsigned int value);

/* What the backend learns from the device's SysEx. */
struct ff802usb_info {
	const struct ffusb_model *m;
	int clock;       /* clock source register, -1 until reported */
	int rate;        /* sample rate register, -1 until reported */
	unsigned int level_packets;
};

/* Handles one complete SysEx message (F0 .. F7): register notifications update info, level
 * packets raise the peaks in s. Returns the sub ID, or -1 if it isn't RME's. */
int ff802usb_handle_sysex(const uint8_t *msg, size_t len, struct ff802usb_info *info,
			  struct ofm_shm *s);

/* Reassembles SysEx messages that arrive in pieces. */
struct ff802usb_rx {
	uint8_t buf[4096];
	size_t len;
	int in_sysex;
};
void ff802usb_rx_feed(struct ff802usb_rx *rx, const uint8_t *data, size_t len,
		      struct ff802usb_info *info, struct ofm_shm *s);

/* The free space of output substream sub in a /proc/asound/cardN/midiD text, or -1. */
long ff802usb_proc_avail(const char *text, int sub);

#endif
