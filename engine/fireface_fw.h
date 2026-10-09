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
 * Mixer sources 0-29 are the hardware inputs, 30/31 the FX returns, 32-61 playback, which is
 * the shared-memory source numbering. Gains: 0 = off, else 0x8000 | (linear * 0x1000), so
 * 0x9000 = unity and 0xa000 = +6 dB (the Digiface's encoding for gains >= 0.5).
 * Output volume: value = dB * 10, -650..60.
 *
 * Fireface 800 ("former" protocol family; all registers little-endian, written as blocks):
 *
 *   0x0000'8008'0000  write block     mixer: one block of 64 quadlets per output (28 outputs),
 *                                     quadlets 0-27 the hardware inputs, 32-59 playback
 *   0x0000'8008'1f80  write block     output volumes, one quadlet per output
 *   0x0000'fc88'f014  write block     configuration, 3 quadlets (write only)
 *   0x0000'801c'0000  read block      status, 2 quadlets (lock/sync, configured clock and rate)
 *   0x0000'8010'0000  read block      meters, 1008 bytes; the last 84 quadlets are inputs,
 *                                     playback and outputs, full scale 0x7fffff00
 *
 * Inputs 0-9 analog, 10/11 S/PDIF, 12-19 ADAT 1, 20-27 ADAT 2; outputs likewise, with 8/9 the
 * phones. Gains and volumes: linear * 0x8000, so 0 = off, 0x8000 = unity, 0x10000 = +6 dB.
 * The shared-memory configuration: dev_config != 0 means send dev_cmd[0..2] as the three
 * configuration quadlets.
 */
#ifndef OPENFACE_MIXER_FIREFACE_FW_H
#define OPENFACE_MIXER_FIREFACE_FW_H

#include <stdint.h>

#include "shm_layout.h"

#define FF802_N_IN    30
#define FF802_N_OUT   30
#define FF802_N_SRC   62   /* inputs, FX returns, playback */
#define FF802_METER_CHUNK 392

#define FF800_N_IN    28
#define FF800_N_OUT   28
#define FF800_N_PLAY  28
#define FF800_AVAIL   32   /* mixer block: inputs from quadlet 0, playback from quadlet 32 */
#define FF800_MIXER_BLOCK (2 * FF800_AVAIL)
#define FF800_N_CONFIG 3
#define FF800_GAIN_ZERO 0x8000
#define FF800_GAIN_MAX  0x10000
#define FF800_METER_LEN (8 * (FF800_N_IN + 2 * FF800_N_OUT) + \
			 4 * (FF800_N_IN + FF800_N_PLAY + FF800_N_OUT))

/* Exposed for tests. */
uint16_t ff802_encode_gain(float lin);
int16_t ff802_out_vol(float lin);
uint32_t ff802_parity(uint32_t cmd);
uint32_t ff802_mixer_cmd(int out, int src, uint16_t gain);
uint32_t ff802_vol_cmd(int out, int16_t vol);
uint32_t ff802_status_rate(uint32_t status);
/* Raises the peaks in s from one meter chunk. Returns the chunk's tag. */
uint32_t ff802_decode_meter_chunk(const uint8_t *chunk, struct ofm_shm *s);

uint32_t ff800_encode_gain(float lin);
/* Fills one output's mixer block from s. Returns the crosspoints in use. */
int ff800_mixer_block(const struct ofm_shm *s, int out, uint32_t block[FF800_MIXER_BLOCK]);
/* Sample rate from the second status quadlet (configured rate, as the kernel driver reads it). */
uint32_t ff800_status_rate(uint32_t status1);
/* Raises the peaks in s from a FF800_METER_LEN meter read. */
void ff800_decode_meters(const uint8_t *raw, struct ofm_shm *s);

#endif
