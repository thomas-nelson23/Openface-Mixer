"""Main window: wires the model, the engine link, the hardware panel and the widgets together."""
import os
import sys
import time

from PySide6.QtCore import QProcess, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QColorDialog, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from . import APP_ID, APP_NAME
from . import arc, control_room
from .channel_settings import ChannelSettings
from .config import (
    device_block, load_device_choice, load_state, load_ui_scale, load_window, presets_file,
    save_device_choice, save_state, save_ui_scale, save_window,
)
from .control_panel import ArcLink, ControlRoomPanel
from .devices import DEVICES, detect
from .engine import Engine
from .hardware import Hardware, pw_digiface_card
from .matrix_view import MatrixView
from .model import (
    N_GROUPS, N_IN, N_PAIRS, N_SLOTS, NEG_INF, any_solo, apply_mix, compute_matrix,
    compute_out_gains, default_state, display_name, extract_mix, get_strip_db, group_follow,
    set_strip_color, set_strip_db, set_strip_hidden, set_strip_icon, speed_mode, strip_channels,
    strip_color, strip_hidden, strip_icon, strip_key,
)
from .presets import PresetBank, export_mix, import_mix
from .settings_panel import SettingsPanel, Sidebar, ask
from .theme import (
    ACCENT, CHANNEL_COLORS, CHANNEL_ICONS, GROUP_COLOR, KIND_COLOR, apply_theme, compact_boxes,
    icon_pixmap, px,
)
from .widgets import Row, Strip


class MainWindow(QMainWindow):
    def __init__(self, dev):
        super().__init__()
        self.dev = dev
        self.setWindowTitle(f"{APP_NAME} — {dev.name}")
        self.engine = Engine(dev)
        self.hw = Hardware(dev.alsa_card)
        self.ui_scale = load_ui_scale()    # applied by main() through theme.apply_theme
        self.st = load_state(dev)
        self.presets = PresetBank(presets_file(dev))
        self.switch_to = None              # device key to reopen the window for, see main()
        self.reopen_geometry = None        # where main() puts the window it reopens
        self.rate = 48000
        self.mode = 1                      # speed mode: 1x / 2x / 4x
        self.strips = {}                   # strip key -> Strip (currently built strips)
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self.save)
        self.engine_error = None           # why the engine could not be started, shown in Status
        self._last_meter = time.monotonic()

        central = QWidget()
        outer = QHBoxLayout(central)          # the mixer, then the settings sidebar
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        main = QWidget()
        outer.addWidget(main, 1)
        root = QVBoxLayout(main)
        root.setContentsMargins(px(12), px(10), px(12), px(10))
        root.setSpacing(px(10))
        self.topbar = QWidget()
        self.topbar.setObjectName("topbar")
        self.topbar.setLayout(self._build_top_bar())
        root.addWidget(self.topbar)
        edge = QFrame()
        edge.setObjectName("edge")
        root.addWidget(edge)

        body = QHBoxLayout()
        mixer_page = QWidget()
        # right-click on the workspace (not on a strip): bring hidden channels back
        mixer_page.setContextMenuPolicy(Qt.CustomContextMenu)
        mixer_page.customContextMenuRequested.connect(
            lambda pos: self.workspace_menu(mixer_page.mapToGlobal(pos)))
        rows = QVBoxLayout(mixer_page)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(px(10))
        self.row_in = Row("Hardware Inputs", "", KIND_COLOR["in"])
        self.row_play = Row("Playback", "", KIND_COLOR["play"])
        self.row_out = Row("Hardware Outputs", "master level · click a name tag to edit its submix",
                           KIND_COLOR["out"])
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
        self.settings = SettingsPanel(self.hw, dev, self.st, self.ui_scale)
        self.settings.eng_btn.clicked.connect(self.restart_engine)
        self.settings.device_selected.connect(self.switch_device)
        self.settings.hw_changed.connect(self.push_matrix)
        self.settings.shown_changed.connect(self.channels_shown)
        self.settings.names_changed.connect(self.set_custom_names)
        self.settings.export_btn.clicked.connect(self.export_mix)
        self.settings.import_btn.clicked.connect(self.import_mix)
        self.settings.reset_btn.clicked.connect(self.reset_mix)
        self.settings.ui_scale_changed.connect(self.set_ui_scale)
        self.settings.restart_requested.connect(self.restart_app)
        self.control = ControlRoomPanel(self.st)
        self.control.changed.connect(self.push_matrix)
        self.settings.add_control_room(self.control)
        compact_boxes(self.settings)
        self.arc = ArcLink(self)
        self.arc.key.connect(self.arc_key)
        self.arc.encoder.connect(self.arc_encoder)
        self.arc.status_changed.connect(self.arc_status)
        self._talkback_pressed = None     # (time, was it switched on by this press)
        root.addLayout(body, 1)
        self.sidebar = Sidebar(self.settings)
        self.sidebar.closed.connect(lambda: self.settings_btn.setChecked(False))
        self.settings_btn.toggled.connect(self.sidebar.set_open)
        self.settings_btn.toggled.connect(lambda _on: self.fit_top_bar())
        outer.addWidget(self.sidebar)
        self.setCentralWidget(central)
        self.toast = QLabel(central)      # short feedback ("Saved … in slot 3"), see notify()
        self.toast.setObjectName("toast")
        self.toast.hide()
        self._toast_timer = QTimer(self, singleShot=True, interval=3500, timeout=self.toast.hide)
        self._build_shortcuts()

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
        self.setMinimumHeight(px(640))   # the width's minimum comes from the layout
        # reopen as it was left: window size and the settings sidebar
        size, sidebar_open = load_window()
        if not sidebar_open:
            self.settings_btn.setChecked(False)
            self.sidebar.set_open(False, animate=False)
        width, height = size or (px(1600), px(960))
        self.fit_top_bar(width)          # sets the bar's compact minimum before the first resize
        self.resize(width, height)
        self.tab_focus_only()

    # ------------------------------------------------------------------ top bar
    def _build_top_bar(self):
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(px(8))
        wordmark = QLabel(
            f"<span style='font-family:Michroma;font-size:{px(14)}px;letter-spacing:{px(1.5)}px;"
            "color:white'>OPENFACE</span><span style='font-family:Michroma;"
            f"font-size:{px(14)}px;letter-spacing:{px(1.5)}px;color:#ff2a4f'>MIXER</span>")
        wordmark.setMinimumWidth(wordmark.sizeHint().width())   # never clipped
        top.addWidget(wordmark)
        top.addSpacing(px(8))

        seg = QWidget()
        seg.setObjectName("segment")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(px(2), px(2), px(2), px(2))
        sl.setSpacing(0)
        self.view_btns = []
        for i, name in enumerate(("Mixer", "Matrix")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setObjectName("segbtn")
            b.setToolTip(f"{name} view (Ctrl+{i + 1})")
            b.clicked.connect(lambda _=False, n=i: self.tabs.setCurrentIndex(n))
            sl.addWidget(b)
            self.view_btns.append(b)
        top.addWidget(seg)
        top.addSpacing(px(8))

        self.captions = [self._caption("SUBMIX"), self._caption("PRESETS")]
        top.addWidget(self.captions[0])
        self.submix = QComboBox()
        self.submix.setMinimumWidth(px(104))
        self.submix.activated.connect(self.select_out)
        top.addWidget(self.submix)
        top.addStretch(1)

        self.status = QLabel("")
        self.status.setObjectName("lcd")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setMinimumWidth(px(120))
        self.status.setCursor(Qt.PointingHandCursor)     # opens the Status section
        self.status.mousePressEvent = lambda _e: self.settings_btn.setChecked(True)
        self._status_short = False        # narrow window: just the light and "HW DSP"
        top.addWidget(self.status)
        top.addStretch(1)

        top.addWidget(self.captions[1])
        # the eight slots, or one "Presets" menu when the window is narrow
        self.slot_box = QWidget()
        slots = QHBoxLayout(self.slot_box)
        slots.setContentsMargins(0, 0, 0, 0)
        slots.setSpacing(px(3))
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
        top.addWidget(self.slot_box)
        self.store_btn = QPushButton("Store")
        self.store_btn.setObjectName("store")
        self.store_btn.setCheckable(True)
        self.store_btn.setToolTip("Click, then click a slot to save the current mix into it "
                                  "(Esc cancels). An empty slot saves with one click.")
        top.addWidget(self.store_btn)
        self.presets_menu = QMenu(self)
        self.presets_menu.aboutToShow.connect(self._fill_presets_menu)
        self.presets_btn = QPushButton("Presets ▾")
        self.presets_btn.setMenu(self.presets_menu)
        self.presets_btn.setVisible(False)
        top.addWidget(self.presets_btn)

        top.addSpacing(px(4))

        self.settings_btn = QPushButton()
        self.settings_btn.setObjectName("gear")
        self.settings_btn.setCheckable(True)
        self.settings_btn.setChecked(True)
        self.settings_btn.setToolTip("Show or hide settings (Ctrl+,)")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        gear = QIcon()
        size = px(14)
        for color, state in (("#d9eef2", QIcon.Off), ("#2ad4ff", QIcon.On)):
            pm = icon_pixmap("settings", color, size)
            if pm:
                gear.addPixmap(pm, QIcon.Normal, state)
        self.settings_btn.setIcon(gear)
        self.settings_btn.setIconSize(QSize(size, size))
        top.addWidget(self.settings_btn)
        return top

    def _build_shortcuts(self):
        """The standard settings key and a few for what people do most."""
        keys = (
            ("Ctrl+,", self.settings_btn.toggle),
            ("Ctrl+1", lambda: self.tabs.setCurrentIndex(0)),
            ("Ctrl+2", lambda: self.tabs.setCurrentIndex(1)),
            ("Escape", lambda: self.store_btn.setChecked(False)),
        )
        for key, slot in keys:
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(slot)

    def tab_focus_only(self):
        """Buttons take the keyboard focus from Tab, not from a click, so the focus ring only
        shows for people who use the keyboard."""
        for b in self.findChildren(QAbstractButton):
            if b.focusPolicy() != Qt.NoFocus:
                b.setFocusPolicy(Qt.TabFocus)

    def notify(self, text):
        """Brief feedback over the bottom of the mixer, gone after a few seconds. Only for
        things that are also visible elsewhere (nothing has to be read in time)."""
        self.toast.setText(text)
        self.toast.adjustSize()
        area = self.centralWidget().width() - (self.sidebar.width() if self.sidebar.is_open()
                                                 else 0)
        self.toast.move((area - self.toast.width()) // 2,
                        self.centralWidget().height() - self.toast.height() - px(24))
        self.toast.raise_()
        self.toast.show()
        self._toast_timer.start()

    @staticmethod
    def _caption(text):
        lbl = QLabel(text)
        lbl.setObjectName("caption")
        f = lbl.font()
        f.setLetterSpacing(QFont.AbsoluteSpacing, px(2.4))
        lbl.setFont(f)
        return lbl

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit_top_bar(e.size().width())

    def fit_top_bar(self, window_width=None):
        """Drop the top bar's less important parts, one step at a time, until it fits the
        window: first the SUBMIX / PRESETS captions, then the status readout shrinks to its
        light, then the eight preset slots fold into a Presets menu."""
        if window_width is None:
            window_width = self.width()
        # the bar's width after this resize (its own width() still shows the old layout)
        avail = window_width - 2 * px(12) - (self.sidebar.full_width if self.sidebar.is_open()
                                             else 0)
        lay = self.topbar.layout()
        steps = (self._show_captions, self._show_status_detail, self._show_slots)
        if self.topbar.minimumWidth() == 0:
            # the window may shrink down to the bar's most compact form, not its full one
            for show in steps:
                show(False)
            lay.activate()
            self.topbar.setMinimumWidth(lay.sizeHint().width())
        for show in steps:
            show(True)
        lay.activate()
        for show in steps:
            if lay.sizeHint().width() <= avail:
                break
            show(False)
            lay.activate()

    def _show_captions(self, on):
        for c in self.captions:
            c.setVisible(on)

    def _show_status_detail(self, on):
        if self._status_short == (not on):
            return
        self._status_short = not on
        self.status.setMinimumWidth(px(120) if on else 0)
        self.poll_engine()

    def _show_slots(self, on):
        self.slot_box.setVisible(on)
        self.store_btn.setVisible(on)
        self.presets_btn.setVisible(not on)

    def _fill_presets_menu(self):
        """The Presets menu of a narrow window: recall a slot, or store into one."""
        menu = self.presets_menu
        menu.clear()
        active = self.st.get("active_slot")
        for i in range(N_SLOTS):
            name = self.presets.name(i)
            a = menu.addAction(f"{i + 1}  {name}" if name else f"{i + 1}  (empty)")
            a.setEnabled(name is not None)
            a.setCheckable(True)
            a.setChecked(name is not None and i == active)
            a.triggered.connect(lambda _=False, n=i: self.recall_slot(n))
        menu.addSeparator()
        store = menu.addMenu("Store current mix in")
        for i in range(N_SLOTS):
            name = self.presets.name(i)
            store.addAction(f"{i + 1}  {name}" if name else f"{i + 1}  (empty)",
                            lambda _=False, n=i: self.store_slot(n))

    # ------------------------------------------------------------------ building strips
    def channels(self, kind):
        """The device's channels of this kind ("in", "play", "out") at the current speed."""
        return self.dev.channels(kind, self.mode)

    def standard_label(self, kind, c, pair=False):
        # playback channel n is meant for output n, so it is named like the output
        k = "out" if kind == "play" else kind
        return self.dev.pair_label(k, c, self.mode) if pair else self.dev.chan_label(k, c, self.mode)

    def label(self, kind, c, pair=False):
        """The strip's name: its custom name (while NAMES is on) or the standard one."""
        key = strip_key("out", c // 2) if kind == "out" else strip_key(kind, c)
        return display_name(self.st, key, self.standard_label(kind, c, pair))

    def shown(self, kind, c):
        """False if channel c is in a group hidden with the SHOW options, or its strip was
        hidden with right-click > Hide channel."""
        k = "out" if kind == "play" else kind
        if self.dev.channel_group(k, c, self.mode) in self.st["hidden"]:
            return False
        key = strip_key("out", c // 2) if kind == "out" else strip_key(kind, c)
        return not strip_hidden(self.st, key)

    def hidden_strips(self):
        """[(key, name)] of the strips hidden one by one, in channel order."""
        found = []
        for kind in ("in", "play"):
            for c in self.channels(kind):
                key = strip_key(kind, c)
                if strip_hidden(self.st, key):
                    pair = c % 2 == 0 and self.st["stereo"][kind][c // 2]
                    found.append((key, self.label(kind, c, pair)))
        for pair in self.out_pairs(all_pairs=True):
            key = strip_key("out", pair)
            if strip_hidden(self.st, key):
                found.append((key, self.label("out", 2 * pair, True)))
        return found

    def hide_strip(self, strip):
        set_strip_hidden(self.st, strip.key, True)
        self.channels_shown()
        self.notify(f"{strip.name} hidden · right-click empty space to show it")

    def unhide_strip(self, key):
        set_strip_hidden(self.st, key, False)
        self.channels_shown()

    def unhide_all(self):
        self.st["hidden_strips"] = []
        self.channels_shown()

    def workspace_menu(self, pos):
        menu = QMenu(self)
        hidden = self.hidden_strips()
        if hidden:
            sub = menu.addMenu("Unhide channel")
            for key, name in hidden:
                sub.addAction(name, lambda _=False, k=key: self.unhide_strip(k))
            menu.addAction("Show all hidden channels", self.unhide_all)
        else:       # a context menu shows only what can be done (HIG: hide, don't dim)
            menu.addAction("No hidden channels").setEnabled(False)
        menu.exec(pos)

    def out_pairs(self, all_pairs=False):
        return [c // 2 for c in self.channels("out")
                if c % 2 == 0 and (all_pairs or self.shown("out", c))]

    def rebuild_all(self):
        self.rebuild_sources("in")
        self.rebuild_sources("play")
        self.rebuild_outputs()
        self.refresh_control_channels()

    def refresh_control_channels(self):
        ins = self.channels("in")
        pairs = [(self.label("out", 2 * p, True), p) for p in self.out_pairs(all_pairs=True)]
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
            if not self.shown(kind, c):
                continue
            has_pair = c % 2 == 0 and c + 1 in present
            chans = strip_channels(self.st, kind, c) if has_pair else [c]
            stereo = len(chans) == 2
            title = self.label(kind, c, pair=stereo)
            key = strip_key(kind, c)
            s = Strip(key, title, chans, kind, standard=self.standard_label(kind, c, stereo),
                      color=strip_color(self.st, key), icon=strip_icon(self.st, key),
                      show_pan=True, show_stereo=has_pair,
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
            s.rename_requested.connect(lambda st=s: self.rename_strip(st))
            s.settings_requested.connect(lambda pos, st=s: self.channel_settings(st, pos))
            s.set_group(self.st["groups"].get(key))
            strips.append(s)
            self.strips[key] = s
        (self.row_in if kind == "in" else self.row_play).set_strips(strips)
        self.refresh_settings_buttons()
        self.refresh_sends(kind)
        self.matrix.rebuild()

    def rebuild_outputs(self):
        self.strips = {k: s for k, s in self.strips.items() if not k.startswith("out:")}
        strips = []
        self.submix.clear()
        for pair in self.out_pairs():
            title = self.label("out", 2 * pair, True)
            key = strip_key("out", pair)
            s = Strip(key, title, [2 * pair, 2 * pair + 1], "out", show_pan=False,
                      standard=self.standard_label("out", 2 * pair, True),
                      color=strip_color(self.st, key), icon=strip_icon(self.st, key),
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
            s.rename_requested.connect(lambda st=s: self.rename_strip(st))
            s.settings_requested.connect(lambda pos, st=s: self.channel_settings(st, pos))
            s.set_group(self.st["groups"].get(key))
            strips.append(s)
            self.strips[key] = s
            self.submix.addItem(title, pair)
        self.row_out.set_strips(strips)
        self.refresh_settings_buttons()
        self.matrix.rebuild()
        sel = self.st["selected"]
        if strips and sel not in [s.pair for s in strips]:
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
        self.push_matrix()

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
        st["names"].pop(strip_key(kind, c + 1), None)
        QTimer.singleShot(0, lambda: self.rebuild_sources(kind))
        self.push_matrix()

    # ------------------------------------------------------------------ device settings
    def channel_settings(self, strip, pos):
        """TotalMix's channel settings for a hardware input or output strip (Fireface 802)."""
        popup = ChannelSettings(self.dev, self.st["hw"], strip.kind, strip.channels,
                                strip.name, self)
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

    def set_ui_scale(self, scale):
        """Save the scale and reopen the window at it (main() lays everything out again)."""
        save_ui_scale(scale)
        self.reopen_geometry = self.geometry()
        self.switch_to = self.dev.key
        self.close()

    def restart_app(self):
        self.save()
        self.arc.disconnect_port()      # so the new process can open the ARC USB
        launched = QProcess.startDetached(
            sys.executable, ["-m", "openface_mixer", "--device", self.dev.key], os.getcwd())
        if isinstance(launched, tuple):     # PySide6 returns (ok, pid)
            launched = launched[0]
        if not launched:
            QMessageBox.warning(self, "Restart", "Could not restart Openface Mixer. Close it "
                                                 "and start it again to apply the change.")
            return
        QApplication.instance().quit()

    def _tab_changed(self, i):
        for n, b in enumerate(self.view_btns):
            b.setChecked(n == i)
        self.st["tab"] = i
        self.schedule_save()

    # ------------------------------------------------------------------ names and SHOW
    def rename_strip(self, strip):
        old = self.st["names"].get(strip.key, "")
        name, ok = QInputDialog.getText(self, "Rename channel",
                                        f"Name for {strip.standard} (empty = standard name):",
                                        text=old)
        if not ok:
            return
        name = name.strip()
        if name:
            self.st["names"][strip.key] = name
        else:
            self.st["names"].pop(strip.key, None)
        if name and not self.st["custom_names"]:
            self.settings.names_box.setChecked(True)   # show the name just given (rebuilds)
        else:
            self.rebuild_all()
        self.schedule_save()

    def set_custom_names(self, on):
        self.st["custom_names"] = on
        self.rebuild_all()
        self.schedule_save()

    def channels_shown(self):
        self.rebuild_all()
        self.schedule_save()

    # ------------------------------------------------------------------ fader groups
    def strip_menu(self, strip, pos):
        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self.rename_strip(strip))
        colors = menu.addMenu("Color")
        current = strip_color(self.st, strip.key)
        for name, hexcol in CHANNEL_COLORS:
            a = colors.addAction(self._swatch(hexcol), name)
            a.setCheckable(True)
            a.setChecked(current == hexcol)
            a.triggered.connect(lambda _=False, c=hexcol: self.set_color(strip, c))
        colors.addSeparator()
        colors.addAction("Custom…", lambda: self.pick_color(strip))
        default = colors.addAction("Default")
        default.setCheckable(True)
        default.setChecked(current is None)
        default.triggered.connect(lambda: self.set_color(strip, None))
        icons = menu.addMenu("Icon")
        cur_icon = strip_icon(self.st, strip.key)
        for section, entries in CHANNEL_ICONS:
            sub = icons.addMenu(section)
            for name, label in entries:
                pm = icon_pixmap(name, "#d9eef2", px(16))
                a = sub.addAction(QIcon(pm) if pm else QIcon(), label)
                a.setCheckable(True)
                a.setChecked(cur_icon == name)
                a.triggered.connect(lambda _=False, n=name: self.set_icon(strip, n))
        icons.addSeparator()
        none = icons.addAction("None")
        none.setCheckable(True)
        none.setChecked(cur_icon is None)
        none.triggered.connect(lambda: self.set_icon(strip, None))
        menu.addSeparator()
        grp = menu.addMenu("Fader group")
        for g in range(N_GROUPS + 1):
            a = (grp.addAction("None") if g == 0 else
                 grp.addAction(self._swatch(GROUP_COLOR[g].name()), f"Group {g}"))
            a.setCheckable(True)
            a.setChecked(strip.group == g)
            a.triggered.connect(lambda _=False, n=g: self.set_group(strip, n))
        menu.addAction("Fader to 0 dB", lambda: self.move_fader(strip, 0.0))
        menu.addAction("Fader to -∞", lambda: self.move_fader(strip, NEG_INF))
        menu.addSeparator()
        menu.addAction("Hide channel", lambda: self.hide_strip(strip))
        menu.exec(pos)

    @staticmethod
    def _swatch(hexcol):
        pm = QPixmap(px(14), px(14))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(hexcol))
        p.drawRoundedRect(pm.rect(), px(4), px(4))
        p.end()
        return QIcon(pm)

    def pick_color(self, strip):
        start = QColor(strip_color(self.st, strip.key) or strip.tag_color())
        col = QColorDialog.getColor(start, self, f"Color for {strip.name}")
        if col.isValid():
            self.set_color(strip, col.name())

    def set_color(self, strip, color):
        set_strip_color(self.st, strip.key, color)
        strip.set_color(color)
        self.matrix.rebuild()
        self.schedule_save()

    def set_icon(self, strip, icon):
        set_strip_icon(self.st, strip.key, icon)
        strip.set_icon(icon)
        self.schedule_save()

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
            self.store_slot(i)      # nothing to overwrite: no need to arm Store first

    def store_slot(self, i):
        name, ok = QInputDialog.getText(self, "Store preset", f"Name for slot {i + 1}:",
                                        text=self.presets.name(i) or f"Mix {i + 1}")
        if not ok:
            return
        name = name.strip() or f"Mix {i + 1}"
        self.presets.store(i, name, extract_mix(self.st))
        self.st["active_slot"] = i
        self.refresh_slots()
        self.schedule_save()
        self.notify(f"Saved “{name}” in slot {i + 1}")

    def recall_slot(self, i):
        apply_mix(self.st, self.presets.slots[i]["mix"])
        self.st["active_slot"] = i
        name = self.presets.name(i)
        self.rebuild_all()
        self.refresh_slots()
        self.push_matrix()
        self.notify(f"Recalled “{name}”")

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
        self.notify(f"Imported “{name}”")

    def reset_mix(self):
        if not ask(self, "Reset mix", "Reset all routing to the default (playback n → output n, "
                   "no input monitoring, no groups)? This can't be undone.", "Reset mix"):
            return
        apply_mix(self.st, extract_mix(default_state(self.dev.phones_pair(1))))
        self.st["active_slot"] = None
        self.rebuild_all()
        self.refresh_slots()
        self.push_matrix()
        self.notify("Mix reset to default")

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
                # shown in the Status section instead of an alert at launch
                self.engine_error = msg
                return
            for _ in range(30):
                time.sleep(0.05)
                if self.engine.status() is not None:
                    break

    def restart_engine(self):
        self.engine_error = None
        self.save()
        self.engine.stop()
        time.sleep(0.3)
        self.engine.start()
        QTimer.singleShot(600, self.push_matrix)

    # ------------------------------------------------------------------ polling
    def poll_engine(self):
        s = self.engine.status()
        self.settings.update_hw_mixer(s, self.engine_error)
        sep = "<span style='color:#2a4552'>&nbsp;&nbsp;·&nbsp;&nbsp;</span>"
        if s is None:
            txt = "<span style='color:#ff2a4f'>○</span>&nbsp; ENGINE STOPPED"
            self.status.setToolTip("The engine is not running · click for details")
        else:
            hw_col = ("#2ad4ff" if s["hw"] == "active" else
                      "#ffb020" if s["hw"] in ("no-nodes", "starting") else "#ff2a4f")
            rate = f"{s['rate'] / 1000:g} kHz" if s["rate"] else "— kHz"
            limit = f"/{self.dev.max_routes}" if self.dev.max_routes else ""
            routes = (f"{s['hw_nodes']}{limit} routes" if s["hw"] in ("active", "no-nodes")
                      else "offline")
            dot = "●" if s["hw"] in ("active", "no-nodes", "starting") else "○"
            txt = (f"<span style='color:{hw_col}'>{dot}</span>&nbsp; HW DSP" +
                   ("" if self._status_short else f"{sep}{rate}{sep}{routes}"))
            self.status.setToolTip(f"Hardware DSP: {rate}, {routes} · click for details")
            if s["rate"] and speed_mode(s["rate"]) != self.mode:
                self.rate, self.mode = s["rate"], speed_mode(s["rate"])
                self.rebuild_all()
        self.status.setText(txt)
        active = s is not None and s["hw"] == "active"
        if self.dev.family == "latter":
            self.settings.update_latter_status(s["dev_status"] if active else None)
        elif self.dev.family == "former":
            hw = self.st["hw"]
            if active and not hw["known"]:
                # the configuration is write only: start from what the device reports
                self.dev.proto.settings_from_status(hw, s["dev_status2"])
                self.settings.refresh_ff800_settings()
                self.push_matrix()
            self.settings.update_former_status((s["dev_status"], s["dev_status2"])
                                               if active else None)

    def poll_hw(self):
        if self.dev.key == "digiface":
            self.settings.update_hw(self.hw.read())
        elif self.dev.family == "hdspe":
            self.settings.update_hdspe(self.hw.read())

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
        size = (self.normalGeometry() if self.isMaximized() else self.geometry()).size()
        save_window(size.width(), size.height(), self.sidebar.is_open())
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    args = app.arguments()[1:]
    if "--device" in args and args.index("--device") + 1 < len(args):
        dev = DEVICES.get(args[args.index("--device") + 1])
        if dev is None:
            sys.exit(f"--device: one of {', '.join(DEVICES)}")
    else:
        dev = detect(load_device_choice())
    geometry = None
    while True:
        apply_theme(app, load_ui_scale())   # the window is laid out at the saved UI scale
        save_device_choice(dev)
        w = MainWindow(dev)
        if geometry is not None:
            w.setGeometry(geometry)
        w.show()
        code = app.exec()
        if w.switch_to is None:
            sys.exit(code)
        dev = DEVICES[w.switch_to]
        geometry = w.reopen_geometry
        w.arc.disconnect_port()      # so the next window can open the ARC USB
        w.deleteLater()
