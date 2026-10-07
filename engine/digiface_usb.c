/*
 * RME Digiface USB hardware mixer over libusb. See digiface_usb.h for the protocol.
 */
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libusb.h>

#include "digiface_usb.h"

#define VID            0x2a39
#define PID_A          0x3f8c
#define PID_B          0x3fa0
#define MIXER_IFACE    1
#define EP_MIXER       0x0b
#define EP_LEVELS      0x84

#define REQ_CTL_REG1   16
#define REQ_STATUS     17
#define REQ_CTL_REG2   18
#define REQ_OUT_GAIN   21

#define REG1_MIXER_OFF 0x0400
#define REG2_MIXER_OFF 0x0100
#define STATUS_MIXER_OFF (1u << 24) /* REG2_MIXER_OFF as seen in status word 3 */

#define SRC_PLAYBACK   0x100
#define TIMEOUT_MS     500
#define NO_NODE        (-1)

/* longest command list: reset every node, then route + gain for every node */
#define MAX_CMDS       (3 * DFU_MAX_NODES)

struct dfu {
	libusb_context *ctx;
	libusb_device_handle *h;

	int16_t node[OFM_N_OUT][OFM_N_SRC];     /* node index per crosspoint, or NO_NODE */
	uint16_t sent[OFM_N_OUT][OFM_N_SRC];    /* encoded gain last sent per crosspoint */
	uint16_t sent_out[OFM_N_OUT];
	int16_t free_nodes[DFU_MAX_NODES];
	int n_free;

	uint32_t cmd[MAX_CMDS];
	int n_cmd;
};

uint16_t dfu_encode_gain(float lin)
{
	if (!(lin > 0.0f))
		return 0;
	double v = lin * 32768.0;
	if (v > 65536.0)
		v = 65536.0;
	uint32_t val = (uint32_t)lround(v);
	if (val >= 0x4000)
		val = (val >> 3) | 0x8000;
	return (uint16_t)val;
}

static void reset_tracking(struct dfu *d)
{
	for (int o = 0; o < OFM_N_OUT; o++) {
		for (int k = 0; k < OFM_N_SRC; k++) {
			d->node[o][k] = NO_NODE;
			d->sent[o][k] = 0;
		}
		d->sent_out[o] = 0xffff; /* unknown: force a write */
	}
	d->n_free = DFU_MAX_NODES;
	for (int i = 0; i < DFU_MAX_NODES; i++)
		d->free_nodes[i] = (int16_t)(DFU_MAX_NODES - 1 - i);
	d->n_cmd = 0;
}

struct dfu *dfu_new(void)
{
	struct dfu *d = calloc(1, sizeof(*d));
	if (d == NULL)
		return NULL;
	if (libusb_init(&d->ctx) < 0) {
		free(d);
		return NULL;
	}
	reset_tracking(d);
	return d;
}

void dfu_free(struct dfu *d)
{
	if (d == NULL)
		return;
	dfu_close(d);
	libusb_exit(d->ctx);
	free(d);
}

bool dfu_is_open(const struct dfu *d)
{
	return d->h != NULL;
}

int dfu_open(struct dfu *d)
{
	libusb_device **list;
	libusb_device *found = NULL;
	ssize_t n;

	if (d->h)
		return DFU_STATE_ACTIVE;

	n = libusb_get_device_list(d->ctx, &list);
	if (n < 0)
		return DFU_STATE_ERROR;
	for (ssize_t i = 0; i < n && !found; i++) {
		struct libusb_device_descriptor desc;
		if (libusb_get_device_descriptor(list[i], &desc) == 0 && desc.idVendor == VID &&
		    (desc.idProduct == PID_A || desc.idProduct == PID_B))
			found = list[i];
	}
	int state = DFU_STATE_NO_DEVICE;
	if (found) {
		int err = libusb_open(found, &d->h);
		if (err == LIBUSB_ERROR_ACCESS) {
			state = DFU_STATE_NO_ACCESS;
		} else if (err < 0) {
			state = DFU_STATE_ERROR;
		} else {
			err = libusb_claim_interface(d->h, MIXER_IFACE);
			if (err < 0) {
				libusb_close(d->h);
				d->h = NULL;
				state = err == LIBUSB_ERROR_BUSY ? DFU_STATE_BUSY : DFU_STATE_ERROR;
			} else {
				reset_tracking(d);
				state = DFU_STATE_ACTIVE;
			}
		}
	}
	libusb_free_device_list(list, 1);
	return state;
}

void dfu_close(struct dfu *d)
{
	if (d->h == NULL)
		return;
	libusb_release_interface(d->h, MIXER_IFACE);
	libusb_close(d->h);
	d->h = NULL;
}

static int ctl_write(struct dfu *d, uint8_t req, uint16_t val, uint16_t idx)
{
	int r = libusb_control_transfer(d->h,
			LIBUSB_ENDPOINT_OUT | LIBUSB_REQUEST_TYPE_VENDOR | LIBUSB_RECIPIENT_DEVICE,
			req, val, idx, NULL, 0, TIMEOUT_MS);
	return r < 0 ? r : 0;
}

int dfu_read_status(struct dfu *d, uint32_t status[4])
{
	uint8_t buf[16];
	int r = libusb_control_transfer(d->h,
			LIBUSB_ENDPOINT_IN | LIBUSB_REQUEST_TYPE_VENDOR | LIBUSB_RECIPIENT_DEVICE,
			REQ_STATUS, 0, 0, buf, sizeof(buf), TIMEOUT_MS);
	if (r < 0)
		return r;
	if (r != sizeof(buf))
		return LIBUSB_ERROR_IO;
	for (int i = 0; i < 4; i++)
		status[i] = (uint32_t)buf[4 * i] | (uint32_t)buf[4 * i + 1] << 8 |
			    (uint32_t)buf[4 * i + 2] << 16 | (uint32_t)buf[4 * i + 3] << 24;
	return 0;
}

bool dfu_status_mixer_enabled(const uint32_t status[4])
{
	return !(status[3] & STATUS_MIXER_OFF);
}

int dfu_set_mixer_enabled(struct dfu *d, bool enabled)
{
	int r = ctl_write(d, REQ_CTL_REG2, enabled ? 0 : REG2_MIXER_OFF, REG2_MIXER_OFF);
	if (r == 0)
		r = ctl_write(d, REQ_CTL_REG1, enabled ? 0 : REG1_MIXER_OFF, REG1_MIXER_OFF);
	return r;
}

static int flush_cmds(struct dfu *d)
{
	/* send in chunks well below what the endpoint takes in one transfer */
	const int chunk = 1024;
	int r = 0;
	for (int i = 0; i < d->n_cmd && r == 0; i += chunk) {
		int n = d->n_cmd - i < chunk ? d->n_cmd - i : chunk;
		uint8_t buf[4 * 1024];
		for (int j = 0; j < n; j++) {
			uint32_t c = d->cmd[i + j];
			buf[4 * j] = c & 0xff;
			buf[4 * j + 1] = (c >> 8) & 0xff;
			buf[4 * j + 2] = (c >> 16) & 0xff;
			buf[4 * j + 3] = c >> 24;
		}
		int done = 0;
		r = libusb_bulk_transfer(d->h, EP_MIXER, buf, 4 * n, &done, TIMEOUT_MS);
		if (r == 0 && done != 4 * n)
			r = LIBUSB_ERROR_IO;
	}
	d->n_cmd = 0;
	return r;
}

static void add_cmd(struct dfu *d, uint32_t c)
{
	if (d->n_cmd < MAX_CMDS)
		d->cmd[d->n_cmd++] = c;
}

static uint32_t mixer_src(int k)
{
	return k < OFM_N_IN ? (uint32_t)k : SRC_PLAYBACK + (uint32_t)(k - OFM_N_IN);
}

int dfu_sync(struct dfu *d, const float gain[OFM_N_OUT][OFM_N_SRC],
	     const float out_gain[OFM_N_OUT], bool full)
{
	int ret = 0;

	if (d->h == NULL)
		return LIBUSB_ERROR_NO_DEVICE;

	if (full) {
		reset_tracking(d);
		for (int i = 0; i < DFU_MAX_NODES; i++)
			add_cmd(d, 0xC000FFFFu | (uint32_t)i << 16);
	}

	/* free nodes that go silent first, so they can be reused below */
	for (int o = 0; o < OFM_N_OUT; o++) {
		for (int k = 0; k < OFM_N_SRC; k++) {
			int node = d->node[o][k];
			if (node == NO_NODE || dfu_encode_gain(gain[o][k]) != 0)
				continue;
			add_cmd(d, (uint32_t)node << 16);
			d->node[o][k] = NO_NODE;
			d->sent[o][k] = 0;
			d->free_nodes[d->n_free++] = (int16_t)node;
		}
	}

	for (int o = 0; o < OFM_N_OUT; o++) {
		for (int k = 0; k < OFM_N_SRC; k++) {
			uint16_t v = dfu_encode_gain(gain[o][k]);
			if (v == 0 || v == d->sent[o][k])
				continue;
			int node = d->node[o][k];
			if (node == NO_NODE) {
				if (d->n_free == 0) {
					ret = -ENOSPC;
					continue;
				}
				node = d->free_nodes[--d->n_free];
				d->node[o][k] = (int16_t)node;
				/* a free node always has gain 0, so routing it first is silent */
				add_cmd(d, 0x40000000u | (uint32_t)node << 16 |
					   (uint32_t)o << 9 | mixer_src(k));
			}
			add_cmd(d, (uint32_t)node << 16 | v);
			d->sent[o][k] = v;
		}
	}

	int r = flush_cmds(d);
	if (r < 0)
		return r;

	for (int o = 0; o < OFM_N_OUT; o++) {
		uint16_t v = dfu_encode_gain(out_gain[o]);
		if (v == d->sent_out[o])
			continue;
		r = ctl_write(d, REQ_OUT_GAIN, v, (uint16_t)(0x100 + o));
		if (r < 0)
			return r;
		d->sent_out[o] = v;
	}
	return ret;
}

int dfu_nodes_used(const struct dfu *d)
{
	return DFU_MAX_NODES - d->n_free;
}

#define METER_FRAME_BYTES 1024
#define METER_FRAME_WORDS 128
#define METER_DATA_WORDS  100
#define METER_PEAK_WORD   64

static uint64_t le64(const uint8_t *p)
{
	uint64_t v = 0;
	for (int i = 7; i >= 0; i--)
		v = v << 8 | p[i];
	return v;
}

int dfu_decode_meter_frame(const uint8_t *frame, struct dfu_meters *m)
{
	uint64_t marker = le64(frame + 8 * (METER_FRAME_WORDS - 1));
	uint32_t type = marker & 0xf;
	if (marker >> 32 != (marker & 0xffffffff) || (marker >> 4 & 0x0fffffff) != 0x0fffffff ||
	    le64(frame + 8 * METER_DATA_WORDS) != marker || type > DFU_METER_OUTPUT)
		return -1;
	for (int c = 0; c < DFU_METER_CHANNELS; c++) {
		uint64_t pw = le64(frame + 8 * (METER_PEAK_WORD + c / 2));
		uint32_t pk = c % 2 ? pw >> 32 : pw & 0xffffffff;
		m->peak[type][c] = (float)pk / (float)(1u << 27);
		m->rms[type][c] = (float)sqrt((double)le64(frame + 8 * c) / 0x1p55);
	}
	return (int)type;
}

int dfu_read_meters(struct dfu *d, struct dfu_meters *m)
{
	uint8_t buf[METER_FRAME_BYTES];
	unsigned int seen = 0;

	if (d->h == NULL)
		return LIBUSB_ERROR_NO_DEVICE;
	/* one frame per transfer; the types cycle, so a few reads cover all three */
	for (int i = 0; i < 6 && seen != 7; i++) {
		int done = 0;
		int r = libusb_bulk_transfer(d->h, EP_LEVELS, buf, sizeof(buf), &done, 100);
		if (r < 0)
			return r;
		if (done != sizeof(buf))
			continue;
		int t = dfu_decode_meter_frame(buf, m);
		if (t >= 0)
			seen |= 1u << t;
	}
	return seen == 7 ? 0 : LIBUSB_ERROR_IO;
}
