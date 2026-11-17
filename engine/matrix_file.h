/*
 * matrix.bin: the mix the engine starts with while the GUI is not running. The GUI writes it
 * with every save (config.matrix_file_bytes).
 */
#ifndef OPENFACE_MIXER_MATRIX_FILE_H
#define OPENFACE_MIXER_MATRIX_FILE_H

#include "shm_layout.h"

/* Loads the mix and device settings in path into s. Returns 0, -ENOENT if there is no file, or
 * -EINVAL if it is not a complete matrix.bin in a format this engine knows; s is then left as
 * it was, so nothing from such a file can reach the hardware. */
int ofm_matrix_load(struct ofm_shm *s, const char *path);

/* The mix of a fresh install: playback n to output n, output masters at 0 dB, no device
 * settings (the same as model.default_state() in the GUI). */
void ofm_matrix_default(struct ofm_shm *s);

#endif
