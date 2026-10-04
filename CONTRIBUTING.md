# Contributing

Thanks for helping! This document covers how the project is laid out and how to work on it.

## Repository layout

```
engine/                 C real-time mixer (PipeWire filter). Builds to engine/openface-mixer-engine
  openface-mixer-engine.c
  shm_layout.h          shared-memory contract between engine and GUI. Keep in sync with engine.py
openface_mixer/         Python GUI package (run with `python3 -m openface_mixer`)
  app.py                MainWindow: builds the UI and wires model ⇄ widgets ⇄ engine
  model.py              mixer state, fader taper, gain-matrix maths, fader groups (no Qt!)
  presets.py            8-slot preset bank + mix file export/import (no Qt)
  config.py             file locations, state load/save
  engine.py             shared-memory client + starting/stopping the engine
  hardware.py           Digiface ALSA controls via amixer, PipeWire card profile via pactl
  widgets.py            custom-painted Meter, Fader, Knob, channel Strip, Row
  matrix_view.py        the routing grid
  settings_panel.py     right-hand hardware / engine panel
  theme.py              colours and the Qt stylesheet
packaging/              .desktop file and systemd user unit
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
- **Real-time safety in the engine:** nothing in `on_process()` may allocate, lock, log or make
  syscalls. Communication with the GUI goes only through the shared-memory struct.
- **Shared-memory changes** must update `engine/shm_layout.h` *and* the constants in
  `openface_mixer/engine.py`, and bump `OFM_SHM_VERSION`.
- **State format changes:** bump `STATE_VERSION` in `model.py` and teach `upgrade_state()` how to
  read the old format, so users don't lose their mixes.
- Python style: PEP 8, 100-column lines, standard library only (plus PySide6).
  C style: kernel/PipeWire-like (tabs, `snake_case`).
- Add or update tests for model and preset changes. Run `make test lint` before opening a PR.

## Testing without hardware

The unit tests need no audio device. To exercise the engine without a Digiface (or without
making sound), use `--no-autolink` and wire it to a null sink:

```sh
pactl load-module module-null-sink sink_name=ofm_test
./engine/openface-mixer-engine --no-autolink &
pw-link openface_mixer:out_1 ofm_test:playback_FL
```

The GUI also runs offscreen (`QT_QPA_PLATFORM=offscreen`), which is useful for screenshots
and smoke tests.
