# Architecture

Openface Mixer is two programs that share one block of memory. All mixing happens in the
interface's own DSP; the computer only tells it what to do. The Digiface USB, the Fireface 802, the
Fireface 800 and the HDSPe RayDAT use the same mix model and GUI; what differs is described by a device (`openface_mixer/devices.py`)
in the GUI and a backend (`engine/backend.h`) in the engine. Each device has its own engine
process, shared memory and config files, so several can run at once.

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

`--device digiface` (default), `--device ff802`, `--device ff800` or `--device raydat` picks the
backend: `backend_digiface.c` on top of `digiface_usb.c` (libusb), the 802 or 800 backend in
`fireface_fw.c` (FireWire transactions through `/dev/fw*`), or `raydat.c` (snd-hdspm's or snd-hdspe's `Mixer`
control and hwdep meters in `/dev/snd`). The rest of the engine is the same
for all of them.

- A 20 ms main-loop timer (`hw_tick`) opens the device and sends only the changed crosspoints,
  output faders and (Firefaces) device settings (protocols in [HARDWARE.md](HARDWARE.md)). It retries every second while the device is missing or not
  accessible, and reports why in `hw_state`.
- Every 500 ms it reads the device status: the sample rate goes to the GUI (which adapts the
  channel count to 1x/2x/4x speed), and if the driver has reset the mixer (replug, resume) the
  whole mix is restored.
- A meter thread reads the level endpoint every 10 ms and raises the shared-memory peaks.
- It loads `libpipewire-module-loopback` to create the **Openface Mixer Playback** sink and links
  it to the Digiface's `playback_AUX0/1` (matched by node name prefix
  `alsa_output.usb-RME_Digiface_USB`; the RayDAT's by its ALSA card name), re-linking on
  hotplug or profile changes. No audio passes through the engine itself.
- It runs as a systemd **user service**: `openface-mixer-engine` for the Digiface,
  `openface-mixer-engine@ff802`, `@ff800` and `@raydat` for the others. At startup it loads the
  device's `matrix.bin` (gain matrix, output gains and device settings), which the GUI keeps up
  to date; files from before the 36-channel layout are moved into place. When it exits, the
  interface keeps mixing with the last mix.
- The Fireface 802 can't report its DSP state, so on every (re)connect the engine sends the whole
  mix and all settings, about 2,300 transactions. The 800 takes its mixer as one block write
  per output, so a full send is about 60 transactions.

## Shared memory (`engine/shm_layout.h`)

One `struct ofm_shm` per user and device at `/dev/shm/openface-mixer-<uid>` (Digiface) or
`/dev/shm/openface-mixer-<device>-<uid>`. It has room for 36 inputs, 36 playback channels and 36
outputs, the RayDAT's counts; source k is input k below 36 and playback k − 36 above. The
Digiface uses inputs 0–31, playback 0–33 and outputs 0–33; the 802 inputs 0–31 (30/31 its FX
returns), playback 0–29 and outputs 0–29; the 800 inputs, playback and outputs 0–27.

| Field | Writer | Meaning |
| --- | --- | --- |
| header (64 bytes) | engine | magic `OFMX`, version, heartbeat, sample rate, pid, playback sink linked |
| `hw_state`, `hw_nodes`, `hw_levels` | engine | hardware mixer status, routes in use, meters live |
| `gain[36][72]` | GUI | linear send gains, with pan, source mute and solo folded in |
| `out_gain[36]` | GUI | output master per channel, 0 when muted |
| `peak_src[72]`, `peak_out[36]` | engine raises, GUI zeroes | max-hold peak meters |
| `dev_status`, `dev_status2` (header) | engine | device status (802: sync status register; 800: its two status quadlets) |
| `dev_config`, `dev_cmd[512]` | GUI | 802 configuration register and setting commands (0 = unused); 800: `dev_cmd[0..2]` are the configuration quadlets, sent while `dev_config` is 1 |

No locks are used: 32-bit float stores are atomic on the supported platforms, and a torn meter
reset costs at most one missed peak. The engine never blocks on the GUI.

## GUI (`openface_mixer/`)

```
app.MainWindow
 ├─ model.py        state dict  ──compute_matrix()──▶ engine.write_matrix()
 ├─ presets.py      PresetBank (8 slots) + mix file import/export
 ├─ widgets.py      Strip = name tag + Knob + S/M/ST buttons + Fader + Meter + readouts
 ├─ matrix_view.py  grid editor for the same sends
 ├─ settings_panel  hardware.Hardware (amixer)  /  hardware.pw_digiface_card (pactl)
 └─ config.py       state.json / matrix.bin / presets.json
```

### The mixer model

- **Sources** are mono channels (`in` 0–35, `play` 0–35). Adjacent pairs can be stereo-linked.
- **Outputs** are stereo pairs (0–17; on the Digiface pair 16 = phones at single speed).
- The device decides which channels exist at the current rate and their names
  (`Device.channels()`, `chan_label()`, `pair_label()`); the window only builds strips for those.
- **Device settings** (802, 800) live in `state["hw"]`. `fireface802.commands()` turns them into
  the DSP command list the engine sends, in a fixed slot order so only changed slots go out;
  `fireface800.config_words()` into the 800's three configuration quadlets, which are written
  only after the first status read has filled in the options the 800 reports.
- `sends[kind][channel][pair] = [gain_dB | None, pan]` holds the TotalMix-style submixes. The
  faders show the sends into the selected pair (`state["selected"]`).
- Pan uses a balance law: at centre both sides get full level. A mono source is panned across the
  pair; in a linked stereo pair, left goes to left and right goes to right.
- **Solo** (`state["solo"]`) is TotalMix's solo-in-place: while any source is soloed, the
  unsoloed ones are left out of the *selected* output pair only, so it follows the submix you
  are editing. Solo is never saved (`save_state` drops it, `matrix.bin` is written without it,
  `upgrade_state` clears it) and is not part of a mix. The top bar's SOLO button clears all
  solos and brings the same set back on the next click.
- `compute_matrix()` folds sends, pan, source mutes and solo into the 36×72 gain matrix;
  `compute_out_gains()` gives the output masters, which map to the DSP's own output faders.
- **Fader groups** live in `state["groups"]` as `{strip_key: 1..4}`. Moving a grouped fader
  applies the same dB change to the other members (`model.group_follow`). −∞ counts as the
  fader floor (−80 dB), so a group can go all the way down and come back up together.
- **Presets** store `extract_mix(state)`: stereo links, mutes, sends, output levels and groups.
  UI state such as the selected output and active tab is not stored, and neither are device
  settings, as in TotalMix's snapshots.

### Updating flow

1. A widget emits a signal (for example `Fader.changed`).
2. `MainWindow` updates `self.st` and applies group follow, then calls `push_matrix()`.
3. `push_matrix()` recomputes the matrix, writes it to shared memory, and schedules a debounced
   save of `state.json` and `matrix.bin`.
4. Timers poll the meters (30 Hz), engine status (2 Hz), ALSA controls (1 Hz) and the
   PipeWire profile (every 3 s).
5. Selecting another output pair re-pushes the matrix while anything is soloed.
