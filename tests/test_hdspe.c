/* Unit test for the HDSPe gain encoding, source numbering, card matching and meter decoding (no
 * card needed). */
#include <math.h>
#include <stdio.h>

#include "../engine/hdspe.h"
#include "../engine/shm_layout.h"

static int failed;

static void check(const char *what, long got, long want)
{
	if (got != want) {
		fprintf(stderr, "%s = %ld, want %ld\n", what, got, want);
		failed = 1;
	}
}

int main(void)
{
	check("gain off", hdspe_encode_gain(0.0f), 0);
	check("gain negative", hdspe_encode_gain(-1.0f), 0);
	check("gain nan", hdspe_encode_gain(NAN), 0);
	check("gain unity", hdspe_encode_gain(1.0f), 32768);
	check("gain -6 dB", hdspe_encode_gain(0.5f), 16384);
	check("gain +6 dB clamps", hdspe_encode_gain(2.0f), 65535);
	check("gain way over", hdspe_encode_gain(10.0f), 65535);

	check("AES L", hdspe_mixer_source(0), 0);
	check("last input", hdspe_mixer_source(35), 35);
	check("playback 1", hdspe_mixer_source(OFM_N_IN), 64);
	check("playback 36", hdspe_mixer_source(OFM_N_IN + 35), 99);

	if (fabsf(hdspe_peak(0x7fffff00) - 1.0f) > 1e-6f || hdspe_peak(0x0000000f) != 0.0f ||
	    fabsf(hdspe_peak(0x40000003) - 0.5f) > 1e-3f) {
		fprintf(stderr, "peaks: %g %g %g\n", hdspe_peak(0x7fffff00), hdspe_peak(0xf),
			hdspe_peak(0x40000003));
		failed = 1;
	}

	check("card", hdspe_card_match(&hdspe_raydat, "HDSPM", "RME RayDAT_1a2b3c"), 1);
	check("card without serial", hdspe_card_match(&hdspe_raydat, "HDSPM", "RME RayDAT"), 1);
	check("AIO isn't a RayDAT", hdspe_card_match(&hdspe_raydat, "HDSPM", "RME AIO_1a2b3c"), 0);
	check("other driver", hdspe_card_match(&hdspe_raydat, "USB-Audio", "RME RayDAT"), 0);
	check("snd-hdspe card", hdspe_card_match(&hdspe_raydat, "HDSPe", "RME RayDAT_00012345"), 1);
	check("AIO", hdspe_card_match(&hdspe_aio, "HDSPM", "RME AIO_1a2b3c"), 1);
	check("AIO Pro isn't an AIO", hdspe_card_match(&hdspe_aio, "HDSPe", "RME AIO Pro_00012345"), 0);
	check("AIO Pro", hdspe_card_match(&hdspe_aio_pro, "HDSPe", "RME AIO Pro_00012345"), 1);
	check("AIO without serial", hdspe_card_match(&hdspe_aio, "HDSPe", "RME AIO"), 1);
	check("AES", hdspe_card_match(&hdspe_aes, "HDSPM", "RME AES32_1a2b3c"), 1);
	check("MADI isn't supported", hdspe_card_match(&hdspe_aes, "HDSPM", "RME MADI_1a2b3c"), 0);

	check("old card, snd-hdspm", hdspe_driver_usable("HDSPM", 0x10ee), 1);
	check("new card, snd-hdspm", hdspe_driver_usable("HDSPM", HDSPE_VENDOR_RME), 0);
	check("new card, snd-hdspe", hdspe_driver_usable("HDSPe", HDSPE_VENDOR_RME), 1);
	check("old card, snd-hdspe", hdspe_driver_usable("HDSPe", 0x10ee), 1);
	check("unknown vendor, snd-hdspm", hdspe_driver_usable("HDSPM", 0), 1);

	if (!failed)
		printf("test_hdspe: ok\n");
	return failed;
}
