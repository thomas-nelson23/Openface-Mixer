/*
 * One supported interface, as the engine sees it: how to reach its DSP mixer, keep it in line
 * with the shared-memory mix, and read its meters. openface-mixer-engine.c picks one with
 * --device and runs it from its main-loop timer and meter thread.
 */
#ifndef OPENFACE_MIXER_BACKEND_H
#define OPENFACE_MIXER_BACKEND_H

#include <stdbool.h>

#include "shm_layout.h"

struct ofm_backend {
	const char *key;              /* --device value: "digiface", "ff802", "ff800" or "raydat" */
	const char *name;             /* for log messages */
	const char *out_node_prefix;  /* PipeWire node name prefix of its ALSA playback device */
	const char *out_node_card;    /* or, for PCI cards whose node names carry only the slot,
	                               * the prefix of the playback node's ALSA card name */
	unsigned int meter_us;        /* meter poll interval */

	void *(*create)(void);
	void (*destroy)(void *h);
	/* Opens the device. Returns a DFU_STATE_* value (digiface_usb.h); ACTIVE when open. */
	int (*open)(void *h);
	void (*close)(void *h);
	bool (*is_open)(void *h);
	/* Reads the device status into s (rate, dev_status). Sets *reset when the device has lost
	 * the mix (the driver reset it) and everything has to be sent again. Returns 0 or < 0. */
	int (*status)(void *h, struct ofm_shm *s, bool *reset);
	/* Sends what changed in s since the last call, or everything with full. Returns 0,
	 * -ENOSPC if the mix did not fit (applied partially), or another error < 0. */
	int (*sync)(void *h, struct ofm_shm *s, bool full);
	/* Mixer crosspoints in use. */
	int (*routes)(void *h);
	/* Reads the meters and raises the peaks in s. Returns 0 or < 0. */
	int (*meters)(void *h, struct ofm_shm *s);
};

/* Max-hold for the shared-memory peak meters. */
static inline void raise_peak(float *dst, float v)
{
	if (v > *dst)
		*dst = v;
}

extern const struct ofm_backend ofm_backend_digiface;
extern const struct ofm_backend ofm_backend_ff802;
extern const struct ofm_backend ofm_backend_ff800;
extern const struct ofm_backend ofm_backend_raydat;

#endif
