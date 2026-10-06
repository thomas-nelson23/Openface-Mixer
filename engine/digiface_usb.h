/*
 * Direct USB control of the RME Digiface USB's DSP mixer (hardware mixing, zero latency).
 *
 * snd-usb-audio binds only interface 0 (audio streaming) and turns the DSP mixer off at boot.
 * We claim interface 1 and talk to the mixer ourselves. Protocol, from Asahi Lina's rmectl
 * (https://github.com/hoshinolina/rmectl), see docs/HARDWARE.md:
 *
 *   ctl req 16 / 18      control registers 1 / 2 (wValue = bits, wIndex = mask)
 *   ctl req 17 (in)      16-byte status; word 3 mirrors control registers 1 (low) and 2 (high)
 *   ctl req 21           output fader: wValue = gain, wIndex = 0x100 + output channel
 *   bulk out EP 0x0B     mixer commands, little-endian 32-bit words:
 *                          (node << 16) | gain                       set node gain
 *                          0x40000000 | (node << 16) | (dst << 9) | src  route node src -> dst
 *                          0xC000FFFF | (node << 16)                 reset node
 *   bulk in EP 0x84      level meters
 *
 * The mixer has 2048 nodes; each routes one source (input n, or 0x100 + playback n) to one
 * output channel with a gain. Gains are 0x8000 = unity, max 0x10000 (+6 dB); values >= 0x4000
 * are stored >> 3 with bit 15 set.
 */
#ifndef OPENFACE_MIXER_DIGIFACE_USB_H
#define OPENFACE_MIXER_DIGIFACE_USB_H

#include <stdbool.h>
#include <stdint.h>

#include "shm_layout.h"

#define DFU_MAX_NODES 2048

/* values for ofm_shm.hw_state */
enum {
	DFU_STATE_OFF = 0,        /* software mode: hardware mixer not in use */
	DFU_STATE_NO_DEVICE,      /* no Digiface on the bus */
	DFU_STATE_NO_ACCESS,      /* device present but /dev/bus/usb not writable (udev rule) */
	DFU_STATE_BUSY,           /* interface 1 claimed by someone else */
	DFU_STATE_ACTIVE,         /* hardware mixer running our matrix */
	DFU_STATE_NO_NODES,       /* active, but the matrix needs more than DFU_MAX_NODES */
	DFU_STATE_ERROR,          /* USB error; will retry */
};

struct dfu;

struct dfu *dfu_new(void);
void dfu_free(struct dfu *d);

/* Opens the device and claims the mixer interface. Returns a DFU_STATE_* value. */
int dfu_open(struct dfu *d);
void dfu_close(struct dfu *d);
bool dfu_is_open(const struct dfu *d);

/* Reads the 4 status words. Returns 0 or a negative libusb error. */
int dfu_read_status(struct dfu *d, uint32_t status[4]);
bool dfu_status_mixer_enabled(const uint32_t status[4]);

int dfu_set_mixer_enabled(struct dfu *d, bool enabled);

/*
 * Brings the hardware in line with the given matrix (linear gains, src = inputs then
 * playback) and output gains. With full set, every node and fader is rewritten, as after a
 * device reset; otherwise only what changed since the last call is sent.
 * Returns 0, -ENOSPC if nodes ran out (the matrix is applied partially), or a libusb error.
 */
int dfu_sync(struct dfu *d, const float gain[OFM_N_OUT][OFM_N_SRC],
	     const float out_gain[OFM_N_OUT], bool full);

int dfu_nodes_used(const struct dfu *d);

/* Exposed for tests. */
uint16_t dfu_encode_gain(float lin);

/*
 * Level meters. dfu_read_levels() blocks for up to timeout_ms on the levels endpoint and
 * copies the raw packet into buf; returns the byte count or a negative libusb error.
 */
int dfu_read_levels(struct dfu *d, uint8_t *buf, int len, unsigned int timeout_ms);

#endif
