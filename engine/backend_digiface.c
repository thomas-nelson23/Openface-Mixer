/*
 * The RME Digiface USB as an engine backend, on top of digiface_usb.c.
 */
#include <errno.h>
#include <stdlib.h>

#include "backend.h"
#include "digiface_usb.h"

struct digiface {
	struct dfu *dfu;
	bool enabled;      /* we switched the DSP mixer on */
};

static void *create(void)
{
	struct digiface *d = calloc(1, sizeof(*d));
	if (d == NULL)
		return NULL;
	d->dfu = dfu_new();
	if (d->dfu == NULL) {
		free(d);
		return NULL;
	}
	return d;
}

static void destroy(void *h)
{
	struct digiface *d = h;
	dfu_free(d->dfu);
	free(d);
}

static int open_dev(void *h)
{
	struct digiface *d = h;
	return dfu_open(d->dfu);
}

static void close_dev(void *h)
{
	struct digiface *d = h;
	dfu_close(d->dfu);
	d->enabled = false;
}

static bool is_open(void *h)
{
	struct digiface *d = h;
	return dfu_is_open(d->dfu);
}

static int status(void *h, struct ofm_shm *s, bool *reset)
{
	struct digiface *d = h;
	uint32_t st[4];
	int r = dfu_read_status(d->dfu, st);
	if (r < 0)
		return r;
	s->rate = dfu_status_rate(st);
	/* the driver re-initialised the device (replug, resume) */
	*reset = d->enabled && !dfu_status_mixer_enabled(st);
	return 0;
}

static int sync_mix(void *h, struct ofm_shm *s, bool full)
{
	struct digiface *d = h;
	int r = dfu_sync(d->dfu, s->gain, s->out_gain, full);
	if (r < 0 && r != -ENOSPC)
		return r;
	if (full) {
		int e = dfu_set_mixer_enabled(d->dfu, true);
		if (e < 0)
			return e;
		d->enabled = true;
	}
	return r;
}

static int routes(void *h)
{
	struct digiface *d = h;
	return dfu_nodes_used(d->dfu);
}

static int meters(void *h, struct ofm_shm *s)
{
	struct digiface *d = h;
	struct dfu_meters m;
	if (!d->enabled)
		return -EAGAIN;
	int r = dfu_read_meters(d->dfu, &m);
	if (r < 0)
		return r;
	for (int i = 0; i < OFM_N_IN; i++)
		raise_peak(&s->peak_src[i], m.peak[DFU_METER_INPUT][i]);
	for (int i = 0; i < OFM_N_PLAY; i++)
		raise_peak(&s->peak_src[OFM_N_IN + i], m.peak[DFU_METER_PLAYBACK][i]);
	for (int o = 0; o < OFM_N_OUT; o++)
		raise_peak(&s->peak_out[o], m.peak[DFU_METER_OUTPUT][o]);
	return 0;
}

const struct ofm_backend ofm_backend_digiface = {
	.key = "digiface",
	.name = "Digiface",
	.out_node_prefix = "alsa_output.usb-RME_Digiface_USB",
	.meter_us = 10000,
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
