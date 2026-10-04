# Openface Mixer

A TotalMix-style mixer and control panel for the **RME Digiface USB** on Linux.

RME's TotalMix FX only runs on Windows and macOS. Linux has supported the Digiface USB for
streaming since kernel 6.12, but nothing controlled its routing. Openface Mixer adds the
familiar TotalMix workflow on top of PipeWire.

- **Mixer view:** Hardware Inputs, Software Playback and Hardware Outputs, one row each.
  Pick an output, and the input and playback faders set the submix sent to it.
- **Matrix view:** route any input or playback channel to any output with a click.
- **Presets:** 8 snapshot slots, like TotalMix, plus export and import of mixes as files.
- **Fader groups:** 4 groups that move together relatively. Hold Shift to move one fader alone.
- **Channel strips:** pan, mute, stereo link, peak meters with clip indicators, and output master faders.
- **Hardware panel:** clock source, ADAT or S/PDIF per optical port, and input lock/sync/rate status.
- **Always on:** the mix keeps running with the window closed. A small engine service restores
  it at login.

> **How mixing works:** the Digiface's internal DSP mixer protocol is not public, and the Linux
> driver disables it. Openface Mixer mixes in software inside PipeWire instead. Input monitoring
> therefore has your PipeWire round-trip latency (a few ms at small buffer sizes), not
> TotalMix's near-zero hardware latency. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- Linux 6.12 or newer (Digiface USB support in `snd-usb-audio`)
- PipeWire with WirePlumber
- Python 3.10+ with PySide6
- Build tools: a C compiler, `pkg-config`, and the libpipewire development headers
- `amixer` (alsa-utils) and `pactl` (libpulse)

On Arch / CachyOS:

```sh
sudo pacman -S --needed pyside6 libpipewire alsa-utils libpulse gcc pkgconf make
```

On Debian / Ubuntu:

```sh
sudo apt install python3-pyside6.qtwidgets libpipewire-0.3-dev alsa-utils pulseaudio-utils build-essential pkg-config
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
2. For input monitoring, the Digiface must be on PipeWire's **Pro Audio** profile. Click
   *Enable hardware inputs* in the settings panel, or pick the profile in your sound settings.
3. To send desktop audio through the mixer, select **Openface Mixer Playback** as your output
   device. It feeds playback channels 1/2. Connect other apps or a DAW to `openface_mixer:play_N`
   with qpwgraph or Helvum.
4. Click an output's name tag (bottom row) to select its submix, then raise input and playback
   faders. Alternatively, click cells in the **Matrix** view.

### Controls

| Action | How |
| --- | --- |
| Fine fader move | Ctrl + drag (or Ctrl + scroll) |
| Fader to 0 dB | Double-click or Alt-click |
| Centre pan | Double-click the knob |
| Reset peak / clip | Click the meter |
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
- Mixing is in software (see above). If someone documents the hardware mixer's USB protocol,
  the engine could drive it directly.
- No solo, EQ, dynamics or reverb. This is the "lite" subset of TotalMix.

## License

MIT. See [LICENSE](LICENSE). Not affiliated with RME (Audio AG). "TotalMix" and "Digiface" are
trademarks of their respective owners.
