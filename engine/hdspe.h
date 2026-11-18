/*
 * RME HDSPe PCIe cards (RayDAT, AIO, AIO Pro, AES) through the kernel's snd-hdspm driver or the
 * newer out-of-tree snd-hdspe driver (github.com/Schroedingers-Cat/snd-hdspe, develop branch).
 *
 * Both stream audio and expose the card's hardware mixer (the same matrix TotalMix and
 * alsa-tools' hdspmixer drive) as ALSA controls and its meters through a hwdep device, with the
 * same control and ioctls on every card of the family (snd-hdspe kept them for hdspmixer):
 *
 *   control "Mixer" (iface HWDEP)   3 integers: source, destination, gain. Source 0-63 is a
 *                                   hardware input, 64-127 playback 0-63; destination 0-63 a
 *                                   hardware output. Gain 0-65535, linear, 32768 = unity, so
 *                                   +6 dB at most. The mixer is write only; the driver keeps a
 *                                   copy that reads return. The driver zeroes it at load and
 *                                   refuses writes with EBUSY while playback and capture are
 *                                   open in two different processes.
 *   hwdep SNDRV_HDSPM_IOCTL_GET_PEAK_RMS    peaks per input, playback and output channel:
 *                                   bits 8-30 are the level, full scale 0x7fffff; bits 0-3
 *                                   count consecutive full-scale samples (the kernel's
 *                                   "full-scale counters", hdspmixer's "FS samples for
 *                                   OVR"); then RMS words per input and playback channel
 *                                   (hdspmixer shows none for the outputs), mean square
 *                                   times about 2^63.
 *   hwdep SNDRV_HDSPM_IOCTL_GET_CONFIG      system sample rate and clock state.
 *
 * snd-hdspm (ALSA driver name "HDSPM") only runs the original cards properly. Cards from 2022
 * on need snd-hdspe (driver name "HDSPe"), though snd-hdspm still binds to them. We take RME's
 * own PCI vendor ID (0x1d18, instead of Xilinx's) as the sign of such a card; that is a guess
 * (snd-hdspe itself goes by the firmware build), so on such a card bound to snd-hdspm the engine
 * leaves the mixer alone and reports DFU_STATE_WRONG_DRIVER only until the user chooses "Use
 * anyway" in the GUI, which sets dev_config.
 *
 * Mixer, meter and shared-memory channel numbers are the card's own (openface_mixer/hdspe.py
 * names them). There is no separate output fader: the output master is folded into every send
 * to that output, as TotalMix and hdspmixer do on these cards. The MADI cards are not included:
 * their 64 channels don't fit the 36-channel mix model.
 */
#ifndef OPENFACE_MIXER_HDSPE_H
#define OPENFACE_MIXER_HDSPE_H

#include <stdbool.h>
#include <stdint.h>

#define HDSPE_MAX_CH       36      /* inputs, playback and outputs of the largest card (RayDAT) */
#define HDSPE_PLAY_SRC     64      /* mixer source of playback channel 0 */
#define HDSPE_GAIN_UNITY   32768
#define HDSPE_GAIN_MAX     65535
#define HDSPE_PEAK_FULL    0x7fffff
#define HDSPE_OVERS        0xf     /* a peak word's count of consecutive full-scale samples */
#define HDSPE_VENDOR_RME   0x1d18  /* PCI vendor of the 2022-and-later cards (older: Xilinx) */

/* One card of the family. */
struct hdspe_model {
	const char *card;   /* the ALSA card's short name, before the serial ("_1a2b3c") if any */
	int n_ch;           /* channel numbers in use per direction (highest + 1) */
};

/* Exposed for tests. */
uint16_t hdspe_encode_gain(float lin);
/* Mixer source number of shared-memory source k (inputs, then playback from OFM_N_IN). */
int hdspe_mixer_source(int k);
/* Linear level of one peak word. */
float hdspe_peak(uint32_t word);
/* Linear RMS level of one RMS word (inputs and playback; hdspmixer shows none for outputs). */
float hdspe_rms(uint64_t word);
/* True for the model's ALSA card under either driver (driver and short name from
 * SNDRV_CTL_IOCTL_CARD_INFO). */
bool hdspe_card_match(const struct hdspe_model *m, const char *driver, const char *name);
/* False for a card the driver may not run: a 2022-and-later card (PCI vendor) under snd-hdspm. */
bool hdspe_driver_usable(const char *driver, unsigned int pci_vendor);
/* The rate a card runs at under snd-hdspm, from its measured clock (the "System Sample Rate"
 * control: 47998, also at 96 and 192 kHz, where the clock runs at single speed) and the rate
 * the driver set last, which holds the speed. A standard rate within 2 % is taken as that. */
uint32_t hdspe_hdspm_rate(uint32_t measured, uint32_t set);

extern const struct hdspe_model hdspe_raydat, hdspe_aio, hdspe_aio_pro, hdspe_aes;

/* How the backends reach the driver: ioctl() on the card's control and hwdep devices. Tests put
 * their own function here and record the mixer writes. */
extern int (*hdspe_ioctl)(int fd, unsigned long req, void *arg);

#endif
