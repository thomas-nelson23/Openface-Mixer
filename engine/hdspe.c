/*
 * RME HDSPe cards (RayDAT, AIO, AIO Pro, AES) as engine backends: hardware mixer, sample rate
 * and meters through snd-hdspm's or snd-hdspe's ALSA control and hwdep devices. See hdspe.h.
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
#include "hdspe.h"

#define N_SRC_MAX    (2 * HDSPE_MAX_CH)
#define MAX_CARDS    32

const struct hdspe_model hdspe_raydat = { "RME RayDAT", 36 };
const struct hdspe_model hdspe_aio = { "RME AIO", 20 };
const struct hdspe_model hdspe_aio_pro = { "RME AIO Pro", 20 };
const struct hdspe_model hdspe_aes = { "RME AES32", 16 };

struct hdspe {
	const struct hdspe_model *model;
	int ctl;      /* /dev/snd/controlC<n>, -1 while closed */
	int hwdep;    /* /dev/snd/hwC<n>D0 */
	unsigned int mixer_numid;
	uint16_t sent[HDSPE_MAX_CH][N_SRC_MAX];
	int routes;
	int check_out;   /* output whose sends the next status call reads back */
};

uint16_t hdspe_encode_gain(float lin)
{
	if (!(lin > 0.0f))
		return 0;
	float v = lin * HDSPE_GAIN_UNITY + 0.5f;
	return v >= HDSPE_GAIN_MAX ? HDSPE_GAIN_MAX : (uint16_t)v;
}

int hdspe_mixer_source(int k)
{
	return k < OFM_N_IN ? k : HDSPE_PLAY_SRC + (k - OFM_N_IN);
}

float hdspe_peak(uint32_t word)
{
	return (float)((word >> 8) & HDSPE_PEAK_FULL) / HDSPE_PEAK_FULL;
}

bool hdspe_card_match(const struct hdspe_model *m, const char *driver, const char *name)
{
	return (strcmp(driver, "HDSPM") == 0 || strcmp(driver, "HDSPe") == 0) &&
	       ofm_card_match(name, m->card);
}

bool hdspe_driver_usable(const char *driver, unsigned int pci_vendor)
{
	return !(strcmp(driver, "HDSPM") == 0 && pci_vendor == HDSPE_VENDOR_RME);
}

/* PCI vendor ID of ALSA card n, or 0 if unknown. */
static unsigned int card_pci_vendor(int n)
{
	char path[64];
	unsigned int v = 0;
	snprintf(path, sizeof(path), "/sys/class/sound/card%d/device/vendor", n);
	FILE *f = fopen(path, "r");
	if (f == NULL)
		return 0;
	if (fscanf(f, "%x", &v) != 1)
		v = 0;
	fclose(f);
	return v;
}

/* shared-memory source of mixer source slot j (inputs, then as many playback channels) */
static int shm_source(const struct hdspe *r, int j)
{
	int n = r->model->n_ch;
	return j < n ? j : OFM_N_IN + (j - n);
}

/* ---------- ALSA control and hwdep ---------- */

static int mixer_io(struct hdspe *r, unsigned long req, int src, int dst, uint16_t *gain)
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

static int write_gain(struct hdspe *r, int src, int dst, uint16_t gain)
{
	return mixer_io(r, SNDRV_CTL_IOCTL_ELEM_WRITE, src, dst, &gain);
}

static int read_gain(struct hdspe *r, int src, int dst, uint16_t *gain)
{
	return mixer_io(r, SNDRV_CTL_IOCTL_ELEM_READ, src, dst, gain);
}

/* Opens the control device of the model's card. Returns its fd, or -1 with *state set. */
static int find_card(const struct hdspe_model *m, int *card, int *state)
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
		    hdspe_card_match(m, (const char *)info.driver, (const char *)info.name)) {
			if (hdspe_driver_usable((const char *)info.driver, card_pci_vendor(n))) {
				*card = n;
				return fd;
			}
			*state = DFU_STATE_WRONG_DRIVER;
		}
		close(fd);
	}
	return -1;
}

/* ---------- backend ---------- */

static void *create(const struct hdspe_model *m)
{
	struct hdspe *r = calloc(1, sizeof(*r));
	if (r == NULL)
		return NULL;
	r->model = m;
	r->ctl = r->hwdep = -1;
	return r;
}

static void close_dev(void *h)
{
	struct hdspe *r = h;
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
	struct hdspe *r = h;
	int card, state;

	if (r->ctl >= 0)
		return DFU_STATE_ACTIVE;
	r->ctl = find_card(r->model, &card, &state);
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
	struct hdspe *r = h;
	return r->ctl >= 0;
}

static int status(void *h, struct ofm_shm *s, bool *reset)
{
	struct hdspe *r = h;
	struct hdspm_config cfg;
	memset(&cfg, 0, sizeof(cfg));
	if (ioctl(r->hwdep, SNDRV_HDSPM_IOCTL_GET_CONFIG, &cfg) < 0)
		return -errno;
	s->rate = cfg.system_sample_rate;
	s->dev_status = cfg.system_clock_mode | (uint32_t)cfg.autosync_ref << 8;

	/* The mixer can't be read from the card, but the driver's copy tells whether it was
	 * reloaded (it zeroes the mixer) or someone else changed it. One output per call. */
	int o = r->check_out;
	int n = r->model->n_ch;
	r->check_out = (o + 1) % n;
	for (int j = 0; j < 2 * n; j++) {
		uint16_t g = 0;
		int e = read_gain(r, hdspe_mixer_source(shm_source(r, j)), o, &g);
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
	struct hdspe *r = h;
	int routes = 0, n = r->model->n_ch;

	for (int o = 0; o < n; o++) {
		float master = s->out_gain[o];
		for (int j = 0; j < 2 * n; j++) {
			int k = shm_source(r, j);
			uint16_t g = hdspe_encode_gain(s->gain[o][k] * master);
			if (g)
				routes++;
			if (!full && g == r->sent[o][j])
				continue;
			int e = write_gain(r, hdspe_mixer_source(k), o, g);
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
	struct hdspe *r = h;
	return r->routes;
}

static int meters(void *h, struct ofm_shm *s)
{
	struct hdspe *r = h;
	struct hdspm_peak_rms m;
	if (ioctl(r->hwdep, SNDRV_HDSPM_IOCTL_GET_PEAK_RMS, &m) < 0)
		return -errno;
	for (int c = 0; c < r->model->n_ch; c++) {
		raise_peak(&s->peak_src[c], hdspe_peak(m.input_peaks[c]));
		raise_peak(&s->peak_src[OFM_N_IN + c], hdspe_peak(m.playback_peaks[c]));
		raise_peak(&s->peak_out[c], hdspe_peak(m.output_peaks[c]));
	}
	return 0;
}

static void *create_raydat(void) { return create(&hdspe_raydat); }
static void *create_aio(void) { return create(&hdspe_aio); }
static void *create_aio_pro(void) { return create(&hdspe_aio_pro); }
static void *create_aes(void) { return create(&hdspe_aes); }

#define HDSPE_BACKEND(k, n, card_name, create_fn)	\
	{						\
		.key = k,				\
		.name = n,				\
		.out_node_card = card_name,		\
		.meter_us = 20000,			\
		.create = create_fn,			\
		.destroy = destroy,			\
		.open = open_dev,			\
		.close = close_dev,			\
		.is_open = is_open,			\
		.status = status,			\
		.sync = sync_mix,			\
		.routes = routes,			\
		.meters = meters,			\
	}

const struct ofm_backend ofm_backend_raydat = HDSPE_BACKEND("raydat", "RayDAT", "RME RayDAT",
							    create_raydat);
const struct ofm_backend ofm_backend_aio = HDSPE_BACKEND("aio", "AIO", "RME AIO", create_aio);
const struct ofm_backend ofm_backend_aiopro = HDSPE_BACKEND("aiopro", "AIO Pro", "RME AIO Pro",
							    create_aio_pro);
const struct ofm_backend ofm_backend_aes = HDSPE_BACKEND("aes", "AES", "RME AES32", create_aes);
