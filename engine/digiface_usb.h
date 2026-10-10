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
 *   bulk in EP 0x84      level meters: 1024-byte frames of 128 LE 64-bit words, one frame
 *                        per transfer, cycling through inputs (0), playback (1), outputs (2).
 *                        Words 0-33: RMS, mean square * 2^55 (smoothed by the device).
 *                        Words 64-80: peaks, two channels per word (even channel in the low
 *                        32 bits), full scale 2^27. Words 100-127: the frame type repeated as
 *                        0xFFFFFFFn_FFFFFFFn. The device always has a frame ready.
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

/* the Digiface's own channels at single speed; the shared memory has room for more */
#define DFU_N_IN   32
#define DFU_N_PLAY 34
#define DFU_N_OUT  34

/* values for ofm_shm.hw_state */
enum {
	DFU_STATE_STARTING = 0,   /* engine has not looked for the device yet */
	DFU_STATE_NO_DEVICE,      /* no Digiface on the bus */
	DFU_STATE_NO_ACCESS,      /* device present but /dev/bus/usb not writable (udev rule) */
	DFU_STATE_BUSY,           /* interface 1 claimed by someone else */
	DFU_STATE_ACTIVE,         /* hardware mixer running our matrix */
	DFU_STATE_NO_NODES,       /* active, but the matrix needs more than DFU_MAX_NODES */
	DFU_STATE_ERROR,          /* USB error; will retry */
	DFU_STATE_WRONG_DRIVER,   /* the kernel driver bound to the device can't run it (RayDAT) */
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
/* Current sample rate in Hz from the status words, or 0 if not locked/unknown. */
uint32_t dfu_status_rate(const uint32_t status[4]);

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

#define DFU_METER_CHANNELS 34

struct dfu_meters {
	float peak[3][DFU_METER_CHANNELS]; /* [DFU_METER_*][channel], linear, 1.0 = full scale */
	float rms[3][DFU_METER_CHANNELS];
};

enum { DFU_METER_INPUT = 0, DFU_METER_PLAYBACK = 1, DFU_METER_OUTPUT = 2 };

/*
 * Reads one frame of each type from the level endpoint (blocks briefly).
 * Returns 0, or a negative libusb error. Safe to call from a thread other than the one doing
 * dfu_sync(), as long as dfu_close() is not called concurrently.
 */
int dfu_read_meters(struct dfu *d, struct dfu_meters *m);

/* Decodes one 1024-byte frame into m. Returns the frame type, or -1 if it is not a frame. */
int dfu_decode_meter_frame(const uint8_t *frame, struct dfu_meters *m);

#endif
