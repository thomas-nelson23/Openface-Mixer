# Openface Mixer

A TotalMix-style mixer and control panel for the **RME Digiface USB** on Linux.

![Openface Mixer: mixer view with input, playback and output rows, presets and hardware panel](docs/screenshots/mixer.png)

RME's TotalMix FX only runs on Windows and macOS. Linux has supported the Digiface USB for
streaming since kernel 6.12, but nothing controlled its routing. Openface Mixer drives the
Digiface's own DSP mixer over USB, with the familiar TotalMix workflow.

- **Mixer view:** Hardware Inputs, Playback and Hardware Outputs, one row each.
  Pick an output, and the input and playback faders set the submix sent to it.
- **Matrix view:** route any input or playback channel to any output with a click.
- **Presets:** 8 snapshot slots, like TotalMix, plus export and import of mixes as files.
- **Fader groups:** 4 groups that move together relatively. Hold Shift to move one fader alone.
- **Channel strips:** name tag on top, pan, mute, solo, stereo link, peak meters with clip
  indicators, and output master faders.
- **Solo like TotalMix:** solo-in-place, post fader, in the current submix only. The **SOLO**
  button in the top bar lights while anything is soloed and switches all solos off and back on.
- **Hardware panel:** clock source, ADAT or S/PDIF per optical port, and input lock/sync/rate status.
- **Always on:** the mix keeps running with the window closed. A small engine service restores
  it at login.

> **How mixing works:** Openface Mixer drives the Digiface's own DSP mixer over USB, like
> TotalMix, so input monitoring has no added latency and the mix keeps running in the
> interface even with the computer idle. There is no software mixing. See
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/HARDWARE.md](docs/HARDWARE.md).

### Matrix view

![Matrix view: click a cell to route any input or playback channel to any output](docs/screenshots/matrix.png)

## Requirements

- Linux 6.12 or newer (Digiface USB support in `snd-usb-audio`)
- PipeWire with WirePlumber
- Python 3.10+ with PySide6
- libusb 1.0 and a udev rule for the Digiface (installed by `make install`)
- Build tools: a C compiler, `pkg-config`, and the libpipewire development headers
- `amixer` (alsa-utils) and `pactl` (libpulse)

On Arch / CachyOS:

```sh
sudo pacman -S --needed pyside6 libpipewire libusb alsa-utils libpulse gcc pkgconf make
```

On Debian / Ubuntu:

```sh
sudo apt install python3-pyside6.qtwidgets libpipewire-0.3-dev libusb-1.0-0-dev alsa-utils pulseaudio-utils build-essential pkg-config
```

## Install

```sh
git clone https://github.com/thomas-nelson23/Openface-Mixer.git
cd Openface-Mixer
make install
```

This installs to `~/.local`, adds **Openface Mixer** to your app menu, and enables the
`openface-mixer-engine` user service. Remove it with `make uninstall`.

## First run

1. Open **Openface Mixer**.
2. Monitoring works in any PipeWire profile, because the mix happens in the interface. To
   *record* all 32 inputs in apps, switch the Digiface to the **Pro Audio** profile: click
   *Record all inputs* in the settings panel, or pick the profile in your sound settings.
3. To send desktop audio through the mixer, select **Openface Mixer Playback** as your output
   device. It feeds playback channels 1/2. Send other apps or a DAW to the Digiface's own output
   channels (playback channel *n* = Digiface output *n*) with qpwgraph or Helvum.
4. Click an output's name tag (bottom row) to select its submix, then raise input and playback
   faders. Alternatively, click cells in the **Matrix** view.

### Controls

| Action | How |
| --- | --- |
| Fine fader move | Ctrl + drag (or Ctrl + scroll) |
| Fader to 0 dB | Double-click or Alt-click |
| Centre pan | Double-click the knob |
| Reset peak / clip | Click the meter |
| Solo | **S** on a strip: solo-in-place in the current submix. **SOLO** (top bar) switches all solos off, and back on |
| Store a preset | Click **Store**, then a slot (or right-click a slot) |
| Recall a preset | Click a lit slot |
| Fader groups | Right-click a strip → *Fader group*. Hold Shift to move one fader alone. |
| Matrix | Click a cell to toggle 0 dB / off, scroll to adjust, right-click to clear |

## Files

| Path | Contents |
| --- | --- |
| `~/.config/openface-mixer/state.json` | Current mix and UI state |
| `~/.config/openface-mixer/presets.json` | The 8 preset slots |
| `~/.config/openface-mixer/matrix.bin` | Flattened gain matrix that the engine loads at startup |
| `~/.cache/openface-mixer/engine.log` | Engine log (only when started without systemd) |

## Development

```sh
make          # build the engine
make test     # unit tests (no hardware or display needed)
make run      # run the GUI from the source tree
make help     # list all targets
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first.
Hardware notes are in [docs/HARDWARE.md](docs/HARDWARE.md).

## Status and limitations

- Only the Digiface USB is supported, and only tested at 48 kHz (single speed). The UI is designed
  to adapt to 2x/4x channel counts, but that path is untested.
- The hardware mixer allows 2048 active routes; the settings panel warns if a mix needs more.
- The mixer needs USB access to the Digiface (the udev rule installed by `make install`).
  Without it nothing is mixed, and the settings panel says why.
- Meters come from the interface itself: inputs, playback and the hardware outputs (post mix).
- Solo has no PFL / live mode or exclusive mode yet.
- No EQ, dynamics or reverb: the Digiface's DSP has no effects.

## Acknowledgements

- **Asahi Lina** wrote the Digiface USB support in the Linux kernel and
  [rmectl](https://github.com/hoshinolina/rmectl). The USB protocol for the hardware mixer comes
  from those two; the level-meter format was decoded for this project
  (see [docs/HARDWARE.md](docs/HARDWARE.md)).
- [oscmix](https://github.com/michaelforney/oscmix) by Michael Forney does the same job for the
  Fireface UCX II and was the model for controlling the interface's own mixer instead of mixing
  in software. It does not support the Digiface and none of its code or protocol is used here.

## License

MIT. See [LICENSE](LICENSE). Not affiliated with RME (Audio AG). "TotalMix" and "Digiface" are
trademarks of their respective owners.
