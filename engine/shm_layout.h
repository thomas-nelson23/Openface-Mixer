/*
 * Shared-memory contract between openface-mixer-engine (C) and the GUI (Python).
 *
 * The engine creates /dev/shm/openface-mixer-<uid> and maps this struct into it.
 * The GUI writes gain[][] and out_gain[], which the engine loads into the Digiface's DSP
 * mixer, and reads/clears the peak meters, which the engine fills from the device.
 * If you change anything here, bump OFM_SHM_VERSION and update
 * the HDR/OFF constants in openface_mixer/engine.py to match.
 */
#ifndef OPENFACE_MIXER_SHM_LAYOUT_H
#define OPENFACE_MIXER_SHM_LAYOUT_H

#include <stdint.h>

#define OFM_N_IN    32                      /* Digiface hardware inputs (1x speed) */
#define OFM_N_PLAY  34                      /* playback channels from the computer */
#define OFM_N_SRC   (OFM_N_IN + OFM_N_PLAY) /* mixer sources: inputs then playback */
#define OFM_N_OUT   34                      /* Digiface hardware outputs (32 ADAT + phones) */

#define OFM_SHM_MAGIC   0x584d464fu /* "OFMX" in little-endian byte order */
#define OFM_SHM_VERSION 3

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
	uint32_t sink_linked;    /* 1 while the playback sink is linked to Digiface playback 1/2 */
	uint32_t reserved[5];

	/* Written by the GUI: linear gain from source k to output channel o.
	 * Pan and source mute are folded in; the output master is separate. */
	float gain[OFM_N_OUT][OFM_N_SRC];

	/* Written by the GUI: output master level per channel (linear, 0 = muted). */
	float out_gain[OFM_N_OUT];

	/* Max-hold peak meters (linear). The engine raises them, the GUI reads and zeroes them. */
	float peak_src[OFM_N_SRC];
	float peak_out[OFM_N_OUT];
};

#endif
