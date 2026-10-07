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

## USB protocol (hardware mixer)

The kernel binds only interface 0. Interface 1 carries the DSP mixer, and Openface Mixer's
engine claims it with libusb (`engine/digiface_usb.c`). The protocol below comes from the kernel
quirk and from Asahi Lina's [rmectl](https://github.com/hoshinolina/rmectl).

Vendor control requests (`bmRequestType 0x40`, device recipient; 17 is device-to-host):

| bRequest | Use |
| --- | --- |
| 16 | control register 1 (wValue = bits, wIndex = mask): clock source bits 0–2, rate bits 3–6, speed mode bits 12–14, **bit 10 = mixer off** |
| 17 | read 16 bytes of status; word 1 bits 20–23 = current rate index (32k, 44.1k, 48k, –, 64k, …), word 3 mirrors control register 1 (low half) and 2 (high half) |
| 18 | control register 2: output formats, bit 6 = TMS off, **bit 8 = mixer off** |
| 21 | output fader: wValue = gain, wIndex = `0x100 + output channel` |
| 22 | input loopback: wValue = 1/0, wIndex = `0x100 + channel` |

Mixer commands go to **bulk OUT endpoint 0x0B** as little-endian 32-bit words:

| Word | Meaning |
| --- | --- |
| `(node << 16) \| gain` | set a node's gain |
| `0x40000000 \| (node << 16) \| (dst << 9) \| src` | route a node: `src` = input n or `0x100 + playback n`, `dst` = output channel |
| `0xC000FFFF \| (node << 16)` | reset a node |

There are **2048 nodes** (crosspoints in use at once). Gains: `0x8000` = unity, linear, max
`0x10000` (+6 dB). Values of `0x4000` and up are stored shifted right by 3 with bit 15 set, so
unity is `0x9000`, which is also what the kernel writes to the output faders. The device does
not report the matrix back, so the engine keeps its own copy and rewrites everything when status
word 3 shows the mixer was switched off (the driver does that on every probe).

Interrupt IN endpoint 0x83 is rate feedback.

### Level meters (bulk IN endpoint 0x84)

The device always has a frame ready; each 1024-byte transfer returns one frame of 128
little-endian 64-bit words, and successive frames cycle through three types:

| Words | Content |
| --- | --- |
| 0–33 | RMS per channel: mean square × 2^55, smoothed by the device (falls about 25 dB/s) |
| 64–80 | peak per channel, two per word (even channel in the low 32 bits), full scale = 2^27 |
| 100–127 | end marker `0xFFFFFFFn_FFFFFFFn`, n = type: 0 inputs, 1 playback, 2 outputs |

Measured on a Digiface (firmware in vendor mode, 48 kHz): a −20 dBFS sine on playback 1 reads
peak `0xCCC000` (−20.00 dB) and RMS 10·log10(E) = 142.56, i.e. E = 0.005 × 2^55. Output meters
are post-mixer and post-fader, and matched the expected mix gains within 0.05 dB. The engine
polls all three frame types every 10 ms on a separate thread.

Access needs the udev rule in `packaging/70-rme-digiface.rules`.

## RME ARC USB

The ARC USB (`2a39:0101`, "RME ARC") is a class-compliant USB MIDI device. `snd-usb-audio`
makes it an ALSA card named `ARC` with one rawmidi port, `/dev/snd/midiC<card>D0`, which members
of the `audio` group can open. Openface Mixer reads it directly (`openface_mixer/arc.py`).

RME doesn't publish its MIDI messages. Captured from a unit in its default (note) mode:

| Control | Message |
| --- | --- |
| Keys, row by row then Talkback, Speaker B, Dim | Note On ch 1, notes `0x36`–`0x44`, velocity `0x7F` press / `0x00` release |
| Encoder | CC 16 ch 1, relative signed-bit: `0x01`–`0x3F` up, `0x41`–`0x7F` down |
| Idle | nothing (no active sensing) |

Newer firmware can also switch to a SysEx mode (`F0 00 20 0D …`), which TotalMix FX turns on
itself; Openface Mixer never does, so the ARC stays in note mode. LEDs are lit by sending the
same note back with velocity `0x7F` (off: `0x00`).

## Channel naming used in the UI

At single speed, inputs 1–32 are `AD1 1`…`AD4 8` (optical port, channel). Outputs 1–32 are named
the same way; outputs 33/34 are `Phones`. Playback channel *n* is named after output *n*, which
it feeds by default.

## PipeWire profiles

- **Multichannel Output**: output only. Monitoring and meters still work, because both happen
  in the interface, but apps can't record the inputs.
- **Pro Audio**: exposes `pro-input-0` (32 ch) and `pro-output-0` (34 ch), so apps can record
  every input and play to every playback channel. The settings panel offers a button for it.
