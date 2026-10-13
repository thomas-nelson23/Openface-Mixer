/*
 * RME Fireface DSP mixers over FireWire (Linux firewire-core character device /dev/fw*).
 *
 * The kernel's snd-fireface driver streams audio and leaves the DSP (TotalMix) to userspace.
 * We open the interface's FireWire node and send asynchronous transactions, as
 * snd-firewire-ctl-services does (protocols in docs/HARDWARE.md, openface_mixer/fireface802.py
 * and openface_mixer/fireface800.py).
 *
 * Fireface 802 ("latter" protocol family):
 *
 *   0xffff'0000'0014  write quadlet   configuration (clock source, optical/AES options, MIDI)
 *   0xffff'0000'001c  write quadlet   DSP command, little-endian, odd parity in bit 31:
 *                                       (ch << 24) | (cmd << 16) | value        channel setting
 *                                       0x40000000 | ((0x40 * out + src) << 16) | gain   mixer
 *   0x0000'801c'0000  read quadlet    sync status (rate, clock source, lock/sync per input)
 *   0xffff'ff00'0000  read block      meters: 392-byte chunks, one per read, cycling through
 *                                     outputs (tag 0x11111111), playback (0x33333333), inputs
 *                                     (0x66666666) and two others. Peaks are 32-bit words at
 *                                     byte 256 + 4 * channel, full scale 0x07fffff0.
 *
 * Mixer sources 0-29 are the hardware inputs, 30/31 the FX returns, 32-61 playback (shared
 * memory sources OFM_N_IN and up, see ff802_shm_source). Gains: 0 = off, else
 * 0x8000 | (linear * 0x1000), so 0x9000 = unity and 0xa000 = +6 dB (the Digiface's encoding for gains >= 0.5).
 * Output volume: value = dB * 10, -650..60.
 *
 * Fireface 800 and 400 ("former" protocol family; all registers little-endian, written as
 * blocks; addresses for the 800, then the 400):
 *
 *   0x0000'8008'0000  write block     mixer: one block per output (28 / 18 outputs) of 64 / 36
 *                                     quadlets, the hardware inputs from quadlet 0, playback
 *                                     from quadlet 32 / 18
 *   0x0000'8008'1f80  write block     output volumes, one quadlet per output (400: '0f80)
 *   0x0000'fc88'f014  write block     configuration, 3 quadlets, write only (400: '8010'0514)
 *   0x0000'801c'0000  read block      status, 2 quadlets (lock/sync, configured clock and rate)
 *   0x0000'8010'0000  read block      meters: 8 bytes per input, playback and output channel,
 *                                     then the same channels as quadlets (the ones used), full
 *                                     scale 0x7fffff00
 *   0x0000'801c'0180  write quadlet   400 only: amplifier, (channel << 16) | value. Channels
 *                                     0/1 mic gain (0-65 dB), 2/3 line gain (0-36, half dB),
 *                                     4 + n output n's analog volume (0x3f = -57 dB .. 0 = +6 dB)
 *
 * 800: inputs 0-9 analog, 10/11 S/PDIF, 12-19 ADAT 1, 20-27 ADAT 2; outputs likewise, with 8/9
 * the phones. 400: inputs 0-7 analog, 8/9 S/PDIF, 10-17 ADAT; outputs likewise, 6/7 the phones.
 * Gains and volumes: linear * 0x8000, so 0 = off, 0x8000 = unity, 0x10000 = +6 dB.
 * The shared-memory configuration: dev_config != 0 means send dev_cmd[0..2] as the three
 * configuration quadlets; on the 400, dev_cmd[3..6] are the input gains' amplifier commands
 * with bit 31 set (FORMER_AMP_VALID).
 */
#ifndef OPENFACE_MIXER_FIREFACE_FW_H
#define OPENFACE_MIXER_FIREFACE_FW_H

#include <stddef.h>
#include <stdint.h>

#include "shm_layout.h"

#define FF802_N_IN    30
#define FF802_N_OUT   30
#define FF802_N_SRC   62   /* inputs, FX returns, playback */
#define FF802_PLAY_SRC 32  /* mixer source of playback 1 */
#define FF802_METER_CHUNK 392

#define FORMER_MAX_CH    28   /* the 800's inputs, playback and outputs */
#define FORMER_MAX_BLOCK 64   /* quadlets in the 800's mixer block */
#define FORMER_N_CONFIG  3
#define FORMER_N_AMP_IN  4    /* the 400's input gain amplifier commands, after the config */
#define FORMER_AMP_VALID 0x80000000u
#define FORMER_GAIN_ZERO 0x8000
#define FORMER_GAIN_MAX  0x10000

/* One model of the former family. */
struct ff_former_model {
	unsigned long unit_version;   /* SND_FF_UNIT_VERSION_* in the kernel driver */
	int n_ch;                     /* inputs, playback and outputs */
	int avail;                    /* mixer block quadlet of playback 1; the block is twice that */
	uint64_t output;              /* output volume block */
	uint64_t config;              /* configuration (3 quadlets, write only) */
	uint64_t amp;                 /* amplifier commands (the 400), or 0 */
};
extern const struct ff_former_model ff800_model, ff400_model;

/* Exposed for tests. */
uint16_t ff802_encode_gain(float lin);
int16_t ff802_out_vol(float lin);
uint32_t ff802_parity(uint32_t cmd);
/* Shared-memory source of 802 mixer source src. */
int ff802_shm_source(int src);
uint32_t ff802_mixer_cmd(int out, int src, uint16_t gain);
uint32_t ff802_vol_cmd(int out, int16_t vol);
uint32_t ff802_status_rate(uint32_t status);
/* Raises the peaks in s from one meter chunk. Returns the chunk's tag. */
uint32_t ff802_decode_meter_chunk(const uint8_t *chunk, struct ofm_shm *s);

uint32_t former_encode_gain(float lin);
/* Fills one output's mixer block (2 * avail quadlets) from s. Returns the crosspoints in use. */
int former_mixer_block(const struct ff_former_model *m, const struct ofm_shm *s, int out,
		       uint32_t *block);
/* Sample rate from the second status quadlet (configured rate, as the kernel driver reads it). */
uint32_t former_status_rate(uint32_t status1);
/* Bytes of one meter read. */
size_t former_meter_len(const struct ff_former_model *m);
/* Raises the peaks in s from a former_meter_len() meter read. */
void former_decode_meters(const struct ff_former_model *m, const uint8_t *raw, struct ofm_shm *s);
/* The 400's output amplifier value (0x3f = -57 dB, mute .. 0 = +6 dB) for a volume quadlet. */
uint8_t former_amp_out(uint32_t vol);

#endif
