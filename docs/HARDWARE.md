# RME Digiface USB: hardware notes

## Linux driver

Since Linux 6.12, `snd-usb-audio` supports the Digiface USB (USB ID `2a39:3f8c`, plus
`2a39:3fa0`), written by Asahi Lina. The driver:

- binds only USB interface 0 (audio streaming) and leaves the device's DSP to userspace;
- **disables the internal mixer at boot** (pure 1:1 passthrough) and sets all outputs to unity gain;
- streams 32 in / 34 out at 32–48 kHz, 16 / 18 at 64–96 kHz, and 8 / 10 at 128–192 kHz.
  The last two outputs are always the headphones.

### ALSA controls (what `hardware.py` reads and writes)

| Control | Access | Values |
| --- | --- | --- |
| `Sync Source` | rw | Internal, Input 1–4 |
| `Current Sync Source` | r | same items |
| `Current Rate`, `System Rate` | r | Hz |
| `Input N Sync` (N = 1–4) | r | No Lock, Lock, Sync |
| `Input N Format` | r | ADAT, S/PDIF |
| `Input N Rate` | r | Hz |
| `Output N Format` | rw | ADAT, S/PDIF |

Try them with `amixer -c <card> contents`.

### Known vendor requests (from the kernel quirk)

All are `bmRequestType 0x40` (vendor, host-to-device, device recipient):

| bRequest | Use in the kernel |
| --- | --- |
| 16 | control register 1: mixer enable, clock, sample rate, output formats (wValue = bits, wIndex = mask) |
| 17 | read status registers (device-to-host) |
| 18 | control register 2 |
| 21 | output gain: wValue `0x9000` = unity, wIndex `0x100 + channel` |
| 22 | input loopback: wValue `0x400` = off, wIndex = channel |

The **matrix mixer gain encoding is undocumented**. That's why Openface Mixer mixes in
PipeWire. If you capture TotalMix's USB traffic on Windows or macOS and work out the protocol,
please open an issue: a hardware-mixer backend would give zero-latency monitoring.

## Channel naming used in the UI

At single speed, inputs 1–32 are `AD1 1`…`AD4 8` (optical port, channel). Outputs 1–32 are named
the same way; outputs 33/34 are `Phones`. Playback channel *n* is named after output *n*, which
it feeds by default.

## PipeWire profiles

- **Multichannel Output**: output only, so there are no input meters and no monitoring.
- **Pro Audio**: exposes `pro-input-0` (32 ch) and `pro-output-0` (34 ch). Openface Mixer needs
  this profile for input monitoring and offers a button to switch to it.
