/*
 * Shared-memory contract between openface-mixer-engine (C) and the GUI (Python).
 *
 * The engine creates /dev/shm/openface-mixer-<uid> (Digiface) or /dev/shm/openface-mixer-<device>-<uid>
 * and maps this struct into it. The GUI writes gain[][] and out_gain[], which the engine loads
 * into the interface's DSP mixer, and reads/clears the peak meters, which the engine fills from
 * the device. Channel counts are the largest any device has (the RayDAT's); each device uses a
 * subset.
 * If you change anything here, bump OFM_SHM_VERSION and update
 * the HDR/OFF constants in openface_mixer/engine.py to match.
 */
#ifndef OPENFACE_MIXER_SHM_LAYOUT_H
#define OPENFACE_MIXER_SHM_LAYOUT_H

#include <stdint.h>

#define OFM_N_IN    36                      /* hardware inputs (RayDAT: 36 at 1x speed) */
#define OFM_N_PLAY  36                      /* playback channels from the computer */
#define OFM_N_SRC   (OFM_N_IN + OFM_N_PLAY) /* mixer sources: inputs then playback */
#define OFM_N_OUT   36                      /* hardware outputs */

#define OFM_SHM_MAGIC   0x584d464fu /* "OFMX" in little-endian byte order */
#define OFM_N_DEV_CMD 512                   /* device setting command slots */

#define OFM_SHM_VERSION 5

/* Header is exactly 64 bytes; all fields are written by the engine. */
struct ofm_shm {
	uint32_t magic;
	uint32_t version;
	uint32_t n_src;
	uint32_t n_out;
	uint32_t heartbeat;      /* incremented on every engine tick (20 ms) */
	uint32_t rate;           /* Digiface sample rate from its status register, 0 if unknown */
	uint32_t engine_pid;     /* 0 after a clean shutdown */
	uint32_t hw_state;       /* DFU_STATE_* (digiface_usb.h) */
	uint32_t hw_nodes;       /* hardware mixer nodes in use */
	uint32_t hw_levels;      /* 1 while the meters come from the hardware */
	uint32_t sink_linked;    /* 1 while the playback sink is linked to playback 1/2 */
	uint32_t dev_status;     /* device status word (Fireface 802: sync status register;
	                          * Fireface 800: first status quadlet) */
	uint32_t dev_status2;    /* Fireface 800: second status quadlet */
	uint32_t reserved[3];

	/* Written by the GUI: linear gain from source k to output channel o.
	 * Pan and source mute are folded in; the output master is separate. */
	float gain[OFM_N_OUT][OFM_N_SRC];

	/* Written by the GUI: output master level per channel (linear, 0 = muted). */
	float out_gain[OFM_N_OUT];

	/* Max-hold peak meters (linear). The engine raises them, the GUI reads and zeroes them. */
	float peak_src[OFM_N_SRC];
	float peak_out[OFM_N_OUT];

	/* Written by the GUI, for devices whose settings are DSP commands (Fireface 802): the
	 * configuration register (0 = leave alone) and setting commands (0 = unused slot). The engine
	 * sends the ones that changed, and all of them when it (re)connects. The Fireface 800 takes
	 * its three configuration quadlets from dev_cmd[0..2], sent while dev_config is non-zero. */
	uint32_t dev_config;
	uint32_t dev_cmd[OFM_N_DEV_CMD];
};

#endif
