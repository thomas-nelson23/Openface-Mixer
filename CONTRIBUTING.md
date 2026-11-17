# Contributing

Thanks for helping! This document covers how the project is laid out and how to work on it.

## Repository layout

```
engine/                 C engine that drives the interface's DSP mixer. Builds to engine/openface-mixer-engine
  openface-mixer-engine.c
  matrix_file.c         reads matrix.bin, the mix the engine starts with; refuses unknown files
  backend.h             what a device backend provides (open, status, sync, meters)
  backend_digiface.c    the Digiface backend, on top of digiface_usb.c
  digiface_usb.c        libusb: mixer nodes, output faders, status, level meters
  fireface_fw.c         Fireface 802, 800 and 400 backends: FireWire transactions for mixer, settings, meters
  fireface_usb.c        Fireface 802 and UCX II over USB (Class Compliant): SysEx through the ALSA sequencer
  hdspe.c               HDSPe RayDAT/AIO/AIO Pro/AES backend: snd-hdspm/snd-hdspe Mixer control and meters
  shm_layout.h          shared-memory contract between engine and GUI. Keep in sync with engine.py
openface_mixer/         Python GUI package (run with `python3 -m openface_mixer`)
  app.py                MainWindow: builds the UI and wires model ⇄ widgets ⇄ engine
  model.py              mixer state, fader taper, gain-matrix maths, fader groups (no Qt!)
  presets.py            8-slot preset bank + mix file export/import (no Qt)
  control_room.py       Dim, Mono, Speaker B, Talkback, Ext In on top of the matrix (no Qt)
  arc.py                ARC USB remote: find it, parse its MIDI, key layout and LEDs (no Qt)
  control_panel.py      Control Room settings box and the ARC USB port watcher
  config.py             file locations (per device), state load/save
  devices.py            supported devices: channel layout, names, engine and service names
  fireface802.py        Fireface 802 channel layout, settings and DSP command words (no Qt)
  fireface800.py        Fireface 800 channel layout, configuration and status words (no Qt)
  fireface400.py        Fireface 400 differences from the 800 (no Qt)
  ucx2.py               Fireface UCX II differences from the 802 over USB (no Qt)
  hdspe.py              HDSPe card models and their ALSA settings controls (no Qt)
  fx.py                 TotalMix FX effects (802, UCX II): model, defaults and DSP commands (no Qt)
  fx_panels.py          the EQ, Dynamics, Room EQ and Reverb / Echo panels
  osc_remote.py         TotalMix Global OSC: codec, fader curve, state ⇄ addresses (no Qt)
  remote_link.py        the OSC remote's UDP socket, sidebar section and autostart entry
  channel_settings.py   per-channel settings popup (phase, 48V, Inst, level, gain, jacks)
  engine.py             shared-memory client + starting/stopping the engine
  hardware.py           Digiface and HDSPe ALSA controls via amixer, PipeWire card profile via pactl
  widgets.py            custom-painted Meter, Fader, Knob, channel Strip, Row
  wheel.py              mouse wheel and touchpad steps for faders, knobs and matrix cells (no Qt)
  matrix_view.py        the routing grid
  settings_panel.py     right-hand hardware / engine panel
  updater.py            the Updates section's work: check and update the checkout it was installed from (no Qt)
  theme.py              colours, bundled fonts and the Qt stylesheet
  fonts/                Sora, JetBrains Mono, Michroma (SIL OFL)
packaging/              .desktop file, systemd user units, udev rules; aur/ has the Arch package recipe
scripts/                install.sh / uninstall.sh (user install to ~/.local, or staged for a package with DESTDIR)
docs/                   ARCHITECTURE.md (how it fits together), HARDWARE.md (device notes)
tests/                  unittest suite for the Qt-free modules, the install scripts, and an offscreen GUI smoke test
```

## Getting started

```sh
make            # build the engine
make test       # run unit tests
make lint       # byte-compile and pyflakes (pip install pyflakes, or your distribution's package)
make run        # run the GUI from the checkout
```

`make run` uses the engine binary in `engine/` if no engine is running. If you have the
installed service running, the GUI attaches to it. To test engine changes, stop the service
(`systemctl --user stop openface-mixer-engine`) and run `make run-engine` in another terminal.

## Guidelines

- **Keep `model.py` and `presets.py` free of Qt imports.** They hold the logic and are what the
  tests cover. Widgets should stay presentational: they emit signals, and `app.py` updates the model.
- **The engine never blocks on the GUI.** Communication goes only through the shared-memory
  struct, and USB transfers stay on the engine's main loop and meter thread.
- **Shared-memory changes** must update `engine/shm_layout.h` *and* the constants in
  `openface_mixer/engine.py`, and bump `OFM_SHM_VERSION`.
- **State format changes:** bump `STATE_VERSION` in `model.py` and teach `upgrade_state()` how to
  read the old format, so users don't lose their mixes.
- Python style: PEP 8, 100-column lines, standard library only (plus PySide6).
  C style: kernel/PipeWire-like (tabs, `snake_case`).
- Add or update tests for model and preset changes. Run `make test lint` before opening a PR.

## Testing without hardware

The unit tests need no audio device. Without the interface the engine still runs and reports
`no-device`; `--no-autolink` keeps it from linking the playback sink. `make run-engine DEVICE=ff802`
runs the Fireface 802 engine, and `python3 -m openface_mixer --device ff802` its GUI (`ff802usb`
for the 802 over USB, `ff800` for the Fireface 800, `raydat` for the HDSPe RayDAT).

The GUI also runs offscreen (`QT_QPA_PLATFORM=offscreen`), which is useful for screenshots
and smoke tests. `tests/test_gui_smoke.py` does that for every device with a stub engine and a
temporary HOME: it opens the window, both views, the settings and every effects panel, folds
and unfolds each row, and fails on any exception or Qt warning. It runs with `make test` where
PySide6 is installed, and in CI. When you add a window, a panel or a device, make the smoke
test open it.
