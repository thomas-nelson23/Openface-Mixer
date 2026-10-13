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
 *                                   count overs (as hdspmixer reads them).
 *   hwdep SNDRV_HDSPM_IOCTL_GET_CONFIG      system sample rate and clock state.
 *
 * snd-hdspm (ALSA driver name "HDSPM") only runs the original cards properly. Cards from 2022
 * on carry RME's own PCI vendor ID (0x1d18) instead of Xilinx's; snd-hdspm still binds to them,
 * but they need snd-hdspe (driver name "HDSPe"). On such a card bound to snd-hdspm the engine
 * leaves the mixer alone and reports DFU_STATE_WRONG_DRIVER.
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
/* True for the model's ALSA card under either driver (driver and short name from
 * SNDRV_CTL_IOCTL_CARD_INFO). */
bool hdspe_card_match(const struct hdspe_model *m, const char *driver, const char *name);
/* False for a card the driver can't run: a 2022-and-later card (PCI vendor) under snd-hdspm. */
bool hdspe_driver_usable(const char *driver, unsigned int pci_vendor);

extern const struct hdspe_model hdspe_raydat, hdspe_aio, hdspe_aio_pro, hdspe_aes;

#endif
