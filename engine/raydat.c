/*
 * The RME HDSPe RayDAT as an engine backend: hardware mixer, sample rate and meters through
 * snd-hdspm's ALSA control and hwdep devices. See raydat.h.
 */
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <sound/asound.h>
#include <sound/hdspm.h>

#include "backend.h"
#include "digiface_usb.h"   /* DFU_STATE_* */
#include "raydat.h"

#define N_SRC_RAYDAT (2 * RAYDAT_N_CH)
#define MAX_CARDS    32

struct raydat {
	int ctl;      /* /dev/snd/controlC<n>, -1 while closed */
	int hwdep;    /* /dev/snd/hwC<n>D0 */
	unsigned int mixer_numid;
	uint16_t sent[RAYDAT_N_CH][N_SRC_RAYDAT];
	int routes;
	int check_out;   /* output whose sends the next status call reads back */
};

uint16_t raydat_encode_gain(float lin)
{
	if (!(lin > 0.0f))
		return 0;
	float v = lin * RAYDAT_GAIN_UNITY + 0.5f;
	return v >= RAYDAT_GAIN_MAX ? RAYDAT_GAIN_MAX : (uint16_t)v;
}

int raydat_mixer_source(int k)
{
	return k < OFM_N_IN ? k : RAYDAT_PLAY_SRC + (k - OFM_N_IN);
}

float raydat_peak(uint32_t word)
{
	return (float)((word >> 8) & RAYDAT_PEAK_FULL) / RAYDAT_PEAK_FULL;
}

bool raydat_card_match(const char *driver, const char *name)
{
	return strcmp(driver, "HDSPM") == 0 && strncmp(name, "RME RayDAT", 10) == 0;
}

/* shared-memory source of mixer source slot j (0-35 inputs, 36-71 playback) */
static int shm_source(int j)
{
	return j < RAYDAT_N_CH ? j : OFM_N_IN + (j - RAYDAT_N_CH);
}

/* ---------- ALSA control and hwdep ---------- */

static int mixer_io(struct raydat *r, unsigned long req, int src, int dst, uint16_t *gain)
{
	struct snd_ctl_elem_value v;
	memset(&v, 0, sizeof(v));
	v.id.numid = r->mixer_numid;
	v.value.integer.value[0] = src;
	v.value.integer.value[1] = dst;
	if (req == SNDRV_CTL_IOCTL_ELEM_WRITE)
		v.value.integer.value[2] = *gain;
	if (ioctl(r->ctl, req, &v) < 0)
		return -errno;
	if (req == SNDRV_CTL_IOCTL_ELEM_READ)
		*gain = (uint16_t)v.value.integer.value[2];
	return 0;
}

static int write_gain(struct raydat *r, int src, int dst, uint16_t gain)
{
	return mixer_io(r, SNDRV_CTL_IOCTL_ELEM_WRITE, src, dst, &gain);
}

static int read_gain(struct raydat *r, int src, int dst, uint16_t *gain)
{
	return mixer_io(r, SNDRV_CTL_IOCTL_ELEM_READ, src, dst, gain);
}

/* Opens the control device of the RayDAT's card. Returns its fd, or -1 with *state set. */
static int find_card(int *card, int *state)
{
	*state = DFU_STATE_NO_DEVICE;
	for (int n = 0; n < MAX_CARDS; n++) {
		char path[32];
		snprintf(path, sizeof(path), "/dev/snd/controlC%d", n);
		int fd = open(path, O_RDWR | O_CLOEXEC);
		if (fd < 0) {
			if (errno == EACCES || errno == EPERM)
				*state = DFU_STATE_NO_ACCESS;
			continue;
		}
		struct snd_ctl_card_info info;
		memset(&info, 0, sizeof(info));
		if (ioctl(fd, SNDRV_CTL_IOCTL_CARD_INFO, &info) == 0 &&
		    raydat_card_match((const char *)info.driver, (const char *)info.name)) {
			*card = n;
			return fd;
		}
		close(fd);
	}
	return -1;
}

/* ---------- backend ---------- */

static void *create(void)
{
	struct raydat *r = calloc(1, sizeof(*r));
	if (r == NULL)
		return NULL;
	r->ctl = r->hwdep = -1;
	return r;
}

static void close_dev(void *h)
{
	struct raydat *r = h;
	if (r->hwdep >= 0)
		close(r->hwdep);
	if (r->ctl >= 0)
		close(r->ctl);
	r->ctl = r->hwdep = -1;
	r->routes = 0;
}

static void destroy(void *h)
{
	close_dev(h);
	free(h);
}

static int open_dev(void *h)
{
	struct raydat *r = h;
	int card, state;

	if (r->ctl >= 0)
		return DFU_STATE_ACTIVE;
	r->ctl = find_card(&card, &state);
	if (r->ctl < 0)
		return state;

	struct snd_ctl_elem_info info;
	memset(&info, 0, sizeof(info));
	info.id.iface = SNDRV_CTL_ELEM_IFACE_HWDEP;
	snprintf((char *)info.id.name, sizeof(info.id.name), "Mixer");
	if (ioctl(r->ctl, SNDRV_CTL_IOCTL_ELEM_INFO, &info) < 0 || info.count != 3) {
		fprintf(stderr, "openface-mixer: card %d has no hdspm Mixer control\n", card);
		close_dev(r);
		return DFU_STATE_ERROR;
	}
	r->mixer_numid = info.id.numid;

	char path[32];
	snprintf(path, sizeof(path), "/dev/snd/hwC%dD0", card);
	r->hwdep = open(path, O_RDONLY | O_CLOEXEC);
	if (r->hwdep < 0) {
		int e = errno;
		close_dev(r);
		return e == EACCES || e == EPERM ? DFU_STATE_NO_ACCESS : DFU_STATE_ERROR;
	}

	/* Writing a crosspoint back unchanged tells whether the driver accepts mixer changes now:
	 * it answers EBUSY while playback and capture belong to different processes. */
	uint16_t g = 0;
	int e = read_gain(r, 0, 0, &g);
	if (e == 0)
		e = write_gain(r, 0, 0, g);
	if (e < 0) {
		close_dev(r);
		return e == -EBUSY ? DFU_STATE_BUSY : DFU_STATE_ERROR;
	}
	return DFU_STATE_ACTIVE;
}

static bool is_open(void *h)
{
	struct raydat *r = h;
	return r->ctl >= 0;
}

static int status(void *h, struct ofm_shm *s, bool *reset)
{
	struct raydat *r = h;
	struct hdspm_config cfg;
	memset(&cfg, 0, sizeof(cfg));
	if (ioctl(r->hwdep, SNDRV_HDSPM_IOCTL_GET_CONFIG, &cfg) < 0)
		return -errno;
	s->rate = cfg.system_sample_rate;
	s->dev_status = cfg.system_clock_mode | (uint32_t)cfg.autosync_ref << 8;

	/* The mixer can't be read from the card, but the driver's copy tells whether it was
	 * reloaded (it zeroes the mixer) or someone else changed it. One output per call. */
	int o = r->check_out;
	r->check_out = (o + 1) % RAYDAT_N_CH;
	for (int j = 0; j < N_SRC_RAYDAT; j++) {
		uint16_t g = 0;
		int e = read_gain(r, raydat_mixer_source(shm_source(j)), o, &g);
		if (e < 0)
			return e;
		if (g != r->sent[o][j]) {
			*reset = true;
			break;
		}
	}
	return 0;
}

static int sync_mix(void *h, struct ofm_shm *s, bool full)
{
	struct raydat *r = h;
	int routes = 0;

	for (int o = 0; o < RAYDAT_N_CH; o++) {
		float master = s->out_gain[o];
		for (int j = 0; j < N_SRC_RAYDAT; j++) {
			int k = shm_source(j);
			uint16_t g = raydat_encode_gain(s->gain[o][k] * master);
			if (g)
				routes++;
			if (!full && g == r->sent[o][j])
				continue;
			int e = write_gain(r, raydat_mixer_source(k), o, g);
			if (e < 0)
				return e;
			r->sent[o][j] = g;
		}
	}
	r->routes = routes;
	return 0;
}

static int routes(void *h)
{
	struct raydat *r = h;
	return r->routes;
}

static int meters(void *h, struct ofm_shm *s)
{
	struct raydat *r = h;
	struct hdspm_peak_rms m;
	if (ioctl(r->hwdep, SNDRV_HDSPM_IOCTL_GET_PEAK_RMS, &m) < 0)
		return -errno;
	for (int c = 0; c < RAYDAT_N_CH; c++) {
		raise_peak(&s->peak_src[c], raydat_peak(m.input_peaks[c]));
		raise_peak(&s->peak_src[OFM_N_IN + c], raydat_peak(m.playback_peaks[c]));
		raise_peak(&s->peak_out[c], raydat_peak(m.output_peaks[c]));
	}
	return 0;
}

const struct ofm_backend ofm_backend_raydat = {
	.key = "raydat",
	.name = "RayDAT",
	.out_node_card = "RME RayDAT",
	.meter_us = 20000,
	.create = create,
	.destroy = destroy,
	.open = open_dev,
	.close = close_dev,
	.is_open = is_open,
	.status = status,
	.sync = sync_mix,
	.routes = routes,
	.meters = meters,
};
