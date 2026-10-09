# Hardware notes

Openface Mixer supports the RME Digiface USB, the RME Fireface 802 and the RME Fireface 800
(both FireWire) and the RME HDSPe RayDAT (PCIe), and reads the RME ARC USB remote.

# RME Digiface USB

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

## Channel naming used in the UI (Digiface)

At single speed, inputs 1–32 are `AD1 1`…`AD4 8` (optical port, channel). Outputs 1–32 are named
the same way; outputs 33/34 are `Phones`. Playback channel *n* is named after output *n*, which
it feeds by default.

## PipeWire profiles (Digiface)

- **Multichannel Output**: output only. Monitoring and meters still work, because both happen
  in the interface, but apps can't record the inputs.
- **Pro Audio**: exposes `pro-input-0` (32 ch) and `pro-output-0` (34 ch), so apps can record
  every input and play to every playback channel. The settings panel offers a button for it.

# RME Fireface 802

The original Fireface 802 (not the 802 FS) has FireWire 400 and USB 2.0. On Linux only
**FireWire** gives access to its DSP mixer:

- **FireWire:** the kernel's `snd-fireface` driver (Linux 5.x and later, unit version `0x000005`)
  streams 30 in / 30 out at 32–48 kHz, 22 at 64–96 kHz and 14 at 128–192 kHz (no ADAT at quad
  speed on FireWire 400). It leaves the DSP to userspace, and the protocol is known from
  Takashi Sakamoto's [snd-firewire-ctl-services](https://github.com/alsa-project/snd-firewire-ctl-services)
  (`protocols/fireface/src/latter.rs`, `latter/ff802.rs`). Openface Mixer's engine uses it
  (`engine/fireface_fw.c`).
- **USB:** without RME's driver the 802 only works in Class Compliant mode, and RME says the
  mixer can't be remote-controlled in CC mode (only TotalMix FX for iPad does it, over a protocol
  nobody has documented). Openface Mixer doesn't support the 802 over USB.

Don't run `snd-fireface-ctl-service` at the same time: both would write the DSP.

## FireWire access

The engine opens the 802's node (`/dev/fwN`, found through `/sys/bus/firewire/devices/fwN.M`
with `specifier_id 0x000a35`, `version 0x000005`) and sends asynchronous transactions with the
firewire-core character device ioctls. That needs read/write access to `/dev/fwN`:
`packaging/70-rme-fireface.rules` gives it to the `audio` group and the logged-in user.

## Registers

All values are little-endian quadlets.

| Address | Access | Use |
| --- | --- | --- |
| `0xffff'0000'0014` | write | configuration: clock source bits 10–12 (0 internal, `0x400` word clock, `0x800` AES, `0xc00` ADAT A, `0x1000` ADAT B), `0x200` AES in from optical, `0x100` S/PDIF on optical out, `0x40` DSP effects on inputs, `0x20` AES out professional, `0x10` word clock out single speed, `0x2000` MIDI to address offset 0 (the kernel driver's) |
| `0xffff'0000'001c` | write | DSP command (below) |
| `0x0000'801c'0000` | read | sync status: rate bits 28–31, clock source bits 9–11 (`0xe00` internal, `0x200` word, `0x400` AES, `0x600` ADAT A, `0x800` ADAT B), sync bits 4–7 and lock bits 0–3 (word, AES, ADAT A, ADAT B), detected rate per input in bits 12–27 |
| `0xffff'ff00'0000` | read block | meters (below) |

The device can't report its DSP settings back, so the engine keeps its own copy and sends
everything when it connects. Rates use RME's codes: 0 = 32k, 1 = 44.1k, 2 = 48k, 4 = 64k,
5 = 88.2k, 6 = 96k, 8 = 128k, 9 = 176.4k, 10 = 192k.

## DSP commands

Each command is one quadlet with **odd parity** in bit 31 (bit 31 set when bits 0–30 have an even
number of ones).

- **Channel settings:** `(channel << 24) | (command << 16) | value`. Channels 0–29 are the
  hardware inputs, 30–59 the hardware outputs, `0x3c` the FX unit.

  | Command | Inputs | Outputs |
  | --- | --- | --- |
  | `0x00` | | volume, dB × 10, −650…60 |
  | `0x01` | FX send, dB × 10 | stereo balance |
  | `0x02` | stereo link | |
  | `0x03` | | FX return, dB × 10 |
  | `0x04` | | stereo link |
  | `0x06` / `0x07` | phase invert / AN 1–8 gain (0–120 = 0–12 dB) | — / phase invert |
  | `0x08` | AN 1–8 level (0 Lo Gain, 1 +4 dBu); AN 9–12 48V | AN 1–8 level (0 −10 dBV, 1 +4 dBu, 2 Hi Gain) |
  | `0x09` | AN 9–12 Inst | |
  | `0x20`–`0x22` | low cut on, frequency, slope | same |
  | `0x40`–`0x4b` | 3-band EQ | same |
  | `0x60`–`0x67` | dynamics | same |
  | `0x80`–`0x83` | auto level | same |

  FX unit: `0x00`–`0x0d` reverb, `0x20`–`0x26` echo (`0x00`/`0x20` switch them on).
- **Mixer:** `0x40000000 | ((0x40 * mixer + source) << 16) | gain`. Mixers 0–29 feed the
  hardware outputs, 30/31 the FX unit. Sources 0–29 are the hardware inputs, 30/31 the FX
  returns, 32–61 playback. Gain: 0 = off, else `0x8000 | linear × 0x1000`, so `0x9000` = unity
  and `0xa000` = +6 dB, the same encoding as the Digiface's gains of −6 dB and up.

Openface Mixer has controls for phase, gain, level, 48V and Inst, and switches low cut, EQ,
dynamics, auto level, FX sends and returns, reverb and echo off, since it can't show them yet.
Muted outputs get no mixer signal at all, because the volume only goes down to −65 dB.

## Meters

Each block read of 392 bytes at `0xffff'ff00'0000` returns one chunk; successive reads cycle
through five. The last quadlet tags the chunk: `0x11111111` hardware outputs, `0x22222222`
input channel strips, `0x33333333` playback, `0x55555555` FX bus, `0x66666666` hardware
inputs. Bytes 0–255 hold 32 64-bit values, bytes 256–383 32 quadlets, masked with `0x07fffff0`
(full scale). The engine reads all five every 30 ms and uses the quadlets as peaks.

## Channel naming

TotalMix's names: inputs `AN 1`–`AN 12` (9–12 are the front mic/instrument inputs), `AES L/R`,
`A 1`–`A 8` and `B 1`–`B 8` (ADAT ports); outputs `AN 1`–`AN 8`, `PH 9`–`PH 12` (two phones
pairs), `AES`, `A`, `B`. The DSP keeps the same channel numbers at every rate; at 2x speed only
ADAT channels 1–4 of each port exist, at 4x none.

## Not verified on hardware yet

Everything above comes from snd-firewire-ctl-services and the kernel driver, not from captures
on an 802 with Openface Mixer. Worth checking first: the meter chunks (which values are peaks),
the PipeWire node name of the 802's playback device (`alsa_output.firewire-0x000a35…`), mixer
and channel numbers at 96/192 kHz, and the AN 1–8 input gain.

# RME Fireface 800

The Fireface 800 is FireWire only (FireWire 800 and 400). The kernel's `snd-fireface` driver
(unit version `0x000001`) streams 28 in / 28 out at 32–48 kHz, 20 at 64–96 kHz and 12 at
128–192 kHz, and leaves the mixer to userspace. It belongs to RME's older "former" protocol
family (with the Fireface 400), which is nothing like the 802's DSP commands: the mixer, the
output volumes and the configuration are memory blocks written with block write transactions.
The layout comes from snd-firewire-ctl-services (`protocols/fireface/src/former.rs`,
`former/ff800.rs`) and the kernel's `ff-protocol-former.c`. The engine finds the node the same
way as the 802's (`specifier_id 0x000a35`, `version 0x000001`), with the same udev rule.

## Registers

All values are little-endian quadlets, written as blocks.

| Address | Access | Use |
| --- | --- | --- |
| `0x0000'8008'0000` | write block | mixer: 28 blocks of 64 quadlets, one per output; quadlets 0–27 are the hardware inputs, 32–59 playback |
| `0x0000'8008'1f80` | write block | output volumes, 28 quadlets |
| `0x0000'fc88'f014` | write block | configuration, 3 quadlets (write only, below) |
| `0x0000'801c'0000` | read block | status, 2 quadlets (below) |
| `0x0000'8010'0000` | read block | meters, 1008 bytes: 84 64-bit values, then 84 quadlets (inputs, playback, outputs), full scale `0x7fffff00` |

Gains and volumes are linear × `0x8000`: 0 = off, `0x8000` = unity, `0x10000` = +6 dB. The output
volume reaches silence, so a muted output is simply volume 0.

Channels: inputs 0–9 `AN 1`–`AN 10`, 10/11 `SPDIF L/R`, 12–19 `A1 1`–`A1 8`, 20–27
`A2 1`–`A2 8`; outputs the same with 8/9 the phones (`PH 9/10`). AN 1 is the front instrument
jack or rear line 1, AN 7/8 front mic or rear line, AN 9/10 the rear mic inputs.

## Configuration

| Quadlet | Bits |
| --- | --- |
| 0 | line in level `0x08` Lo Gain, `0x10` +4 dBu, `0x20` −10 dBV; line out level `0x400` Hi Gain, `0x800` +4 dBu, `0x1000` −10 dBV; 48V `0x01` AN 7, `0x80` AN 8, `0x02` AN 9, `0x100` AN 10; AN 1 instrument `0x200` drive, `0x04` speaker emulation |
| 1 | line in level `0x0` Lo Gain, `0x2` +4 dBu, `0x3` −10 dBV; line out level `0x10` Hi Gain, `0x18` +4 dBu, `0x08` −10 dBV; jacks (rear, front) AN 1 (`0x4`, `0x800`), AN 7 (`0x40`, `0x20`), AN 8 (`0x100`, `0x80`), both set = front + rear; `0x200` drive again |
| 2 | clock `0x1` internal, `0x0` ADAT 1, `0x400` ADAT 2, `0xc00` S/PDIF, `0x1400` word clock, `0x1c00` TCO; `0x1e` allow 44.1/48 kHz at all speeds; `0x200` S/PDIF in optical; `0x100` S/PDIF on optical out; `0x20` S/PDIF out professional, `0x40` emphasis, `0x80` non-audio; `0x2000` word clock out single speed; `0x10000` AN 1 limiter; `0x80000000` continue at errors |

## Status

Quadlet 0: lock/sync per input (ADAT 1 `0x1000`/`0x400`, ADAT 2 `0x2000`/`0x800`, S/PDIF
`0x40000`/`0x100000`, word clock `0x20000000`/`0x40000000`), active clock source in bits 22–24,
external rate in bits 25–28, S/PDIF rate in bits 14–17. Quadlet 1 reflects the configuration:
clock source and rate (bits 1–4, which the kernel driver uses as the sample rate), S/PDIF in
optical `0x200`, optical out S/PDIF `0x100`, S/PDIF out emphasis `0x40` and professional
`0x20`, word clock single speed `0x2000`. Openface Mixer takes those options from quadlet 1 the
first time the 800 connects; the rest of the configuration starts at defaults.

## Fireface 800 not verified on hardware yet

Nobody has run Openface Mixer on an 800 yet. Worth checking first: that mixing and output
volumes work at all, the meter layout (snd-firewire-ctl-services uses the last 84 quadlets),
the lock/sync bits (the kernel's `/proc` dump reads some of them the other way round from
snd-firewire-ctl-services, which Openface Mixer follows), and the mixer and playback slots at
2x/4x speed.

# RME HDSPe RayDAT

The RayDAT is a PCIe card with four ADAT ports, AES and S/PDIF. The kernel's `snd-hdspm` driver
(firmware revision 211) streams 36 in / 36 out at 32–48 kHz, 20 at 64–96 kHz and 12 at
128–192 kHz, and, unlike the USB and FireWire drivers, already exposes the card's hardware
mixer to userspace. That is what alsa-tools' `hdspmixer` uses, and what Openface Mixer's engine
uses too (`engine/raydat.c`). No low-level protocol and no udev rule are needed: the engine
opens the card's `/dev/snd/controlC<n>` and `/dev/snd/hwC<n>D0`, found by driver name `HDSPM`
and short name `RME RayDAT_<serial>`.

## Mixer

The ALSA control `Mixer` (iface HWDEP) takes three integers: source, destination, gain.

| Value | Meaning |
| --- | --- |
| source | 0–63 hardware input, 64–127 playback 0–63 |
| destination | hardware output 0–63 |
| gain | 0–65535, linear, 32768 = unity (so at most +6 dB) |

The hardware matrix is write only; the driver keeps a copy, which reads of the control return.
The driver zeroes the whole mixer when it loads, so the card is silent until a mixer program
sets it up. It answers writes with `EBUSY` while playback and capture are open in two different
processes. There are no output faders: like TotalMix and hdspmixer, Openface Mixer multiplies
each send by its output's master level. The engine writes only changed crosspoints, reads one
output's sends back from the driver every 500 ms, and sends everything again if they differ (the
driver was reloaded, or another program changed the mixer).

## Channels

The mixer, the meters and Openface Mixer use the card's own channel numbers: 0/1 AES, 2/3
S/PDIF, then ADAT 1–4 from 4 up (8 channels per port at single speed, 4 at double, 2 at quad,
packed together). ALSA's PCM channels are ordered differently (ADAT first, then AES and
S/PDIF), so PipeWire's `playback_AUX0/1`, which the playback sink feeds, are ADAT 1 channels
1/2. The engine finds that PipeWire node by its ALSA card name (`alsa.card_name`), since PCI
node names only carry the slot.

## Meters and status

The hwdep ioctl `SNDRV_HDSPM_IOCTL_GET_PEAK_RMS` returns peak and RMS values for 64 inputs,
playback channels and outputs. Peaks: bits 8–30 are the level, full scale `0x7fffff`; bits 0–3
count overs. `SNDRV_HDSPM_IOCTL_GET_CONFIG` gives the sample rate.

The settings are ordinary ALSA mixer controls, which the GUI reads and writes with amixer:

| Control | Access | Values |
| --- | --- | --- |
| `Clock Mode` | rw | Master, AutoSync |
| `Pref Sync Ref` | rw | Word Clock, ADAT 1–4, AES, SPDIF, (TCO,) Sync In |
| `Internal Clock` | rw | 32 kHz … 192 kHz (the rate as master) |
| `System Sample Rate` | rw | Hz |
| `S/PDIF Out Professional`, `Single Speed WordClock Out` | rw | on/off |
| `WC`, `AES`, `SPDIF`, `ADAT1`–`ADAT4`, `TCO`, `SYNC IN` `SyncCheck` | r | No Lock, Lock, Sync, N/A |
| the same with `Frequency` | r | No Lock, 32 kHz … 192 kHz |

## RayDAT not verified on hardware yet

Nobody has run Openface Mixer on a RayDAT yet. Worth checking first: that the mixer accepts
writes with PipeWire running (the `EBUSY` rule above compares the processes that opened
playback and capture), the PipeWire card name property the engine matches, the meter scale,
and the channel layout at 2x/4x speed.

# RME ARC USB

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
itself; Openface Mixer never does, so the ARC stays in note mode. Key LEDs are lit by sending the
same note back with velocity `0x7F` and switched off with `0x00` (tested on the unit). They are
on/off only: lower velocities don't give a dimmer level. While nothing drives them, Talkback and
Speaker B glow faintly as the ARC's power and USB indicators, and Dim glows faintly once
TotalMix has connected.
