# Contributing

Thanks for helping! This document covers how the project is laid out and how to work on it.

## Repository layout

```
engine/                 C engine that drives the interface's DSP mixer. Builds to engine/openface-mixer-engine
  openface-mixer-engine.c
  backend.h             what a device backend provides (open, status, sync, meters)
  backend_digiface.c    the Digiface backend, on top of digiface_usb.c
  digiface_usb.c        libusb: mixer nodes, output faders, status, level meters
  fireface_fw.c         Fireface 802 backend: FireWire transactions for mixer, settings, meters
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
  channel_settings.py   per-channel settings popup (phase, 48V, Inst, level, gain)
  engine.py             shared-memory client + starting/stopping the engine
  hardware.py           Digiface ALSA controls via amixer, PipeWire card profile via pactl
  widgets.py            custom-painted Meter, Fader, Knob, channel Strip, Row
  matrix_view.py        the routing grid
  settings_panel.py     right-hand hardware / engine panel
  theme.py              colours and the Qt stylesheet
packaging/              .desktop file, systemd user units, udev rules
scripts/                install.sh / uninstall.sh (user install to ~/.local)
docs/                   ARCHITECTURE.md (how it fits together), HARDWARE.md (device notes)
tests/                  unittest suite for the Qt-free modules
```

## Getting started

```sh
make            # build the engine
make test       # run unit tests
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
runs the Fireface 802 engine, and `python3 -m openface_mixer --device ff802` its GUI.

The GUI also runs offscreen (`QT_QPA_PLATFORM=offscreen`), which is useful for screenshots
and smoke tests.
