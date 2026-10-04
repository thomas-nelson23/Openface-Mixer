/*
 * Shared-memory contract between openface-mixer-engine (C) and the GUI (Python).
 *
 * The engine creates /dev/shm/openface-mixer-<uid> and maps this struct into it.
 * The GUI writes gain[][] and reads/clears the peak meters.
 * If you change anything here, bump OFM_SHM_VERSION and update
 * the HDR/OFF constants in openface_mixer/engine.py to match.
 */
#ifndef OPENFACE_MIXER_SHM_LAYOUT_H
#define OPENFACE_MIXER_SHM_LAYOUT_H

#include <stdint.h>

#define OFM_N_IN    32                      /* Digiface hardware inputs (1x speed) */
#define OFM_N_PLAY  34                      /* software playback channels */
#define OFM_N_SRC   (OFM_N_IN + OFM_N_PLAY) /* mixer sources: inputs then playback */
#define OFM_N_OUT   34                      /* Digiface hardware outputs (32 ADAT + phones) */

#define OFM_SHM_MAGIC   0x584d464fu /* "OFMX" in little-endian byte order */
#define OFM_SHM_VERSION 1

/* Header is exactly 64 bytes; all fields are written by the engine unless noted. */
struct ofm_shm {
	uint32_t magic;
	uint32_t version;
	uint32_t n_src;
	uint32_t n_out;
	uint32_t heartbeat;      /* incremented every audio cycle */
	uint32_t in_connected;   /* in_* ports that currently have a link */
	uint32_t play_connected; /* play_* ports that currently have a link */
	uint32_t out_connected;  /* out_* ports that currently have a link */
	uint32_t rate;           /* graph sample rate */
	uint32_t quantum;        /* samples per cycle */
	uint32_t engine_pid;     /* 0 after a clean shutdown */
	uint32_t reserved[5];

	/* Written by the GUI: final linear gain from source k to output o.
	 * Pan, mute and output master level are already folded in. */
	float gain[OFM_N_OUT][OFM_N_SRC];

	/* Max-hold peak meters (linear). The engine raises them, the GUI reads and zeroes them. */
	float peak_src[OFM_N_SRC];
	float peak_out[OFM_N_OUT];
};

#endif
