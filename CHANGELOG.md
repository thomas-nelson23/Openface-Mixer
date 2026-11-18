# Changelog

What changed in each version of Openface Mixer, for the people who use it. Releases are
installed from **Updates** in the settings (see the [README](README.md#updates)).

## Unreleased

## 0.4.0 (2026-11-18)

### New

- Type a level: double-click the dB readout under a fader, or press Enter on a focused fader, and type `-12`, `+3`, `-inf` or `off`.
- Matrix view: channel names stay in view while the grid scrolls, dragging a cell up or down sets its level, the arrow keys, Enter and Delete work on the selected cell, and a mono source shows where it is panned.
- *Restore previous mix* in the preset menus: every Recall, Import and Reset keeps the mix it replaces.
- Effects panels (802, UCX II): Esc or Ctrl+W closes a panel, and right-click or Enter on a knob types its value.
- HDSPe: with two cards of the same model you can choose which one to mix.
- The settings show the installed version, and the app looks for an update a few seconds after it starts. A dot on the gear button says one is waiting. Untick *Check when the app starts* to switch this off.
- Updates follow releases by default. Choose *Update to: Latest changes* to get every change as soon as it is made (for testers).
- Arch: a package recipe (`openface-mixer-git`) in `packaging/aur`.
- Click a row's title (Hardware inputs, Playback, Hardware outputs) to fold that row to names and meters, so the other rows get longer faders. Click again to unfold. A folded row still shows what is soloed, muted or clipped, and which rows are folded is remembered per device.
- A *Keyboard shortcuts* list at the bottom of the settings, and the same list in the README.
- Control room: set the Dim amount and the Talkback dim amount, send talkback to further outputs with *Talkback to*, and set Main Out to a stored listening level with *Recall*.
- Control room: up to four Phones outputs. With two or more, the ARC USB's two Phones keys select the first and the second.
- Meters show the RMS level as a bright bar inside a dimmer peak bar, as TotalMix does, on interfaces that report it (Digiface, 802 over USB, UCX II, and HDSPe inputs and playback). Untick *Show RMS level* in Display for peak only.
- Display: *Peak hold* sets how long the peak line stays (0.5 to 9.9 seconds), and on HDSPe cards *Clip after* sets how many full-scale samples in a row light the clip light. Both are kept for this computer.
- Display: eight *Layouts* store which channel groups and strips are hidden, which rows are folded and whether custom names are shown. Click *Store* and a slot to save the view, click a slot to bring it back. A layout never changes the mix, and a strip it hides has its solo switched off. Layouts are kept per interface.
- Mix: *Save workspace* puts everything for this interface in one file: mix, names, colours, icons, layouts, presets, control room, device and effect settings, and the meter and ARC key options. *Load workspace* brings it all back after checking the whole file, and the mix it replaces stays available as *Restore previous mix*. A workspace for another interface is refused.
- ARC USB: *Keys* beside the ARC status lists the 15 keys. Give any key any function (a preset, Dim, Mono, Talkback, Speaker B, External In, or the volume of Main Out or a Phones output), or *Reset to default*. The lights follow the keys, and the choice is kept for this computer.
- Right-click an output: *Copy mix*, *Paste mix* and *Mirror mix*, as in TotalMix. A mirrored output plays the same mix as the other and follows every change to it; *Remove mirror* gives it its own copy again.
- Right-click an input or playback channel: *Copy sends*, *Paste sends* and *Clear sends* (all of the channel's sends, to every output). A pair counts as one channel.
- Paste and clear keep the mix they replace for *Restore previous mix*.
- **Cue** on each output strip: hear that output's mix on Main Out, as in TotalMix. One at a time; press it again to end it. It is not saved, and it ends when the app quits.
- **Trim** at the end of the Hardware inputs title: a fader moves all of its channel's sends by the same amount, in every submix. Sends that are off stay off.
- Mute groups and solo groups, four of each, next to Trim. A mute group mutes its channels in the submix they were added in; a solo group solos its channels. Both are part of presets and mix files.
- A tray icon for a mixer started at login without a window: click it to show or hide the window, right-click for Dim, Mute main out, the presets and Quit. *Tray icon* below *Start at login* can show it always or never.
- The *Device* list marks the interfaces that are connected.
- Commands for desktop shortcuts: `openface-mixer --dim`, `--mute-main`, `--recall 3` and `--show` act on the running mixer, so the desktop's keyboard settings can give them hotkeys (see the README).
- Desktop outputs and inputs: right-click a playback strip → *Add desktop output…* for a sink on that pair ("Openface Music" on playback 3/4), or an input strip → *Add desktop input…* for a mono or stereo source. Rename or remove them from the same menu. The playback sink on playback 1/2 is there as before and can be renamed or removed too.
- Clock: *PipeWire rate* (Auto, 44.1 to 192 kHz) and *Buffer* (Auto, 32 to 2048 frames) set PipeWire's sample rate and buffer for every program. Auto writes nothing, and the app does not force a value again after PipeWire restarts.

### Changed

- Matrix view: a click on a cell that has a level selects it instead of clearing it. Double-click or Delete clears it. The wheel scrolls the grid, and Ctrl + wheel changes a level.
- Clearing a preset slot asks first.
- Status lights are a filled dot in one of three colours: cyan for on, red for a fault, grey for off or not known yet. The empty rings and amber lights are gone, and the top bar says "Engine stopped".
- 802, UCX II: EQ, Dynamics and Reverb / Echo open inside the mixer row, next to their strip, instead of in floating windows. A second click on EQ, D or FX closes the panel. The UCX II Room EQ is still a window of its own.
- Pan: a mono channel panned to the centre is 3 dB down on each side, as in TotalMix. Saved mixes, presets and mix files are converted once and sound the same; a centred mono fader reads about 3 dB higher than before. Going back to an older version afterwards needs the config backup the update made.
- Linking two channels to a stereo pair keeps what each had on its own. Unlinking brings back both channels' levels, pans and mutes, and the right channel's fader group.
- Hiding a channel or a channel group switches its solo off, so nothing out of sight can silence a mix.
- An interface's engine and playback sink are switched on the first time the app sees that interface, no longer for everyone at install.
- 802, UCX II: mute and solo also cut a channel's reverb and echo send.
- 802, UCX II: a stereo-linked pair uses the left channel's effects for both sides. The right channel's own settings come back on unlink.
- 802: ticking Inst unticks 48V and locks it while Inst is on.
- 802 over USB, UCX II: "Active" means the unit has answered. Until then the status says "Connected, no reply from the DSP".
- 802, 800, 400 over FireWire: connecting leaves the unit's clock source alone until you change a setting here.
- HDSPe on snd-hdspm: faders stop at +6 dB, which is what the card can do. Saved levels are not changed.
- HDSPe: the wrong-driver block is now a warning with a *Use anyway* choice.
- The engine starts with the default routing when its saved mix file is damaged or unknown. Files from the very first format are no longer read.
- Uninstall also removes the start-at-login entries.
- Updating keeps only the three newest config backups.
- With the window hidden or closed to the background, the app stops polling and the engine reads the interface's meters once a second instead of about a hundred times. Meters are live again the moment the window is shown, and peak readouts start fresh.
- The engine and the window share a new meter layout, so the app restarts an engine from before the update once, by itself.
- A mixer started at login without a window that switches to an interface whose window is already open shows that window and keeps running hidden, instead of quitting.
- The engine runs without PipeWire: the hardware mixer, meters and status work under JACK alone or before PipeWire starts. When PipeWire starts, crashes or is restarted, the engine connects again by itself without sending the mix again, and the sinks come back.
- The engines start at login whether PipeWire runs or not. Updating moves existing engines over.
- Status: "Playback sink" is now "Desktop audio". It says "PipeWire not running" when PipeWire is not there, and its tooltip lists the desktop outputs and inputs.

### Fixed

- Scrolling sideways over a fader or pan knob scrolls the row instead of turning the control down, and a touchpad moves a control one step per notch of movement.
- A damaged or half-written mix or preset file no longer stops the app from opening: the last good save is loaded, and saves are synced to disk.
- Importing a damaged or foreign mix file leaves the current mix as it was, and exporting to a place that can't be written says so.
- A mix made on a bigger interface no longer leaves hidden routing on channels this interface doesn't have.
- Talkback goes to the output chosen as Phones, no longer always to the interface's own phones output.
- 802, UCX II: effect settings are sent before an effect is switched on, so it never starts with settings left by another controller.
- UCX II: input 1 is unmuted on connect like the other inputs.
- 802, UCX II: Room EQ volume and the UCX II echo delay show the value that is sent, and typing a value on FX Send in the channel settings works.
- FireWire: a bus reset no longer drops the unit, the meters keep moving while the mix is sent after connect, and a slow answer is not taken for a lost device.
- FireWire: with two Firefaces the playback sink connects to the right one.
- 800, 400: TCO and LTC are shown as the clock source instead of being replaced by Internal.
- HDSPe on snd-hdspm: the sample rate shown follows the card's real clock, also at double and quad speed.
- HDSPe: a setting the driver refuses says why, and "Card not found" names a missing driver.
- A setting that could not be changed shows a short message, and the system tools the app calls have a time limit.
- Installing or updating stops an engine that was started by hand, so the new version takes over.
- The app menu entry, *Start at login* and the engine services work when `~/.local/bin` is not on the PATH, and with `PREFIX=` installs.
- The "Record all inputs" question no longer mentions a config only the author has.
- Screen readers get distinct names for the effect knobs and for each strip's buttons.
- README: correct package names for Debian 13 and for Ubuntu 25.04 and later.
- Opening the window and *Restart engine* no longer freeze the window for up to two seconds.
- A second engine for the same interface stops with one clear line instead of fighting the first, and an older engine started by hand is replaced after an update.
- The Digiface engine shuts down cleanly when it is stopped.
- ARC USB: the snapshot key goes out when its preset is cleared, and unplugging the ARC at the wrong moment no longer raises an error.
- Network remote: a *Send to* host given by name no longer freezes the window at startup when the name server is slow.
- A mixer started at login without a window stays in background mode after an update restart, a new UI scale or a device switch, so closing the window still only hides it.
- Two mixers started at login for two interfaces no longer stop each other from starting when they save at the same moment.
- Scrolling the settings with the mouse over *Device* no longer switches to another interface.

### Not yet tried on hardware

These changes were made and tested without the interface attached, and they ship so that
people who have one can try them. Please try the points for your interface and
[open an issue](https://github.com/thomas-nelson23/Openface-Mixer/issues) with what you find.

**Fireface 802**

- EQ and Dynamics open inside the row: open them on an input and on the last output, open two strips at once, press FX, rename a channel with a panel open, and try a small window.
- Talkback with Phones set to PH 11/12 lands there only, and on both PH 9/10 and PH 11/12 once both are ticked as Phones.
- Mute or solo a channel that has an FX send: it leaves the reverb and echo and returns at its old level.
- Link and unlink a pair, and recall a preset that links one: both sides take the left channel's effects, and the right channel's own return on unlink.
- AN 9 to 12 with a condenser mic: 48V stays off after Inst was ticked and unticked, also on a linked pair.
- FireWire: connecting leaves the clock source alone (try ADAT A and ADAT B), and another FireWire device plugged in does not drop the unit.
- FireWire: the meters keep moving right after connect, and the effects start with your settings.
- USB: the status reaches "Active". Please report it if it stays at "Connected, no reply from the DSP" while the mix works, or if the unit reconnects by itself.
- Effect knobs with a mouse wheel and a touchpad, and typing a value on FX Send in the channel settings (X11 and Wayland).
- After an engine restart the routing, clock, levels, gains, 48V, Inst and all effect settings are as saved.
- USB: the RMS bar on a sine at -20 dBFS sits about 3 dB below the peak, as in TotalMix. FireWire shows no RMS.

**Fireface UCX II**

- The effects panels inside the row, as for the 802. Room EQ is still its own window: on Wayland, check that it opens somewhere usable.
- Mute, solo, stereo link and the effect knobs, as for the 802.
- AN 1 passes audio after another controller muted it.
- The status reaches "Active", the unit never reconnects by itself, and unplugging and replugging reconnects.
- After an engine restart the routing, clock, levels and all effect settings are as saved.
- The RMS bar on a sine at -20 dBFS sits about 3 dB below the peak, as in TotalMix.

**Fireface 800 and 400**

- Connecting leaves the clock source alone, and the clock box shows the real source (also TCO on the 800 and LTC on the 400).
- Another FireWire device plugged in while the mixer runs does not drop the unit.
- After an engine restart the routing, clock and levels are as saved.

**HDSPe RayDAT, AIO, AIO Pro and AES**

- On snd-hdspm: the +6 dB limit mark on the faders, *Check driver* with *Use anyway*, and the sample rate shown at 96 and 192 kHz, internal and slaved.
- The "busy" explanation when playback and recording are open in two programs.
- AIO and AIO Pro with AI4S or AO4S boards: analog 3 to 6 keep their routing and still play.
- An existing mix opened at 48 kHz and at 96 or 192 kHz has every send you had set.
- The RMS bar on inputs and playback sits about 3 dB below the peak on a sine (outputs show peak only), and *Clip after* set to 3 lights the clip light only for three or more full-scale samples in a row.

**Digiface USB**

- After an engine restart the saved routing is back.
- Update with the Digiface unplugged: `systemctl --user is-enabled openface-mixer-engine` still says `enabled`.
- An old mix sounds the same after the update, and a mono channel panned to the centre is 3 dB down on each side.
- Hide the window to the background for a minute and show it again: the meters move at once.
- Restart PipeWire (`systemctl --user restart pipewire`) while monitoring an input: the mix keeps playing, the engine is not restarted (`systemctl --user status openface-mixer-engine` shows the same start time) and Openface Mixer Playback comes back. Please report a click or dropout.
- Switch PipeWire off (`systemctl --user mask --now pipewire.socket pipewire`), log out and in: the engine runs, the mix is restored and Status says "PipeWire not running". Unmask both and start them again: the sink appears.
- Add a desktop output on AD1 3/4 and a desktop input on AD1 1: they show in the sound settings as "Openface …" and play and record on those channels. At 96 kHz one on a channel the Digiface lacks there goes away and comes back at 48 kHz.
- *PipeWire rate* 96 kHz: the Digiface switches to double speed and the mixer shows its 2x channels. Back to Auto: PipeWire chooses again.
- After the update the engine restarts once by itself and the meters move. The RMS bar sits inside the peak bar and falls with it.
- Cue the Phones output while listening on Main Out: you hear the Phones mix at the Main Out level, Dim and Talkback still work, and a second press brings Main Out's own mix back.
- Mirror one output's mix onto another, switch a mute group on, and move a fader with Trim on: what you hear follows what the faders show.
- With *Start at login* ticked, log in again: the tray icon is there, its Dim, Mute main out and presets act as the buttons do, and the window it opens only hides when closed (KDE Plasma, GNOME with a tray extension, X11 and Wayland).
- A keyboard shortcut set to `openface-mixer --dim` dims the running mixer, also with its window hidden.

**Every Fireface and HDSPe card**

- An old mix sounds the same after the update, and a mono channel panned to the centre is 3 dB down on each side.
- Talkback to further outputs, the Talkback dim amount and *Recall*.
- ARC USB: with one Phones output both Phones keys light together as before; with two, each key lights alone and the encoder turns its own output.
- With the window closed to the background for several minutes the unit stays connected (802 over USB and UCX II: the status must not fall to "no reply"), and the meters move at once when the window is shown.
- Open the app with the interface connected, then `systemctl --user is-enabled openface-mixer-engine@<device>` says `enabled`.
- ARC USB: give a key another function under *Keys*: it does that function, its light follows it, and *Reset to default* brings back the printed layout. The Phones keys still behave as above.
- Cue, Mirror, mute groups and Trim, as for the Digiface. HDSPe: a cued mix plays at the Main Out level, which the card adds into every send.
- The *Device* list says “(connected)” next to the interface while it is connected, and no longer once it is unplugged or switched off.
- A desktop output on a pair other than 1/2 and a desktop input play and record on the channels their strips show. On the RayDAT and the AIO the driver orders its channels differently from the mixer, so please try AES and S/PDIF as well as ADAT, and the AIO phones.
- *PipeWire rate* and *Buffer* change the rate the interface runs at; on an HDSPe card with Clock mode Master the card follows the PipeWire rate while PipeWire plays.
- After an update, `systemctl --user list-dependencies default.target` lists the engine and `pipewire.service.wants` no longer does.
