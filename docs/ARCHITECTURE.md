# Architecture

Openface Mixer is two programs that share one block of memory. All mixing happens in the
Digiface's own DSP; the computer only tells it what to do.

```
 Python GUI (openface_mixer/) ──amixer──▶ ALSA controls (clock, formats)
        │ gains, output faders      ▲ peak meters, status
        ▼                           │
 /dev/shm/openface-mixer-<uid>   (struct ofm_shm, engine/shm_layout.h)
        │                           ▲
        ▼                           │
 openface-mixer-engine (C) ──libusb, interface 1──▶ Digiface DSP mixer (2048 nodes, output faders)
        │                  ◀──level meters (EP 0x84)──
        └─ "Openface Mixer Playback" sink ──PipeWire link──▶ Digiface playback 1/2
```

## Engine (`engine/openface-mixer-engine.c`)

- A 20 ms main-loop timer (`hw_tick`) opens the Digiface's mixer interface with libusb and sends
  only the changed crosspoints and output faders (`engine/digiface_usb.c`, protocol in
  [HARDWARE.md](HARDWARE.md)). It retries every second while the device is missing or not
  accessible, and reports why in `hw_state`.
- Every 500 ms it reads the device status: the sample rate goes to the GUI (which adapts the
  channel count to 1x/2x/4x speed), and if the driver has reset the mixer (replug, resume) the
  whole mix is restored.
- A meter thread reads the level endpoint every 10 ms and raises the shared-memory peaks.
- It loads `libpipewire-module-loopback` to create the **Openface Mixer Playback** sink and links
  it to the Digiface's `playback_AUX0/1` (matched by node name prefix
  `alsa_output.usb-RME_Digiface_USB`), re-linking on hotplug or profile changes. No audio passes
  through the engine itself.
- It runs as a systemd **user service**. At startup it loads `~/.config/openface-mixer/matrix.bin`
  (gain matrix and output gains), which the GUI keeps up to date. When it exits, the interface
  keeps mixing with the last mix.

## Shared memory (`engine/shm_layout.h`)

One `struct ofm_shm` per user at `/dev/shm/openface-mixer-<uid>`:

| Field | Writer | Meaning |
| --- | --- | --- |
| header (64 bytes) | engine | magic `OFMX`, version, heartbeat, sample rate, pid, playback sink linked |
| `hw_state`, `hw_nodes`, `hw_levels` | engine | hardware mixer status, routes in use, meters live |
| `gain[34][66]` | GUI | linear send gains, with pan, source mute and solo folded in |
| `out_gain[34]` | GUI | output master per channel, 0 when muted |
| `peak_src[66]`, `peak_out[34]` | engine raises, GUI zeroes | max-hold peak meters |

No locks are used: 32-bit float stores are atomic on the supported platforms, and a torn meter
reset costs at most one missed peak. The engine never blocks on the GUI.

## GUI (`openface_mixer/`)

```
app.MainWindow
 ├─ model.py        state dict  ──compute_matrix()──▶ engine.write_matrix()
 ├─ presets.py      PresetBank (8 slots) + mix file import/export
 ├─ widgets.py      Strip = name tag + Knob + M/S/ST buttons + readouts + Fader + Meter
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
- **Solo** (`state["solo"]`) is TotalMix's solo-in-place: while any source is soloed, the
  unsoloed ones are left out of the *selected* output pair only, so it follows the submix you
  are editing. Solo is never saved (`save_state` drops it, `matrix.bin` is written without it,
  `upgrade_state` clears it) and is not part of a mix. The top bar's SOLO button clears all
  solos and brings the same set back on the next click.
- `compute_matrix()` folds sends, pan, source mutes and solo into the 34×66 gain matrix;
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
5. Selecting another output pair re-pushes the matrix while anything is soloed.
