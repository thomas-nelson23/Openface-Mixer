/*
 * RME Fireface 802 as an engine backend: DSP mixer, settings and meters over FireWire.
 * See fireface_fw.h for the protocol.
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <linux/firewire-cdev.h>
#include <linux/firewire-constants.h>

#include "backend.h"
#include "digiface_usb.h"   /* DFU_STATE_* */
#include "fireface_fw.h"

#define RME_OUI            0x000a35
#define FF802_UNIT_VERSION 0x000005   /* SND_FF_UNIT_VERSION_802 in the kernel driver */

#define REG_CONFIG  0xffff00000014ull
#define REG_DSP     0xffff0000001cull
#define REG_STATUS  0x0000801c0000ull
#define REG_METERS  0xffffff000000ull

#define FW_DEVICES      "/sys/bus/firewire/devices"
#define TIMEOUT_MS      200
#define METER_CHUNKS    5
#define METER_FULL      0x07fffff0
#define METER_TAG_OUT   0x11111111u
#define METER_TAG_PLAY  0x33333333u
#define METER_TAG_IN    0x66666666u
#define VIRT_CMD        0x40000000u
#define MIXER_STEP      0x40
#define OUT_CH_OFFSET   FF802_N_IN
#define VOL_MIN         (-650)
#define VOL_MAX         60
#define UNSENT          0xffffffffu

struct ff802 {
	int fd;
	uint32_t generation;
	uint64_t closure;

	uint32_t sent_gain[FF802_N_OUT][FF802_N_SRC];   /* encoded, or UNSENT */
	uint32_t sent_vol[FF802_N_OUT];
	uint32_t sent_cmd[OFM_N_DEV_CMD];
	uint32_t sent_config;
	int routes;
};

/* ---------- encoding (no I/O) ---------- */

uint16_t ff802_encode_gain(float lin)
{
	if (!(lin > 0.0f))
		return 0;
	long v = lround(lin * 4096.0);
	if (v > 0x2000)
		v = 0x2000;
	return (uint16_t)(0x8000 | v);
}

int16_t ff802_out_vol(float lin)
{
	if (!(lin > 0.0f))
		return VOL_MIN;
	long v = lround(200.0 * log10(lin));
	return (int16_t)(v < VOL_MIN ? VOL_MIN : v > VOL_MAX ? VOL_MAX : v);
}

uint32_t ff802_parity(uint32_t cmd)
{
	cmd &= 0x7fffffffu;
	return __builtin_popcount(cmd) % 2 ? cmd : cmd | 0x80000000u;
}

uint32_t ff802_mixer_cmd(int out, int src, uint16_t gain)
{
	return VIRT_CMD | ((uint32_t)(MIXER_STEP * out + src) << 16) | gain;
}

uint32_t ff802_vol_cmd(int out, int16_t vol)
{
	return ((uint32_t)(OUT_CH_OFFSET + out) << 24) | (uint16_t)vol;
}

uint32_t ff802_status_rate(uint32_t status)
{
	static const uint32_t rates[16] = {
		32000, 44100, 48000, 0, 64000, 88200, 96000, 0, 128000, 176400, 192000,
	};
	return rates[status >> 28];
}

static uint32_t le32(const uint8_t *p)
{
	return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static void raise_peak(float *dst, float v)
{
	if (v > *dst)
		*dst = v;
}

uint32_t ff802_decode_meter_chunk(const uint8_t *chunk, struct ofm_shm *s)
{
	uint32_t tag = le32(chunk + FF802_METER_CHUNK - 4);
	float *dst;
	int n;

	switch (tag) {
	case METER_TAG_IN:
		dst = s->peak_src;
		n = FF802_N_IN;
		break;
	case METER_TAG_PLAY:
		dst = s->peak_src + OFM_N_IN;
		n = FF802_N_OUT;
		break;
	case METER_TAG_OUT:
		dst = s->peak_out;
		n = FF802_N_OUT;
		break;
	default:
		return tag;
	}
	for (int i = 0; i < n; i++) {
		uint32_t v = le32(chunk + 256 + 4 * i) & METER_FULL;
		raise_peak(&dst[i], (float)v / (float)METER_FULL);
	}
	return tag;
}

/* ---------- FireWire transactions ---------- */

static int fw_request(struct ff802 *f, int tcode, uint64_t offset, void *data, size_t len)
{
	for (int attempt = 0; attempt < 2; attempt++) {
		struct fw_cdev_send_request req = {
			.tcode = (uint32_t)tcode,
			.length = (uint32_t)len,
			.offset = offset,
			.closure = ++f->closure,
			.data = (uintptr_t)data,
			.generation = f->generation,
		};
		if (ioctl(f->fd, FW_CDEV_IOC_SEND_REQUEST, &req) < 0)
			return -errno;

		for (;;) {
			struct pollfd p = { .fd = f->fd, .events = POLLIN };
			int r = poll(&p, 1, TIMEOUT_MS);
			if (r == 0)
				return -ETIMEDOUT;
			if (r < 0) {
				if (errno == EINTR)
					continue;
				return -errno;
			}
			if (p.revents & (POLLHUP | POLLERR))
				return -ENODEV;

			uint64_t buf[(sizeof(struct fw_cdev_event_response) + 1024) / 8];
			ssize_t n = read(f->fd, buf, sizeof(buf));
			if (n < 0) {
				if (errno == EINTR || errno == EAGAIN)
					continue;
				return -errno;
			}
			union fw_cdev_event *e = (union fw_cdev_event *)buf;
			if (e->common.type == FW_CDEV_EVENT_BUS_RESET) {
				f->generation = e->bus_reset.generation;
				continue;
			}
			if (e->common.type != FW_CDEV_EVENT_RESPONSE || e->response.closure != req.closure)
				continue;
			if (e->response.rcode == RCODE_GENERATION)
				break;   /* a bus reset happened in between: retry with the new generation */
			if (e->response.rcode != RCODE_COMPLETE)
				return -EIO;
			if (tcode == TCODE_READ_QUADLET_REQUEST || tcode == TCODE_READ_BLOCK_REQUEST)
				memcpy(data, e->response.data,
				       e->response.length < len ? e->response.length : len);
			return 0;
		}
	}
	return -EAGAIN;
}

static int write_quadlet(struct ff802 *f, uint64_t offset, uint32_t value)
{
	uint8_t b[4] = { value, value >> 8, value >> 16, value >> 24 };
	return fw_request(f, TCODE_WRITE_QUADLET_REQUEST, offset, b, 4);
}

static int read_quadlet(struct ff802 *f, uint64_t offset, uint32_t *value)
{
	uint8_t b[4];
	int r = fw_request(f, TCODE_READ_QUADLET_REQUEST, offset, b, 4);
	if (r == 0)
		*value = le32(b);
	return r;
}

static int dsp_cmd(struct ff802 *f, uint32_t cmd)
{
	return write_quadlet(f, REG_DSP, ff802_parity(cmd));
}

/* ---------- finding the device ---------- */

static int read_hex(const char *dir, const char *name, unsigned long *v)
{
	char path[512], buf[32];
	snprintf(path, sizeof(path), "%s/%s", dir, name);
	FILE *fp = fopen(path, "r");
	if (fp == NULL)
		return -1;
	int ok = fgets(buf, sizeof(buf), fp) != NULL;
	fclose(fp);
	if (!ok)
		return -1;
	*v = strtoul(buf, NULL, 16);
	return 0;
}

/* Finds the 802's node ("fw1") by its unit directory ("fw1.0"). */
static int find_node(char *node, size_t len)
{
	DIR *d = opendir(FW_DEVICES);
	if (d == NULL)
		return -1;
	struct dirent *e;
	int found = -1;
	while (found < 0 && (e = readdir(d)) != NULL) {
		const char *dot = strchr(e->d_name, '.');
		if (strncmp(e->d_name, "fw", 2) != 0 || dot == NULL)
			continue;
		char dir[512];
		unsigned long spec, ver;
		snprintf(dir, sizeof(dir), "%s/%s", FW_DEVICES, e->d_name);
		if (read_hex(dir, "specifier_id", &spec) == 0 && read_hex(dir, "version", &ver) == 0 &&
		    spec == RME_OUI && ver == FF802_UNIT_VERSION) {
			snprintf(node, len, "%.*s", (int)(dot - e->d_name), e->d_name);
			found = 0;
		}
	}
	closedir(d);
	return found;
}

/* ---------- backend ---------- */

static void forget_sent(struct ff802 *f)
{
	memset(f->sent_gain, 0xff, sizeof(f->sent_gain));
	memset(f->sent_vol, 0xff, sizeof(f->sent_vol));
	memset(f->sent_cmd, 0, sizeof(f->sent_cmd));
	f->sent_config = 0;
	f->routes = 0;
}

static void *create(void)
{
	struct ff802 *f = calloc(1, sizeof(*f));
	if (f == NULL)
		return NULL;
	f->fd = -1;
	forget_sent(f);
	return f;
}

static void close_dev(void *h)
{
	struct ff802 *f = h;
	if (f->fd >= 0)
		close(f->fd);
	f->fd = -1;
	forget_sent(f);
}

static void destroy(void *h)
{
	close_dev(h);
	free(h);
}

static bool is_open(void *h)
{
	return ((struct ff802 *)h)->fd >= 0;
}

static int open_dev(void *h)
{
	struct ff802 *f = h;
	char node[64], path[80];

	if (f->fd >= 0)
		return DFU_STATE_ACTIVE;
	if (find_node(node, sizeof(node)) < 0)
		return DFU_STATE_NO_DEVICE;
	snprintf(path, sizeof(path), "/dev/%s", node);
	int fd = open(path, O_RDWR | O_CLOEXEC);
	if (fd < 0)
		return errno == EACCES || errno == EPERM ? DFU_STATE_NO_ACCESS : DFU_STATE_ERROR;

	struct fw_cdev_event_bus_reset reset = { 0 };
	struct fw_cdev_get_info info = {
		.version = 4,
		.bus_reset = (uintptr_t)&reset,
	};
	if (ioctl(fd, FW_CDEV_IOC_GET_INFO, &info) < 0) {
		close(fd);
		return DFU_STATE_ERROR;
	}
	f->fd = fd;
	f->generation = reset.generation;
	forget_sent(f);
	return DFU_STATE_ACTIVE;
}

static int status(void *h, struct ofm_shm *s, bool *reset)
{
	struct ff802 *f = h;
	uint32_t st = 0;
	int r = read_quadlet(f, REG_STATUS, &st);
	if (r < 0)
		return r;
	s->dev_status = st;
	s->rate = ff802_status_rate(st);
	/* the 802 keeps its DSP state across bus resets; a power cycle drops the node instead */
	*reset = false;
	return 0;
}

static int sync_mix(void *h, struct ofm_shm *s, bool full)
{
	struct ff802 *f = h;
	int r, routes = 0;

	if (s->dev_config && (full || s->dev_config != f->sent_config)) {
		if ((r = write_quadlet(f, REG_CONFIG, s->dev_config)) < 0)
			return r;
		f->sent_config = s->dev_config;
	}

	for (int i = 0; i < OFM_N_DEV_CMD; i++) {
		uint32_t c = s->dev_cmd[i];
		if (c == 0 || (!full && c == f->sent_cmd[i]))
			continue;
		if ((r = dsp_cmd(f, c)) < 0)
			return r;
		f->sent_cmd[i] = c;
	}

	for (int o = 0; o < FF802_N_OUT; o++) {
		/* a muted output gets no signal at all: the volume only goes down to -65 dB */
		bool muted = !(s->out_gain[o] > 0.0f);
		for (int k = 0; k < FF802_N_SRC; k++) {
			uint32_t g = muted ? 0 : ff802_encode_gain(s->gain[o][k]);
			if (g)
				routes++;
			if (!full && g == f->sent_gain[o][k])
				continue;
			if ((r = dsp_cmd(f, ff802_mixer_cmd(o, k, (uint16_t)g))) < 0)
				return r;
			f->sent_gain[o][k] = g;
		}
		uint32_t v = (uint16_t)ff802_out_vol(s->out_gain[o]);
		if (full || v != f->sent_vol[o]) {
			if ((r = dsp_cmd(f, ff802_vol_cmd(o, (int16_t)v))) < 0)
				return r;
			f->sent_vol[o] = v;
		}
	}
	f->routes = routes;
	return 0;
}

static int routes(void *h)
{
	return ((struct ff802 *)h)->routes;
}

static int meters(void *h, struct ofm_shm *s)
{
	struct ff802 *f = h;
	uint8_t chunk[FF802_METER_CHUNK];
	for (int i = 0; i < METER_CHUNKS; i++) {
		int r = fw_request(f, TCODE_READ_BLOCK_REQUEST, REG_METERS, chunk, sizeof(chunk));
		if (r < 0)
			return r;
		ff802_decode_meter_chunk(chunk, s);
	}
	return 0;
}

const struct ofm_backend ofm_backend_ff802 = {
	.key = "ff802",
	.name = "Fireface 802",
	/* snd-fireface's PCM device; PipeWire names firewire cards after the node GUID */
	.out_node_prefix = "alsa_output.firewire-0x000a35",
	.meter_us = 30000,
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
