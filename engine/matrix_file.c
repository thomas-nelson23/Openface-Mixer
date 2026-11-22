/*
 * Reading matrix.bin. The file is "OFM4", gain[][] and out_gain[], then for devices with
 * settings "OFD2", dev_config, the number of command slots and dev_cmd[] ("OFD1" files: 512
 * slots, no count). "OFM3" files hold the gains at the old 34 x 66 size, which is moved into
 * place; "OFM2" files carry a mixer mode word before that, which is skipped.
 *
 * Anything else is refused as a whole: another magic (a newer format, or the first version's
 * bare gain table), a file that ends early or goes on after its last block, or a gain that is
 * not a number. The bytes of such a file never reach the hardware.
 */
#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "matrix_file.h"

#define MATRIX_MAGIC         0x344d464fu /* "OFM4" */
#define MATRIX_MAGIC_V3      0x334d464fu /* "OFM3": gains at the old 34 x 66 size */
#define MATRIX_MAGIC_V2      0x324d464fu /* "OFM2": mixer mode, then as OFM3 */
#define DEVICE_BLOCK_MAGIC   0x3244464fu /* "OFD2": dev_config, count, dev_cmd[count] */
#define DEVICE_BLOCK_MAGIC_V1 0x3144464fu /* "OFD1": dev_config, dev_cmd[512] */
#define DEVICE_BLOCK_V1_CMDS 512

/* matrix sizes before "OFM4" (32 inputs, 34 playback, 34 outputs) */
#define OLD_N_IN    32
#define OLD_N_PLAY  34
#define OLD_N_SRC   (OLD_N_IN + OLD_N_PLAY)
#define OLD_N_OUT   34

/* what a file holds, read here first so that a bad file changes nothing */
struct matrix {
	float gain[OFM_N_OUT][OFM_N_SRC];
	float out_gain[OFM_N_OUT];
	uint32_t dev_config;
	uint32_t dev_cmd[OFM_N_DEV_CMD];
};

static bool read_all(FILE *f, void *buf, size_t len)
{
	return fread(buf, len, 1, f) == 1;
}

static bool all_finite(const float *v, size_t n)
{
	for (size_t i = 0; i < n; i++)
		if (!isfinite(v[i]))
			return false;
	return true;
}

/* Reads gains and output gains at the old size into m. */
static bool read_old_gains(struct matrix *m, FILE *f)
{
	static float g[OLD_N_OUT][OLD_N_SRC];
	float out[OLD_N_OUT];
	if (!read_all(f, g, sizeof(g)) || !read_all(f, out, sizeof(out)))
		return false;
	for (int o = 0; o < OLD_N_OUT; o++) {
		memcpy(m->gain[o], g[o], OLD_N_IN * sizeof(float));
		memcpy(m->gain[o] + OFM_N_IN, g[o] + OLD_N_IN, OLD_N_PLAY * sizeof(float));
		m->out_gain[o] = out[o];
	}
	return true;
}

/* The optional device settings block; true if there is none or a whole one. */
static bool read_device_block(struct matrix *m, FILE *f)
{
	uint32_t magic, n = DEVICE_BLOCK_V1_CMDS;
	size_t got = fread(&magic, 1, sizeof(magic), f);
	if (got != sizeof(magic))
		return got == 0 && feof(f);
	if (magic != DEVICE_BLOCK_MAGIC && magic != DEVICE_BLOCK_MAGIC_V1)
		return false;
	if (!read_all(f, &m->dev_config, sizeof(m->dev_config)))
		return false;
	if (magic == DEVICE_BLOCK_MAGIC && (!read_all(f, &n, sizeof(n)) || n > OFM_N_DEV_CMD))
		return false;
	return n == 0 || read_all(f, m->dev_cmd, n * sizeof(uint32_t));
}

int ofm_matrix_load(struct ofm_shm *s, const char *path)
{
	static struct matrix m;
	FILE *f = fopen(path, "rb");
	if (f == NULL)
		return errno == ENOENT ? -ENOENT : -EINVAL;
	memset(&m, 0, sizeof(m));
	uint32_t magic = 0, mode;
	bool ok = read_all(f, &magic, sizeof(magic));
	if (ok && magic == MATRIX_MAGIC)
		ok = read_all(f, m.gain, sizeof(m.gain)) && read_all(f, m.out_gain, sizeof(m.out_gain));
	else if (ok && magic == MATRIX_MAGIC_V3)
		ok = read_old_gains(&m, f);
	else if (ok && magic == MATRIX_MAGIC_V2)
		ok = read_all(f, &mode, sizeof(mode)) && read_old_gains(&m, f);
	else
		ok = false;
	ok = ok && read_device_block(&m, f) && fgetc(f) == EOF &&
	     all_finite(&m.gain[0][0], OFM_N_OUT * OFM_N_SRC) && all_finite(m.out_gain, OFM_N_OUT);
	fclose(f);
	if (!ok)
		return -EINVAL;
	memcpy(s->gain, m.gain, sizeof(s->gain));
	memcpy(s->out_gain, m.out_gain, sizeof(s->out_gain));
	s->dev_config = m.dev_config;
	memcpy(s->dev_cmd, m.dev_cmd, sizeof(s->dev_cmd));
	return 0;
}

void ofm_matrix_default(struct ofm_shm *s)
{
	memset(s->gain, 0, sizeof(s->gain));
	for (int o = 0; o < OFM_N_OUT; o++) {
		s->gain[o][OFM_N_IN + o] = 1.0f;
		s->out_gain[o] = 1.0f;
	}
	s->dev_config = 0;
	memset(s->dev_cmd, 0, sizeof(s->dev_cmd));
}
