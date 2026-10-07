# Architecture

Openface Mixer is two programs that share one block of memory:

```
                ┌──────────────────────────── PipeWire graph ─────────────────────────────┐
                │                                                                          │
 Digiface ──capture_AUX0..31──▶ in_1..in_32  ┐                                             │
 (Pro Audio input node)         │            │   openface_mixer        out_1..out_34 ──playback_AUX0..33──▶ Digiface
                │               │            ├─▶  (pw_filter, C)  ──▶                      │   (output node)
 apps ──▶ "Openface Mixer       │            │   gain matrix 34×66                         │
          Playback" sink ──────▶ play_1/2   ┘                                             │
 DAW / qpwgraph ──────────────▶ play_3..34                                                 │
                └──────────────────────────────────────────────────────────────────────────┘
                                         ▲  gains           │ peak meters, status
                                         │                  ▼
                              /dev/shm/openface-mixer-<uid>   (struct ofm_shm, engine/shm_layout.h)
                                         ▲                  │
                                         │                  ▼
                                   Python GUI (openface_mixer/)  ──amixer──▶ ALSA controls (clock, formats)
```

## Engine (`engine/openface-mixer-engine.c`)

- A PipeWire **filter node** named `openface_mixer` with 66 mono input ports (32 hardware inputs
  plus 34 playback channels) and 34 mono output ports.
- In each audio cycle (`on_process`), it computes `out[o] = Σ gain[o][k] · src[k]`, skipping zero
  gains. When a gain changes, it ramps linearly over one cycle to avoid zipper noise. Peak meters
  are max-held into shared memory.
- It watches the PipeWire registry and **links itself** to the Digiface nodes (matched by name
  prefix `alsa_output/alsa_input.usb-RME_Digiface_USB`), re-linking on hotplug or profile changes.
- It loads `libpipewire-module-loopback` to create the **Openface Mixer Playback** sink, whose
  output is linked to `play_1/2`.
- It runs as a systemd **user service**, so the mix survives closing the GUI. At startup it loads
  `~/.config/openface-mixer/matrix.bin` (mixer mode, gain matrix, output gains), which the GUI
  keeps up to date.
- **Hardware mode** (the default): a 20 ms main-loop timer (`hw_tick`) opens the Digiface's
  mixer interface with libusb and sends only the changed crosspoints and output faders
  (`engine/digiface_usb.c`, protocol in [HARDWARE.md](HARDWARE.md)). The audio callback then
  just passes `play_N` to `out_N`, i.e. to the Digiface's playback channel N, which the DSP
  mixes like any input. Every 500 ms it reads the device status and restores the whole mix if
  the driver has reset the mixer (replug, resume). USB never runs in the audio thread.
- A meter thread reads the Digiface's level endpoint every 10 ms and raises the shared-memory
  peaks from it (`hw_levels = 1`); the audio callback then stops measuring peaks itself.
- **Software mode**: `out[o] = out_gain[o] · Σ gain[o][k] · src[k]` as before.

## Shared memory (`engine/shm_layout.h`)

One `struct ofm_shm` per user at `/dev/shm/openface-mixer-<uid>`:

| Field | Writer | Meaning |
| --- | --- | --- |
| header (64 bytes) | engine | magic `OFMX`, version, link counts, rate, quantum, heartbeat, pid |
| `mixer_mode` | GUI | 0 = software, 1 = hardware |
| `hw_state`, `hw_nodes`, `hw_levels` | engine | hardware mixer status, routes in use |
| `gain[34][66]` | GUI | linear send gains, with pan and source mute folded in |
| `out_gain[34]` | GUI | output master per channel, 0 when muted |
| `peak_src[66]`, `peak_out[34]` | engine raises, GUI zeroes | max-hold peak meters |

No locks are used: 32-bit float stores are atomic on the supported platforms, and a torn meter
reset costs at most one missed peak. The engine never blocks on the GUI.

## GUI (`openface_mixer/`)

```
app.MainWindow
 ├─ model.py        state dict  ──compute_matrix()──▶ engine.write_matrix()
 ├─ presets.py      PresetBank (8 slots) + mix file import/export
 ├─ widgets.py      Strip = Knob + M/ST buttons + readouts + Fader + Meter + name tag
 ├─ matrix_view.py  grid editor for the same sends
 ├─ settings_panel  hardware.Hardware (amixer)  /  hardware.pw_digiface_card (pactl)
 └─ config.py       state.json / matrix.bin / presets.json
```

### The mixer model

- **Sources** are mono channels (`in` 0–31, `play` 0–33). Adjacent pairs can be stereo-linked.
- **Outputs** are stereo pairs (0–16; pair 16 = phones at single speed).
- `sends[kind][channel][pair] = [gain_dB | None, pan]` holds the TotalMix-style submixes. The
  faders show the sends into the selected pair (`state["selected"]`).
- Pan uses a balance law: at centre both sides get full level. A mono source is panned across the
  pair; in a linked stereo pair, left goes to left and right goes to right.
- `compute_matrix()` folds sends, pan and source mutes into the 34×66 gain matrix;
  `compute_out_gains()` gives the output masters, which map to the DSP's own output faders.
- **Fader groups** live in `state["groups"]` as `{strip_key: 1..4}`. Moving a grouped fader
  applies the same dB change to the other members (`model.group_follow`). −∞ counts as the
  fader floor (−80 dB), so a group can go all the way down and come back up together.
- **Presets** store `extract_mix(state)`: stereo links, mutes, sends, output levels and groups.
  UI state such as the selected output and active tab is not stored.

### Updating flow

1. A widget emits a signal (for example `Fader.changed`).
2. `MainWindow` updates `self.st` and applies group follow, then calls `push_matrix()`.
3. `push_matrix()` recomputes the matrix, writes it to shared memory, and schedules a debounced
   save of `state.json` and `matrix.bin`.
4. Timers poll the meters (30 Hz), engine status (2 Hz), ALSA controls (1 Hz) and the
   PipeWire profile (every 3 s).
