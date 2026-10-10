/* Unit test for the RayDAT gain encoding, source numbering and meter decoding (no card needed). */
#include <math.h>
#include <stdio.h>

#include "../engine/raydat.h"
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
	check("gain off", raydat_encode_gain(0.0f), 0);
	check("gain negative", raydat_encode_gain(-1.0f), 0);
	check("gain nan", raydat_encode_gain(NAN), 0);
	check("gain unity", raydat_encode_gain(1.0f), 32768);
	check("gain -6 dB", raydat_encode_gain(0.5f), 16384);
	check("gain +6 dB clamps", raydat_encode_gain(2.0f), 65535);
	check("gain way over", raydat_encode_gain(10.0f), 65535);

	check("AES L", raydat_mixer_source(0), 0);
	check("last input", raydat_mixer_source(35), 35);
	check("playback 1", raydat_mixer_source(OFM_N_IN), 64);
	check("playback 36", raydat_mixer_source(OFM_N_IN + 35), 99);

	if (fabsf(raydat_peak(0x7fffff00) - 1.0f) > 1e-6f || raydat_peak(0x0000000f) != 0.0f ||
	    fabsf(raydat_peak(0x40000003) - 0.5f) > 1e-3f) {
		fprintf(stderr, "peaks: %g %g %g\n", raydat_peak(0x7fffff00), raydat_peak(0xf),
			raydat_peak(0x40000003));
		failed = 1;
	}

	check("card", raydat_card_match("HDSPM", "RME RayDAT_1a2b3c"), 1);
	check("card without serial", raydat_card_match("HDSPM", "RME RayDAT"), 1);
	check("AIO", raydat_card_match("HDSPM", "RME AIO_1a2b3c"), 0);
	check("other driver", raydat_card_match("USB-Audio", "RME RayDAT"), 0);
	check("snd-hdspe card", raydat_card_match("HDSPe", "RME RayDAT_00012345"), 1);

	check("old card, snd-hdspm", raydat_driver_usable("HDSPM", 0x10ee), 1);
	check("new card, snd-hdspm", raydat_driver_usable("HDSPM", RAYDAT_VENDOR_RME), 0);
	check("new card, snd-hdspe", raydat_driver_usable("HDSPe", RAYDAT_VENDOR_RME), 1);
	check("old card, snd-hdspe", raydat_driver_usable("HDSPe", 0x10ee), 1);
	check("unknown vendor, snd-hdspm", raydat_driver_usable("HDSPM", 0), 1);

	if (!failed)
		printf("test_raydat: ok\n");
	return failed;
}
