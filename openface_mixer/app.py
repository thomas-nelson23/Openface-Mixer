"""Main window: wires the model, the engine link, the hardware panel and the widgets together."""
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
    QMenu, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from . import APP_ID, APP_NAME
from . import arc, control_room, fireface800
from .channel_settings import ChannelSettings
from .config import (
    device_block, load_device_choice, load_state, presets_file, save_device_choice, save_state,
)
from .control_panel import ArcLink, ControlRoomPanel
from .devices import DEVICES, detect
from .engine import Engine
from .hardware import Hardware, pw_digiface_card
from .matrix_view import MatrixView
from .model import (
    N_GROUPS, N_IN, N_PAIRS, N_SLOTS, NEG_INF, any_solo, apply_mix, compute_matrix,
    compute_out_gains, default_state, extract_mix, get_strip_db, group_follow, set_strip_db,
    speed_mode, strip_channels, strip_key,
)
from .presets import PresetBank, export_mix, import_mix
from .settings_panel import SettingsPanel
from .theme import ACCENT, apply_theme
from .widgets import Row, Strip


class MainWindow(QMainWindow):
    def __init__(self, dev):
        super().__init__()
        self.dev = dev
        self.setWindowTitle(f"{APP_NAME} — {dev.name}")
        self.engine = Engine(dev)
        self.hw = Hardware(dev.alsa_card)
        self.st = load_state(dev)
        self.presets = PresetBank(presets_file(dev))
        self.switch_to = None              # device key to reopen the window for, see main()
        self.rate = 48000
        self.mode = 1                      # speed mode: 1x / 2x / 4x
        self.strips = {}                   # strip key -> Strip (currently built strips)
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self.save)
        self._last_meter = time.monotonic()

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)
        root.addLayout(self._build_top_bar())

        body = QHBoxLayout()
        mixer_page = QWidget()
        rows = QVBoxLayout(mixer_page)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(10)
        self.row_in = Row("Hardware Inputs", "")
        self.row_play = Row("Playback", "")
        self.row_out = Row("Hardware Outputs", "master level · click a name tag to edit its submix")
        for r in (self.row_in, self.row_play, self.row_out):
            rows.addWidget(r, 1)
        self.matrix = MatrixView(self)
        mscroll = QScrollArea()
        mscroll.setWidget(self.matrix)
        mscroll.setFrameShape(QFrame.NoFrame)
        self.tabs = QStackedWidget()
        self.tabs.addWidget(mixer_page)
        self.tabs.addWidget(mscroll)
        self.tabs.currentChanged.connect(self._tab_changed)
        body.addWidget(self.tabs, 1)
        self.settings = SettingsPanel(self.hw, dev, self.st)
        self.settings.eng_btn.clicked.connect(self.restart_engine)
        self.settings.device_selected.connect(self.switch_device)
        self.settings.hw_changed.connect(self.push_matrix)
        self.control = ControlRoomPanel(self.st)
        self.control.changed.connect(self.push_matrix)
        self.settings.layout().insertWidget(self.settings.layout().count() - 2, self.control)
        self.arc = ArcLink(self)
        self.arc.key.connect(self.arc_key)
        self.arc.encoder.connect(self.arc_encoder)
        self.arc.status_changed.connect(self.arc_status)
        self._talkback_pressed = None     # (time, was it switched on by this press)
        # the panel scrolls instead of squashing its boxes when the window is short
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidget(self.settings)
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.NoFrame)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.settings_scroll.setFixedWidth(self.settings.width() + 12)
        self.settings_btn.toggled.connect(self.settings_scroll.setVisible)
        body.addWidget(self.settings_scroll)
        root.addLayout(body, 1)
        self.setCentralWidget(central)

        self.tabs.setCurrentIndex(int(self.st.get("tab", 0)))
        self._tab_changed(self.tabs.currentIndex())
        self.poll_hw()
        self.rebuild_all()
        self.refresh_slots()
        self.ensure_engine()
        self.push_matrix()

        QTimer(self, interval=33, timeout=self.update_meters).start()
        QTimer(self, interval=1000, timeout=self.poll_hw).start()
        QTimer(self, interval=500, timeout=self.poll_engine).start()
        QTimer(self, interval=3000, timeout=self.poll_profile).start()
        self.poll_profile()
        self.resize(1600, 960)

    # ------------------------------------------------------------------ top bar
    def _build_top_bar(self):
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(QLabel("<span style='font-size:14px;font-weight:700'>Openface</span>"
                             "<span style='font-size:14px;font-weight:300;color:#8e8e93'> Mixer</span>"))
        top.addSpacing(10)

        seg = QWidget()
        seg.setObjectName("segment")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(2, 2, 2, 2)
        sl.setSpacing(0)
        self.view_btns = []
        for i, name in enumerate(("Mixer", "Matrix")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setObjectName("segbtn")
            b.clicked.connect(lambda _=False, n=i: self.tabs.setCurrentIndex(n))
            sl.addWidget(b)
            self.view_btns.append(b)
        top.addWidget(seg)
        top.addSpacing(10)

        top.addWidget(self._caption("SUBMIX"))
        self.submix = QComboBox()
        self.submix.setMinimumWidth(110)
        self.submix.activated.connect(self.select_out)
        top.addWidget(self.submix)
        top.addSpacing(6)
        self.solo_master = QPushButton("SOLO")
        self.solo_master.setObjectName("solomaster")
        self.solo_master.setCursor(Qt.PointingHandCursor)
        self.solo_master.setToolTip("Lit while any channel is soloed. Click to switch all solos "
                                    "off; click again to bring them back.")
        self.solo_master.clicked.connect(self.toggle_solo_master)
        self._solo_memory = None          # solos switched off by the master, for recall
        top.addWidget(self.solo_master)
        top.addStretch(1)

        self.status = QLabel("")
        self.status.setObjectName("lcd")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setMinimumWidth(160)
        top.addWidget(self.status)
        top.addStretch(1)

        top.addWidget(self._caption("PRESETS"))
        slots = QHBoxLayout()
        slots.setSpacing(3)
        self.slot_btns = []
        for i in range(N_SLOTS):
            b = QPushButton(str(i + 1))
            b.setObjectName("slot")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=i: self.slot_clicked(n))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, n=i: self.slot_menu(n))
            slots.addWidget(b)
            self.slot_btns.append(b)
        top.addLayout(slots)
        self.store_btn = QPushButton("Store")
        self.store_btn.setObjectName("store")
        self.store_btn.setCheckable(True)
        self.store_btn.setToolTip("Arm, then click a slot to save the current mix into it")
        top.addWidget(self.store_btn)

        mix_menu = QMenu(self)
        mix_menu.addAction("Export current mix…", self.export_mix)
        mix_menu.addAction("Import mix…", self.import_mix)
        mix_menu.addSeparator()
        mix_menu.addAction("Reset mix to default…", self.reset_mix)
        mix_btn = QPushButton("Mix ▾")
        mix_btn.setMenu(mix_menu)
        top.addWidget(mix_btn)
        top.addSpacing(6)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setCheckable(True)
        self.settings_btn.setChecked(True)
        top.addWidget(self.settings_btn)
        return top

    @staticmethod
    def _caption(text):
        lbl = QLabel(text)
        lbl.setObjectName("caption")
        return lbl

    # ------------------------------------------------------------------ building strips
    def channels(self, kind):
        """The device's channels of this kind ("in", "play", "out") at the current speed."""
        return self.dev.channels(kind, self.mode)

    def label(self, kind, c, pair=False):
        # playback channel n is meant for output n, so it is named like the output
        k = "out" if kind == "play" else kind
        return self.dev.pair_label(k, c, self.mode) if pair else self.dev.chan_label(k, c, self.mode)

    def out_pairs(self):
        return [c // 2 for c in self.channels("out") if c % 2 == 0]

    def rebuild_all(self):
        self.rebuild_sources("in")
        self.rebuild_sources("play")
        self.rebuild_outputs()
        self.refresh_control_channels()

    def refresh_control_channels(self):
        ins = self.channels("in")
        pairs = [(self.label("out", 2 * p, True), p) for p in self.out_pairs()]
        inputs = [(self.label("in", c), c) for c in ins]
        input_pairs = [(self.label("in", c, True), c) for c in ins if c % 2 == 0 and c + 1 in ins]
        self.control.set_channels(pairs, inputs, input_pairs)

    def rebuild_sources(self, kind):
        chans_all = self.channels(kind)
        present = set(chans_all)
        self.strips = {k: s for k, s in self.strips.items() if not k.startswith(kind + ":")}
        strips = []
        for c in chans_all:
            if c % 2 == 1 and c - 1 in present and self.st["stereo"][kind][c // 2]:
                continue                     # right channel of a linked pair: shares its strip
            has_pair = c % 2 == 0 and c + 1 in present
            chans = strip_channels(self.st, kind, c) if has_pair else [c]
            stereo = len(chans) == 2
            title = self.label(kind, c, pair=stereo)
            key = strip_key(kind, c)
            s = Strip(key, title, chans, kind, show_pan=True, show_stereo=has_pair,
                      show_solo=True, show_settings=kind == "in" and any(
                          self.dev.channel_has_settings(kind, ch) for ch in chans))
            s.stereo.setChecked(stereo)
            s.solo.setChecked(self.st["solo"][kind][c])
            s.solo.toggled.connect(lambda on, k=kind, ch=chans: self.set_solo(k, ch, on))
            s.stereo.toggled.connect(lambda on, k=kind, ch=c: self.toggle_stereo(k, ch, on))
            s.mute.setChecked(self.st["mute"][kind][c])
            s.mute.toggled.connect(lambda on, k=kind, ch=chans: self.set_mute(k, ch, on))
            s.fader.changed.connect(lambda db, st=s: self.on_fader(st, db))
            s.pan.changed.connect(lambda v, k=kind, ch=chans: self.set_pan(k, ch, v))
            s.context_requested.connect(lambda pos, st=s: self.strip_menu(st, pos))
            s.settings_requested.connect(lambda pos, st=s: self.channel_settings(st, pos))
            s.set_group(self.st["groups"].get(key))
            strips.append(s)
            self.strips[key] = s
        (self.row_in if kind == "in" else self.row_play).set_strips(strips)
        self.refresh_settings_buttons()
        self.refresh_sends(kind)
        self.refresh_solo()
        self.matrix.rebuild()

    def rebuild_outputs(self):
        self.strips = {k: s for k, s in self.strips.items() if not k.startswith("out:")}
        strips = []
        self.submix.clear()
        for pair in self.out_pairs():
            title = self.label("out", 2 * pair, True)
            key = strip_key("out", pair)
            s = Strip(key, title, [2 * pair, 2 * pair + 1], "out", show_pan=False,
                      show_settings=any(self.dev.channel_has_settings("out", ch)
                                        for ch in (2 * pair, 2 * pair + 1)))
            s.pair = pair
            o = self.st["out"][pair]
            s.set_fader(NEG_INF if o["gain"] is None else o["gain"])
            s.mute.setChecked(o["mute"])
            s.clicked.connect(lambda pr=pair: self.select_out(pr, by_pair=True))
            s.fader.changed.connect(lambda db, st=s: self.on_fader(st, db))
            s.mute.toggled.connect(lambda on, pr=pair: self.set_out_mute(pr, on))
            s.context_requested.connect(lambda pos, st=s: self.strip_menu(st, pos))
            s.settings_requested.connect(lambda pos, st=s: self.channel_settings(st, pos))
            s.set_group(self.st["groups"].get(key))
            strips.append(s)
            self.strips[key] = s
            self.submix.addItem(title, pair)
        self.row_out.set_strips(strips)
        self.refresh_settings_buttons()
        self.matrix.rebuild()
        sel = self.st["selected"]
        if sel not in [s.pair for s in strips]:
            sel = strips[-1].pair
        self.select_out(sel, by_pair=True)

    def refresh_sends(self, kind):
        """Show the sends into the selected output pair on the source faders."""
        row = self.row_in if kind == "in" else self.row_play
        p = self.st["selected"]
        for s in row.strips:
            gdb, pan = self.st["sends"][kind][s.channels[0]][p]
            s.set_fader(NEG_INF if gdb is None else gdb)
            s.pan.set_value(pan)

    # ------------------------------------------------------------------ model updates
    def select_out(self, idx, by_pair=False):
        pair = idx if by_pair else self.submix.itemData(idx)
        self.st["selected"] = pair
        for s in self.row_out.strips:
            s.set_selected(s.pair == pair)
        i = self.submix.findData(pair)
        if i >= 0:
            self.submix.setCurrentIndex(i)
            hint = f"faders send to&nbsp; <b style='color:{ACCENT.name()}'>{self.submix.itemText(i)}</b>"
            self.row_in.set_subtitle(hint)
            self.row_play.set_subtitle(hint)
        self.refresh_sends("in")
        self.refresh_sends("play")
        if any_solo(self.st):
            self.push_matrix()   # solo follows the current submix
        else:
            self.matrix.update()
            self.schedule_save()

    def on_fader(self, strip, db):
        """A fader moved: update the model, then let its fader group follow (Shift = solo move)."""
        old = get_strip_db(self.st, strip.key)
        set_strip_db(self.st, strip.key, db)
        if strip.group and not (QApplication.keyboardModifiers() & Qt.ShiftModifier):
            for key, val in group_follow(self.st, strip.key, old, db).items():
                if key in self.strips:
                    self.strips[key].set_fader(val)
        self.push_matrix()

    def set_send_at(self, kind, chans, pair, db):
        """Used by the matrix view: set the send level of chans into a given output pair."""
        for c in chans:
            self.st["sends"][kind][c][pair][0] = None if db is None else round(db, 2)
        if pair == self.st["selected"]:
            self.refresh_sends(kind)
        self.push_matrix()

    def set_pan(self, kind, chans, pan):
        p = self.st["selected"]
        for c in chans:
            self.st["sends"][kind][c][p][1] = round(pan, 3)
        self.push_matrix()

    def set_mute(self, kind, chans, on):
        for c in chans:
            self.st["mute"][kind][c] = on
        self.push_matrix()

    def set_solo(self, kind, chans, on):
        for c in chans:
            self.st["solo"][kind][c] = on
        if on:
            self._solo_memory = None
        self.refresh_solo()
        self.push_matrix()

    def toggle_solo_master(self):
        """TotalMix's Solo master: switch every solo off, and the same set back on next time."""
        if any_solo(self.st):
            self._solo_memory = {k: list(v) for k, v in self.st["solo"].items()}
            for k in self.st["solo"]:
                self.st["solo"][k] = [False] * len(self.st["solo"][k])
        elif self._solo_memory:
            self.st["solo"] = self._solo_memory
            self._solo_memory = None
        else:
            self.refresh_solo()
            return
        for s in self.row_in.strips + self.row_play.strips:
            s.solo.blockSignals(True)
            s.solo.setChecked(self.st["solo"][s.kind][s.channels[0]])
            s.solo.blockSignals(False)
        self.refresh_solo()
        self.push_matrix()

    def refresh_solo(self):
        on = any_solo(self.st)
        self.solo_master.setProperty("active", on)
        self.solo_master.setProperty("armed", not on and bool(self._solo_memory))
        self.solo_master.style().unpolish(self.solo_master)
        self.solo_master.style().polish(self.solo_master)

    def set_out_mute(self, pair, on):
        self.st["out"][pair]["mute"] = on
        self.push_matrix()

    def toggle_stereo(self, kind, c, on):
        st = self.st
        st["stereo"][kind][c // 2] = on
        # keep the audible result similar: stereo uses balance, mono L/R get hard-panned
        for p in range(N_PAIRS):
            left, right = st["sends"][kind][c][p], st["sends"][kind][c + 1][p]
            right[0] = left[0]
            if on:
                left[1] = right[1] = 0.0
            else:
                left[1], right[1] = -1.0, 1.0
        st["mute"][kind][c + 1] = st["mute"][kind][c]
        st["solo"][kind][c + 1] = st["solo"][kind][c]
        st["groups"].pop(strip_key(kind, c + 1), None)   # the right channel no longer has a strip
        QTimer.singleShot(0, lambda: self.rebuild_sources(kind))
        self.push_matrix()

    # ------------------------------------------------------------------ device settings
    def channel_settings(self, strip, pos):
        """TotalMix's channel settings for a hardware input or output strip (Fireface 802)."""
        popup = ChannelSettings(self.dev, self.st["hw"], strip.kind, strip.channels,
                                strip.title.text(), self)
        popup.setAttribute(Qt.WA_DeleteOnClose)
        popup.changed.connect(self.push_matrix)
        popup.changed.connect(self.refresh_settings_buttons)
        popup.move(pos)
        popup.show()

    def refresh_settings_buttons(self):
        for s in self.strips.values():
            if s.kind != "play":
                s.set_settings_active(any(self.dev.channel_settings_active(self.st, s.kind, c)
                                          for c in s.channels))

    def switch_device(self, key):
        if key == self.dev.key:
            return
        self.switch_to = key
        self.close()

    def _tab_changed(self, i):
        for n, b in enumerate(self.view_btns):
            b.setChecked(n == i)
        self.st["tab"] = i
        self.schedule_save()

    # ------------------------------------------------------------------ fader groups
    def strip_menu(self, strip, pos):
        menu = QMenu(self)
        grp = menu.addMenu("Fader group")
        for g in range(N_GROUPS + 1):
            a = grp.addAction("None" if g == 0 else f"● Group {g}")
            a.setCheckable(True)
            a.setChecked(strip.group == g)
            a.triggered.connect(lambda _=False, n=g: self.set_group(strip, n))
        menu.addAction("Fader to 0 dB", lambda: self.move_fader(strip, 0.0))
        menu.addAction("Fader to -∞", lambda: self.move_fader(strip, NEG_INF))
        menu.exec(pos)

    def move_fader(self, strip, db):
        strip.set_fader(db)
        self.on_fader(strip, db)

    def set_group(self, strip, group):
        if group:
            self.st["groups"][strip.key] = group
        else:
            self.st["groups"].pop(strip.key, None)
        strip.set_group(group)
        self.schedule_save()

    # ------------------------------------------------------------------ presets
    def refresh_slots(self):
        self.update_arc_leds()
        active = self.st.get("active_slot")
        for i, b in enumerate(self.slot_btns):
            name = self.presets.name(i)
            b.setProperty("filled", name is not None)
            b.setProperty("active", name is not None and i == active)
            b.setToolTip(f"{i + 1}: {name}\nClick to recall · right-click for options" if name
                         else f"{i + 1}: empty — arm Store, then click to save")
            b.style().unpolish(b)
            b.style().polish(b)

    def slot_clicked(self, i):
        if self.store_btn.isChecked():
            self.store_btn.setChecked(False)
            self.store_slot(i)
        elif self.presets.slots[i]:
            self.recall_slot(i)
        else:
            self.statusBar().showMessage(f"Slot {i + 1} is empty — click Store, then the slot.", 4000)

    def store_slot(self, i):
        name, ok = QInputDialog.getText(self, "Store preset", f"Name for slot {i + 1}:",
                                        text=self.presets.name(i) or f"Mix {i + 1}")
        if not ok:
            return
        self.presets.store(i, name.strip() or f"Mix {i + 1}", extract_mix(self.st))
        self.st["active_slot"] = i
        self.refresh_slots()
        self.schedule_save()

    def recall_slot(self, i):
        apply_mix(self.st, self.presets.slots[i]["mix"])
        self.st["active_slot"] = i
        self.rebuild_all()
        self.refresh_slots()
        self.push_matrix()

    def slot_menu(self, i):
        menu = QMenu(self)
        menu.addAction("Store current mix here…", lambda: self.store_slot(i))
        if self.presets.slots[i]:
            menu.addAction("Recall", lambda: self.recall_slot(i))
            menu.addAction("Rename…", lambda: self._rename_slot(i))
            menu.addSeparator()
            menu.addAction("Clear", lambda: (self.presets.clear(i), self.refresh_slots()))
        menu.exec(self.slot_btns[i].mapToGlobal(self.slot_btns[i].rect().bottomLeft()))

    def _rename_slot(self, i):
        name, ok = QInputDialog.getText(self, "Rename preset", f"Name for slot {i + 1}:",
                                        text=self.presets.name(i))
        if ok and name.strip():
            self.presets.rename(i, name.strip())
            self.refresh_slots()

    def export_mix(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export mix", "mix.ofmix.json",
                                              "Openface Mixer mix (*.json)")
        if path:
            slot = self.st.get("active_slot")
            export_mix(path, self.presets.name(slot) if slot is not None else "Mix", extract_mix(self.st))

    def import_mix(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import mix", "", "Openface Mixer mix (*.json)")
        if not path:
            return
        try:
            name, mix = import_mix(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import mix", f"Could not import {path}:\n{e}")
            return
        apply_mix(self.st, mix)
        self.st["active_slot"] = None
        self.rebuild_all()
        self.refresh_slots()
        self.push_matrix()
        self.statusBar().showMessage(f"Imported “{name}”", 4000)

    def reset_mix(self):
        if QMessageBox.question(self, "Reset mix",
                                "Reset all routing to the default (playback n → output n, "
                                "no input monitoring, no groups)?") == QMessageBox.Yes:
            apply_mix(self.st, extract_mix(default_state(self.dev.phones_pair(1))))
            self.st["active_slot"] = None
            self.rebuild_all()
            self.refresh_slots()
            self.push_matrix()

    # ------------------------------------------------------------------ engine sync and saving
    def push_matrix(self):
        self.engine.write_matrix(*control_room.apply(self.st, compute_matrix(self.st),
                                                     compute_out_gains(self.st)))
        self.engine.write_device(device_block(self.st, self.dev))
        self.matrix.update()
        self.control.refresh()
        self.update_arc_leds()
        self.schedule_save()

    # ------------------------------------------------------------------ ARC USB
    def arc_key(self, key, pressed):
        """An ARC key went down or up. Keys act on press; Talkback also on release."""
        action = arc.DEFAULT_KEYS[key]
        cr = control_room.control_room(self.st)
        if action == "talkback":
            if pressed:
                self._talkback_pressed = (time.monotonic(), not cr["talkback"])
                cr["talkback"] = not cr["talkback"]
            elif self._talkback_pressed:
                t, switched_on = self._talkback_pressed
                self._talkback_pressed = None
                if switched_on and time.monotonic() - t > arc.TALKBACK_HOLD_S:
                    cr["talkback"] = False     # held: momentary, like a talkback button
                else:
                    return
            self.push_matrix()
            return
        if not pressed:
            return
        if action.startswith("snapshot:"):
            slot = int(action.split(":")[1]) - 1
            if self.presets.slots[slot]:
                self.recall_slot(slot)
            return
        if action == "phones":
            cr["encoder"] = "main" if cr["encoder"] == "phones" else "phones"
            self.update_arc_leds()
            self.schedule_save()
            return
        control_room.toggle(self.st, action)
        self.push_matrix()

    def arc_encoder(self, clicks):
        """The encoder turns the Main Out (or Phones) master; its fader group follows, as when
        the fader is moved with the mouse."""
        key = strip_key("out", control_room.encoder_pair(self.st))
        old = get_strip_db(self.st, key)
        _pair, db = control_room.step_volume(self.st, clicks)
        moved = {key: db, **group_follow(self.st, key, old, db)}
        for k, val in moved.items():
            if k in self.strips:
                self.strips[k].set_fader(val)
        self.push_matrix()

    def arc_status(self, status):
        self.control.set_arc_status(status)
        if status == "connected":
            self.update_arc_leds()

    def update_arc_leds(self):
        slot = self.st.get("active_slot")
        self.arc.set_leds([arc.key_lit(self.st, a, slot) for a in arc.DEFAULT_KEYS])

    def schedule_save(self):
        self._save_timer.start()

    def save(self):
        save_state(self.st, self.dev)

    def ensure_engine(self):
        if self.engine.outdated():
            self.restart_engine()   # an older engine from before an upgrade is still running
            time.sleep(0.5)
        if self.engine.status() is None:
            self.save()   # so a freshly started engine loads our matrix
            ok, msg = self.engine.start()
            if not ok:
                QMessageBox.warning(self, "Engine", msg)
                return
            for _ in range(30):
                time.sleep(0.05)
                if self.engine.status() is not None:
                    break

    def restart_engine(self):
        self.save()
        self.engine.stop()
        time.sleep(0.3)
        self.engine.start()
        QTimer.singleShot(600, self.push_matrix)

    # ------------------------------------------------------------------ polling
    def poll_engine(self):
        s = self.engine.status()
        self.settings.update_hw_mixer(s)
        sep = "<span style='color:#4a4a4e'>&nbsp;│&nbsp;</span>"
        if s is None:
            txt = "<span style='color:#ff453a'>●</span>&nbsp; ENGINE STOPPED"
            self.settings.eng_label.setText("Engine is not running.")
            self.settings.eng_btn.setText("Start engine")
        else:
            hw_col = ("#34c759" if s["hw"] == "active" else
                      "#ffd60a" if s["hw"] in ("no-nodes", "starting") else "#ff453a")
            rate = f"{s['rate'] / 1000:g} kHz" if s["rate"] else "— kHz"
            limit = f"/{self.dev.max_routes}" if self.dev.max_routes else ""
            routes = (f"{s['hw_nodes']}{limit} routes" if s["hw"] in ("active", "no-nodes")
                      else "offline")
            txt = f"<span style='color:{hw_col}'>●</span>&nbsp; HW DSP{sep}{rate}{sep}{routes}"
            self.settings.eng_label.setText(
                ("Running" if s["processing"] else "Running (not responding)") +
                "<br>Playback sink: " +
                (f"linked to playback {self.dev.sink_channels}" if s["sink_linked"] else
                 f"not linked to the {self.dev.name.replace('RME ', '')}"))
            self.settings.eng_btn.setText("Restart engine")
            if s["rate"] and speed_mode(s["rate"]) != self.mode:
                self.rate, self.mode = s["rate"], speed_mode(s["rate"])
                self.rebuild_all()
        self.status.setText(txt)
        active = s is not None and s["hw"] == "active"
        if self.dev.key == "ff802":
            self.settings.update_ff802_status(s["dev_status"] if active else None)
        elif self.dev.key == "ff800":
            hw = self.st["hw"]
            if active and not hw["known"]:
                # the configuration is write only: start from what the 800 reports
                fireface800.settings_from_status(hw, s["dev_status2"])
                self.settings.refresh_ff800_settings()
                self.push_matrix()
            self.settings.update_ff800_status((s["dev_status"], s["dev_status2"])
                                              if active else None)

    def poll_hw(self):
        if self.dev.key == "digiface":
            self.settings.update_hw(self.hw.read())
        elif self.dev.key == "raydat":
            self.settings.update_raydat(self.hw.read())

    def poll_profile(self):
        if self.dev.key == "digiface":
            self.settings.update_profile(*pw_digiface_card())

    def update_meters(self):
        now = time.monotonic()
        dt, self._last_meter = now - self._last_meter, now
        src, out = self.engine.read_peaks()
        if src is None:
            return
        for s in self.row_in.strips:
            s.meter.push([src[c] for c in s.channels], dt)
        for s in self.row_play.strips:
            s.meter.push([src[N_IN + c] for c in s.channels], dt)
        for s in self.row_out.strips:
            s.meter.push([out[c] for c in s.channels], dt)

    def closeEvent(self, e):
        self.save()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    apply_theme(app)
    args = app.arguments()[1:]
    if "--device" in args and args.index("--device") + 1 < len(args):
        dev = DEVICES.get(args[args.index("--device") + 1])
        if dev is None:
            sys.exit(f"--device: one of {', '.join(DEVICES)}")
    else:
        dev = detect(load_device_choice())
    while True:
        save_device_choice(dev)
        w = MainWindow(dev)
        w.show()
        code = app.exec()
        if w.switch_to is None:
            sys.exit(code)
        dev = DEVICES[w.switch_to]
        w.arc.disconnect_port()      # so the next window can open the ARC USB
        w.deleteLater()
