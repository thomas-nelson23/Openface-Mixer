/* Unit test for reading matrix.bin: known formats load, everything else is refused whole and
 * leaves the mix untouched (no device needed). Run via `make test`. */
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "../engine/matrix_file.h"

#define N_GAINS  (OFM_N_OUT * OFM_N_SRC)
#define OLD_OUT  34
#define OLD_SRC  66
#define OLD_IN   32

static int failed;
static char path[] = "/tmp/ofm-matrix-test-XXXXXX";
static struct ofm_shm shm, before;

static void expect(int ok, const char *what)
{
	if (!ok) {
		fprintf(stderr, "test_matrix_file: %s\n", what);
		failed = 1;
	}
}

/* a file buffer that the cases below build up */
static uint8_t buf[1 << 18];
static size_t len;

static void put(const void *data, size_t n)
{
	memcpy(buf + len, data, n);
	len += n;
}

static void put_u32(uint32_t v)
{
	put(&v, sizeof(v));
}

static void put_floats(size_t n, float value)
{
	for (size_t i = 0; i < n; i++)
		put(&value, sizeof(value));
}

static void start(const char *magic)
{
	len = 0;
	if (magic)
		put(magic, 4);
}

static int load(void)
{
	FILE *f = fopen(path, "wb");
	if (f == NULL || fwrite(buf, 1, len, f) != len || fclose(f) != 0) {
		perror(path);
		exit(1);
	}
	memset(&shm, 0x5a, sizeof(shm));
	before = shm;
	return ofm_matrix_load(&shm, path);
}

static void refused(const char *what)
{
	expect(load() == -EINVAL, what);
	expect(memcmp(&shm, &before, sizeof(shm)) == 0, "a refused file changed the mix");
}

/* a whole current file: every gain 0.25, every output master 0.5 */
static void current_file(void)
{
	start("OFM4");
	put_floats(N_GAINS, 0.25f);
	put_floats(OFM_N_OUT, 0.5f);
}

static void device_block(uint32_t count, uint32_t written)
{
	put("OFD2", 4);
	put_u32(0x1234);
	put_u32(count);
	for (uint32_t i = 0; i < written; i++)
		put_u32(i + 1);
}

static void test_default(void)
{
	memset(&shm, 0x5a, sizeof(shm));
	ofm_matrix_default(&shm);
	int ok = shm.dev_config == 0;
	for (int o = 0; o < OFM_N_OUT; o++) {
		ok = ok && shm.out_gain[o] == 1.0f;
		for (int k = 0; k < OFM_N_SRC; k++)
			ok = ok && shm.gain[o][k] == (k == OFM_N_IN + o ? 1.0f : 0.0f);
	}
	for (int i = 0; i < OFM_N_DEV_CMD; i++)
		ok = ok && shm.dev_cmd[i] == 0;
	expect(ok, "the default is not playback n to output n at 0 dB with no device settings");
}

static void test_known_formats(void)
{
	current_file();
	expect(load() == 0, "OFM4 refused");
	expect(shm.gain[35][71] == 0.25f && shm.out_gain[35] == 0.5f, "OFM4 gains");
	expect(shm.dev_config == 0 && shm.dev_cmd[0] == 0, "no device block must clear the settings");

	current_file();
	device_block(OFM_N_DEV_CMD, OFM_N_DEV_CMD);
	expect(load() == 0, "OFM4 with OFD2 refused");
	expect(shm.gain[0][0] == 0.25f && shm.dev_config == 0x1234 && shm.dev_cmd[0] == 1 &&
	       shm.dev_cmd[OFM_N_DEV_CMD - 1] == OFM_N_DEV_CMD, "OFD2 settings");

	/* the old 34 x 66 layout: its playback columns move up to source 36 */
	for (int with_mode = 0; with_mode < 2; with_mode++) {
		start(with_mode ? "OFM2" : "OFM3");
		if (with_mode)
			put_u32(1);
		for (int o = 0; o < OLD_OUT; o++)
			for (int k = 0; k < OLD_SRC; k++)
				put_floats(1, k < OLD_IN ? 0.25f : 0.75f);
		put_floats(OLD_OUT, 0.5f);
		put("OFD1", 4);
		put_u32(7);
		for (uint32_t i = 0; i < 512; i++)
			put_u32(i + 1);
		expect(load() == 0, "old format refused");
		expect(shm.gain[33][31] == 0.25f && shm.gain[33][32] == 0.0f &&
		       shm.gain[33][OFM_N_IN] == 0.75f && shm.gain[33][OFM_N_IN + 33] == 0.75f &&
		       shm.gain[33][OFM_N_IN + 34] == 0.0f && shm.gain[34][0] == 0.0f &&
		       shm.out_gain[33] == 0.5f && shm.out_gain[34] == 0.0f, "old layout not moved");
		expect(shm.dev_config == 7 && shm.dev_cmd[511] == 512 && shm.dev_cmd[512] == 0,
		       "OFD1 settings");
	}
}

static void test_refused(void)
{
	start(NULL);
	refused("an empty file was loaded");

	/* the first version's format: a bare 34 x 66 gain table */
	start(NULL);
	put_floats(OLD_OUT * OLD_SRC, 1.0f);
	refused("a version 1 gain table was loaded");

	start("OFM5");
	put_floats(N_GAINS + OFM_N_OUT, 0.25f);
	refused("an unknown format was loaded");

	current_file();
	len -= 4;
	refused("a file cut short in the output gains was loaded");
	len = 4 + 100;
	refused("a file cut short in the gains was loaded");

	current_file();
	put_u32(0);
	refused("a file with something after the gains was loaded");

	current_file();
	device_block(OFM_N_DEV_CMD, OFM_N_DEV_CMD - 1);
	refused("a file cut short in the device settings was loaded");

	current_file();
	device_block(OFM_N_DEV_CMD + 1, OFM_N_DEV_CMD + 1);
	refused("a device block that is too long was loaded");

	current_file();
	device_block(OFM_N_DEV_CMD, OFM_N_DEV_CMD);
	put("x", 1);
	refused("a file with something after the device settings was loaded");

	current_file();
	float nan = NAN;
	memcpy(buf + 4 + 40, &nan, sizeof(nan));
	refused("a gain that is not a number was loaded");

	unlink(path);
	memset(&shm, 0x5a, sizeof(shm));
	before = shm;
	expect(ofm_matrix_load(&shm, path) == -ENOENT, "a missing file is not -ENOENT");
	expect(memcmp(&shm, &before, sizeof(shm)) == 0, "a missing file changed the mix");
}

int main(void)
{
	int fd = mkstemp(path);
	if (fd < 0) {
		perror("mkstemp");
		return 1;
	}
	close(fd);
	test_default();
	test_known_formats();
	test_refused();
	unlink(path);
	if (!failed)
		printf("test_matrix_file: ok\n");
	return failed;
}
