/*
 * RME Fireface 802 DSP mixer over FireWire (Linux firewire-core character device /dev/fw*).
 *
 * The kernel's snd-fireface driver streams audio and leaves the DSP (TotalMix FX) to
 * userspace. We open the 802's FireWire node and send asynchronous transactions, as
 * snd-firewire-ctl-services does (protocol in docs/HARDWARE.md and openface_mixer/fireface802.py):
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
 */
#ifndef OPENFACE_MIXER_FIREFACE_FW_H
#define OPENFACE_MIXER_FIREFACE_FW_H

#include <stdint.h>

#include "shm_layout.h"

#define FF802_N_IN    30
#define FF802_N_OUT   30
#define FF802_N_SRC   62   /* inputs, FX returns, playback */
#define FF802_METER_CHUNK 392

/* Exposed for tests. */
uint16_t ff802_encode_gain(float lin);
int16_t ff802_out_vol(float lin);
uint32_t ff802_parity(uint32_t cmd);
uint32_t ff802_mixer_cmd(int out, int src, uint16_t gain);
uint32_t ff802_vol_cmd(int out, int16_t vol);
uint32_t ff802_status_rate(uint32_t status);
/* Raises the peaks in s from one meter chunk. Returns the chunk's tag. */
uint32_t ff802_decode_meter_chunk(const uint8_t *chunk, struct ofm_shm *s);

#endif
