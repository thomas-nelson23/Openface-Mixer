/*
 * RME HDSPe RayDAT (PCIe) through the kernel's snd-hdspm driver.
 *
 * snd-hdspm streams audio and exposes the card's hardware mixer (the same matrix TotalMix and
 * alsa-tools' hdspmixer drive) as ALSA controls and its meters through a hwdep device:
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
 * Mixer, meter and shared-memory channel numbers are the card's own: 0/1 AES, 2/3 S/PDIF, then
 * ADAT 1-4 (8 channels each at single speed, 4 at double, 2 at quad, packed from 4 up). There
 * is no separate output fader: the output master is folded into every send to that output, as
 * TotalMix and hdspmixer do on this card.
 */
#ifndef OPENFACE_MIXER_RAYDAT_H
#define OPENFACE_MIXER_RAYDAT_H

#include <stdbool.h>
#include <stdint.h>

#define RAYDAT_N_CH        36      /* inputs, playback and outputs at single speed */
#define RAYDAT_PLAY_SRC    64      /* mixer source of playback channel 0 */
#define RAYDAT_GAIN_UNITY  32768
#define RAYDAT_GAIN_MAX    65535
#define RAYDAT_PEAK_FULL   0x7fffff

/* Exposed for tests. */
uint16_t raydat_encode_gain(float lin);
/* Mixer source number of shared-memory source k (inputs, then playback from OFM_N_IN). */
int raydat_mixer_source(int k);
/* Linear level of one peak word. */
float raydat_peak(uint32_t word);
/* True for the RayDAT's ALSA card (driver and short name from SNDRV_CTL_IOCTL_CARD_INFO). */
bool raydat_card_match(const char *driver, const char *name);

#endif
