/* Unit test for the Digiface USB gain encoding (no device needed). Run via `make test`. */
#include <math.h>
#include <stdio.h>

#include "../engine/digiface_usb.h"

static int failed;

static void check(float lin, unsigned int want)
{
	unsigned int got = dfu_encode_gain(lin);
	if (got != want) {
		fprintf(stderr, "dfu_encode_gain(%g) = 0x%04x, want 0x%04x\n", lin, got, want);
		failed = 1;
	}
}

int main(void)
{
	check(0.0f, 0x0000);
	check(-1.0f, 0x0000);
	check(NAN, 0x0000);
	check(1.0f, 0x9000);          /* unity, as the kernel writes to the output faders */
	check(2.0f, 0xa000);          /* +6 dB, the maximum */
	check(4.0f, 0xa000);          /* clamped */
	check(0.25f, 0x2000);         /* below 0x4000: stored as is */
	check(0.5f, 0x8800);          /* 0x4000 >> 3 | 0x8000 */
	if (!failed)
		printf("test_digiface_usb: ok\n");
	return failed;
}
