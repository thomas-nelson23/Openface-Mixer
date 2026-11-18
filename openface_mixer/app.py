"""Main window: wires the model, the engine link, the hardware panel and the widgets together."""
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QColorDialog, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget,
)

from . import APP_ID, APP_NAME
from . import arc, control_room, fx, hdspe, osc_remote
from .channel_settings import ChannelSettings
from .fx_panels import DynamicsPanel, EqPanel, FxUnitPanel, RoomEqPanel
from .config import (
    device_block, load_device_choice, load_state, load_ui_scale, load_window, presets_file,
    previous_mix_file, save_device_choice, save_state, save_ui_scale, save_window,
)
from .control_panel import ArcLink, ControlRoomPanel
from .devices import DEVICES, detect
from .engine import Engine
from .hardware import Hardware, pw_digiface_card
from .matrix_view import MatrixView
from .model import (
    N_GROUPS, N_IN, N_SLOTS, N_SRC, NEG_INF, any_solo, apply_mix, clear_sends, clear_sends_outside,
    compute_matrix, compute_out_gains, copy_mix, copy_sends, default_state, display_name,
    extract_mix, get_strip_db, group_follow, group_members, mute_group, parse_key, paste_mix,
    paste_sends, remove_mirror, set_mirror, set_mute_group, set_stereo, set_strip_color,
    set_strip_db, set_strip_hidden, set_strip_icon, speed_mode, strip_channels, strip_color,
    strip_hidden, strip_icon, strip_key, submix_of, trim_sends,
)
from .presets import PresetBank, export_mix, import_mix
from .remote_link import LEVEL_MS, RemoteLink, RemotePanel, remote_settings
from .settings_panel import RED, SettingsPanel, Sidebar, ask, hw_color, light
from .theme import (
    ACCENT, CHANNEL_COLORS, CHANNEL_ICONS, GROUP_COLOR, KIND_COLOR, PANEL, SOLO, WARN,
    apply_theme, compact_boxes, icon_pixmap, px,
)
from .widgets import Row, Strip, small_button


def reason(e):
    """Why a file could not be read or written, without the error number and path that Python
    puts around it."""
    return getattr(e, "strerror", None) or str(e)


class MainWindow(QMainWindow):
    def __init__(self, dev, background=False):
        super().__init__()
        self.dev = dev
        self.background = background       # started at login: closing hides the window
        self.setWindowTitle(f"{APP_NAME} — {dev.name}")
        self.engine = Engine(dev)
        self.hw = Hardware(dev.alsa_card)
        self.ui_scale = load_ui_scale()    # applied by main() through theme.apply_theme
        self.st, problem = load_state(dev)
        self._load_notice = {       # said once, when the window is first shown
            "backup": "The saved mix could not be read. The last good copy was loaded.",
            "reset": "The saved mix was damaged. What could not be read was reset to the default.",
        }.get(problem)
        if dev.family == "hdspe":
            self.hw.exact = self.st["hw"]["card"]      # one of several cards of the model
        self.presets = PresetBank(presets_file(dev))
        self.switch_to = None              # device key to reopen the window for, see main()
        self._quitting = False             # Ctrl+Q: close for real even when started in the background
        self.reopen_geometry = None        # where main() puts the window it reopens
        self.rate = 48000
        self.mode = 1                      # speed mode: 1x / 2x / 4x
        self.strips = {}                   # strip key -> Strip (currently built strips)
        self._limited = set()              # HDSPe: crosspoints held at +6 dB (show_limited)
        self.fx_panels = {}                # (kind of panel, strip key) -> open effect panel
        self.fx_clipboard = None           # copied channel effects (Copy / Paste effects)
        self.mix_clipboard = None          # (name, pair, copy_mix()) from Copy mix
        self.sends_clipboard = None        # (name, copy_sends()) from Copy sends
        self.cue = None                    # output pair heard on Main Out (Cue), never saved
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self.save)
        self.engine_error = None           # why the engine could not be started, shown in Status
        self._engine_up = False            # the engine answered the last poll
        self._engine_due = 0.0             # until when a started engine may take to answer
        self._start_timer = QTimer(self, singleShot=True, interval=300, timeout=self.start_engine)
        self._last_meter = time.monotonic()
        self._shown = False                # the polls follow it, see pace_timers()
        self._meter_timer = QTimer(self, timeout=self.update_meters)
        self._engine_timer = QTimer(self, timeout=self.poll_engine)
        self._hw_timer = QTimer(self, timeout=self.poll_hw)
        self._profile_timer = QTimer(self, timeout=self.poll_profile)

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
        self.row_in = Row("in", "Hardware Inputs", "", KIND_COLOR["in"])
        self.row_play = Row("play", "Playback", "", KIND_COLOR["play"])
        self.row_out = Row("out", "Hardware Outputs",
                           "master level · click a name tag to edit its submix", KIND_COLOR["out"])
        self.row_in.head_row.addWidget(self._build_mix_tools())
        for r in (self.row_in, self.row_play, self.row_out):
            rows.addWidget(r)
            r.set_folded(r.kind in self.st["folded"])
            r.fold_changed.connect(self.rows_folded)
        rows.addStretch()           # takes the height when every row is folded
        self.matrix = MatrixView(self)
        self.tabs = QStackedWidget()
        self.tabs.addWidget(mixer_page)
        self.tabs.addWidget(self.matrix.scroll)
        self.tabs.currentChanged.connect(self._tab_changed)
        body.addWidget(self.tabs, 1)
        self.settings = SettingsPanel(self.hw, dev, self.st, self.ui_scale)
        self.settings.eng_btn.clicked.connect(self.restart_engine)
        self.settings.device_selected.connect(self.switch_device)
        self.settings.hw_changed.connect(self.push_matrix)
        self.settings.notice.connect(self.notify)
        self.settings.card_chosen.connect(self.restart_engine)
        self.settings.shown_changed.connect(self.channels_shown)
        self.settings.names_changed.connect(self.set_custom_names)
        self.settings.export_btn.clicked.connect(self.export_mix)
        self.settings.import_btn.clicked.connect(self.import_mix)
        self.settings.reset_btn.clicked.connect(self.reset_mix)
        self.settings.ui_scale_changed.connect(self.set_ui_scale)
        self.settings.restart_requested.connect(self.restart_app)
        self.settings.update_waiting.connect(self.show_update_waiting)
        self.control = ControlRoomPanel(self.st)
        self.control.changed.connect(self.push_matrix)
        self.control.recall.connect(self.recall_main)
        self.settings.add_control_room(self.control)
        self.remote_panel = RemotePanel(self.st, dev)
        self.settings.add_before_updates(self.remote_panel)
        self.remote = RemoteLink(self.remote_snapshot, self)
        self.remote.received.connect(self.remote_message)
        self.remote.status_changed.connect(
            lambda st: self.remote_panel.set_status(st, self.remote.target_error))
        self.remote.status_changed.connect(lambda _st: self.pace_timers())
        self.remote_panel.changed.connect(self.apply_remote_settings)
        self._hw_active = False
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
        self.toast.mousePressEvent = lambda _e: self.toast.hide()
        self._toast_timer = QTimer(self, singleShot=True, interval=3500, timeout=self.toast.hide)
        self._build_shortcuts()

        self.tabs.setCurrentIndex(int(self.st.get("tab", 0)))
        self._tab_changed(self.tabs.currentIndex())
        self.rebuild_all()
        self.refresh_slots()
        self.ensure_engine()
        self.push_matrix()
        self.apply_remote_settings()    # also starts the polls a hidden window needs
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

        # TotalMix FX: the Reverb / Echo panel (interfaces with effects only)
        self.fx_btn = QPushButton("FX")
        self.fx_btn.setCheckable(True)
        self.fx_btn.setToolTip("Reverb and Echo (Ctrl+E)")
        self.fx_btn.setCursor(Qt.PointingHandCursor)
        self.fx_btn.toggled.connect(self.show_fx_unit)
        self.fx_btn.setVisible(self.dev.fx is not None)
        top.addWidget(self.fx_btn)

        self.settings_btn = QPushButton()
        self.settings_btn.setObjectName("gear")
        self.settings_btn.setCheckable(True)
        self.settings_btn.setChecked(True)
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setIconSize(QSize(px(14), px(14)))
        self.show_update_waiting(False)
        top.addWidget(self.settings_btn)
        return top

    def _build_mix_tools(self):
        """TotalMix's Trim Gains, and its mute and solo groups, at the end of the inputs' title."""
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(3))
        self.trim_btn = small_button("Trim", "#ffffff", "#0b0f1c", width=40,
                                     tip="Trim: a channel's fader moves all of its sends by the "
                                         "same amount, in every submix")
        self.trim_btn.setChecked(self.st["trim"])
        self.trim_btn.toggled.connect(self.set_trim)
        lay.addWidget(self.trim_btn)
        self.group_btns = {}
        for kind, color, ink in (("mute", "#2ad4ff", "#021012"), ("solo", SOLO.name(), "#0b0f1c")):
            lay.addSpacing(px(8))
            lay.addWidget(self._caption(kind.upper()))
            for g in range(1, N_GROUPS + 1):
                b = small_button(str(g), color, ink, width=20)
                b.clicked.connect(lambda _=False, k=kind, n=g: self.toggle_group(k, n))
                b.setAccessibleName(f"{kind.title()} group {g}")
                lay.addWidget(b)
                self.group_btns[kind, g] = b
        for b in (self.trim_btn, *self.group_btns.values()):
            b.setFixedHeight(px(16))        # as high as the row's title, which sets the line
        return box

    def show_update_waiting(self, waiting):
        """The gear button, with a dot while the Updates section has an update to install."""
        gear = QIcon()
        size = px(14)
        for color, state in (("#d9eef2", QIcon.Off), ("#2ad4ff", QIcon.On)):
            pm = icon_pixmap("settings", color, size)
            if pm and waiting:
                pm = QPixmap(pm)
                p = QPainter(pm)
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(QPen(PANEL, px(1)))        # a dark rim, so it stands off the gear
                p.setBrush(WARN)
                p.drawEllipse(QRectF(size - px(6) - 0.5, 0.5, px(6), px(6)))
                p.end()
            if pm:
                gear.addPixmap(pm, QIcon.Normal, state)
        self.settings_btn.setIcon(gear)
        self.settings_btn.setToolTip("Show or hide settings (Ctrl+,)"
                                     + ("\nAn update is waiting under Updates" if waiting else ""))

    def _build_shortcuts(self):
        """The standard settings key and a few for what people do most."""
        keys = (
            ("Ctrl+,", self.settings_btn.toggle),
            ("Ctrl+E", lambda: self.dev.fx is not None and self.fx_btn.toggle()),
            ("Ctrl+1", lambda: self.tabs.setCurrentIndex(0)),
            ("Ctrl+2", lambda: self.tabs.setCurrentIndex(1)),
            ("Escape", self.escape),
            ("Ctrl+W", self.close_fx_panel),
            ("Ctrl+Q", self.quit_app),          # also quits when closing only hides (--background)
        )
        for key, slot in keys:
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(slot)

    def escape(self):
        """Esc un-arms Store; with nothing to un-arm it closes an effects panel."""
        if self.store_btn.isChecked():
            self.store_btn.setChecked(False)
        else:
            self.close_fx_panel()

    def tab_focus_only(self):
        """Buttons take the keyboard focus from Tab, not from a click, so the focus ring only
        shows for people who use the keyboard."""
        for b in self.findChildren(QAbstractButton):
            if b.focusPolicy() != Qt.NoFocus:
                b.setFocusPolicy(Qt.TabFocus)

    def notify(self, text, stay=False):
        """Brief feedback over the bottom of the mixer, gone after a few seconds. Only for
        things that are also visible elsewhere (nothing has to be read in time); anything else
        is shown with stay, until it is clicked or the next message comes."""
        self.toast.setText(text)
        self.toast.adjustSize()
        area = self.centralWidget().width() - (self.sidebar.width() if self.sidebar.is_open()
                                                 else 0)
        self.toast.move((area - self.toast.width()) // 2,
                        self.centralWidget().height() - self.toast.height() - px(24))
        self.toast.raise_()
        self.toast.show()
        if stay:
            self._toast_timer.stop()
        else:
            self._toast_timer.start()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._shown:
            self._shown = True
            self.engine.read_peaks()    # tells the engine to read the meters again, and
            self._last_meter = time.monotonic()    # drops the peaks held while nobody looked
            self.pace_timers()
            for poll in (self.poll_engine, self.poll_hw, self.poll_profile):
                poll()                  # what the window shows is as old as the last hide
        if self._load_notice:
            QTimer.singleShot(0, lambda text=self._load_notice: self.notify(text, stay=True))
            self._load_notice = None

    def hideEvent(self, e):
        super().hideEvent(e)
        self._shown = False
        self.pace_timers()

    def pace_timers(self):
        """Nothing polls for a window nobody sees (closed with --background, never opened, or
        minimised where the desktop says so). Shown: the meters 30 times a second, the engine
        status twice, the ALSA controls every second (amixer) and the PipeWire profile every
        3 s (pactl). Hidden: amixer and pactl stop. The engine status is still read, every 2 s:
        it is also how the window follows the interface for the ARC and the network remote
        (connected or not, sample rate, a FireWire unit's clock). The meters are only read
        while the remote sends levels to a controller, at the rate it sends them; while
        nobody reads them the engine reads the device's meters at its idle rate. An engine
        that was just started is looked for every 50 ms, shown or not."""
        levels = LEVEL_MS if self.remote.wants_levels() else 0
        engine = 50 if self._engine_due else 500 if self._shown else 2000
        for timer, ms in ((self._meter_timer, 33 if self._shown else levels),
                          (self._engine_timer, engine),
                          (self._hw_timer, 1000 if self._shown else 0),
                          (self._profile_timer, 3000 if self._shown else 0)):
            if not ms:
                timer.stop()
            elif not timer.isActive() or timer.interval() != ms:
                timer.start(ms)

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
        for old in menu.findChildren(QMenu):     # the last Store submenu
            old.deleteLater()
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
        self._add_restore_action(menu)

    # ------------------------------------------------------------------ building strips
    def channels(self, kind):
        """The device's channels of this kind ("in", "play", "out") at the current speed."""
        return self.dev.channels(kind, self.mode)

    def standard_label(self, kind, c, pair=False):
        # playback channel n is meant for output n, so it is named like the output
        k = "out" if kind == "play" else kind
        return self.dev.pair_label(k, c, self.mode) if pair else self.dev.chan_label(k, c, self.mode)

    def label(self, kind, c, pair=False):
        """The strip's name: its custom name (while custom names are on) or the standard
        one."""
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
        menu.deleteLater()

    def rows_folded(self):
        """A row was folded or unfolded: remembered with the device's state."""
        self.st["folded"] = [r.kind for r in (self.row_in, self.row_play, self.row_out)
                             if r.folded]
        self.schedule_save()

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
                      show_solo=True, show_settings=(kind == "in" and any(
                          self.dev.channel_has_settings(kind, ch) for ch in chans)) or (
                          self.dev.fx is not None),
                      show_fx=kind == "in" and self.dev.fx is not None)
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
            s.eq_requested.connect(lambda st=s: self.toggle_fx_panel(st, "eq"))
            s.dyn_requested.connect(lambda st=s: self.toggle_fx_panel(st, "dyn"))
            s.set_group(self.st["groups"].get(key))
            strips.append(s)
            self.strips[key] = s
        (self.row_in if kind == "in" else self.row_play).set_strips(strips)
        self.close_stale_fx_panels()
        self.refresh_settings_buttons()
        self.refresh_sends(kind)
        self.matrix.rebuild()
        if self.solo_as_shown(kind):
            self.push_matrix()

    def solo_as_shown(self, kind):
        """Solo is what the strips show: off for a channel without one (hidden, or not there
        at this sample rate), which would silence the submix with nothing on screen to say
        why, and the same on both channels of a linked pair. True if that changed any."""
        solo = self.st["solo"][kind]
        shown = [False] * len(solo)
        for s in (self.row_in if kind == "in" else self.row_play).strips:
            for c in s.channels:
                shown[c] = solo[s.channels[0]]
        changed = shown != solo
        solo[:] = shown
        return changed

    def rebuild_outputs(self):
        self.strips = {k: s for k, s in self.strips.items() if not k.startswith("out:")}
        strips = []
        self.submix.clear()
        for pair in self.out_pairs():
            title = self.label("out", 2 * pair, True)
            key = strip_key("out", pair)
            s = Strip(key, title, [2 * pair, 2 * pair + 1], "out", show_pan=False, show_cue=True,
                      standard=self.standard_label("out", 2 * pair, True),
                      color=strip_color(self.st, key), icon=strip_icon(self.st, key),
                      show_settings=any(self.dev.channel_has_settings("out", ch)
                                        for ch in (2 * pair, 2 * pair + 1)),
                      show_fx=self.dev.fx is not None)
            s.pair = pair
            o = self.st["out"][pair]
            s.set_fader(NEG_INF if o["gain"] is None else o["gain"])
            s.mute.setChecked(o["mute"])
            s.cue.toggled.connect(lambda on, pr=pair: self.set_cue(pr, on))
            s.clicked.connect(lambda pr=pair: self.select_out(pr, by_pair=True))
            s.fader.changed.connect(lambda db, st=s: self.on_fader(st, db))
            s.mute.toggled.connect(lambda on, pr=pair: self.set_out_mute(pr, on))
            s.context_requested.connect(lambda pos, st=s: self.strip_menu(st, pos))
            s.rename_requested.connect(lambda st=s: self.rename_strip(st))
            s.settings_requested.connect(lambda pos, st=s: self.channel_settings(st, pos))
            s.eq_requested.connect(lambda st=s: self.toggle_fx_panel(st, "eq"))
            s.dyn_requested.connect(lambda st=s: self.toggle_fx_panel(st, "dyn"))
            s.set_group(self.st["groups"].get(key))
            strips.append(s)
            self.strips[key] = s
            self.submix.addItem(title, pair)
        self.row_out.set_strips(strips)
        self.close_stale_fx_panels()
        self.refresh_settings_buttons()
        self.matrix.rebuild()
        sel = self.st["selected"]
        if strips and sel not in [s.pair for s in strips]:
            sel = strips[-1].pair
        self.select_out(sel, by_pair=True)
        cue = self.cue
        self.refresh_cue()
        if self.cue != cue:
            self.push_matrix()

    def refresh_sends(self, kind):
        """Show the sends into the selected output pair on the source faders."""
        row = self.row_in if kind == "in" else self.row_play
        p = submix_of(self.st, self.st["selected"])
        for s in row.strips:
            gdb, pan = self.st["sends"][kind][s.channels[0]][p]
            s.set_fader(NEG_INF if gdb is None else gdb)
            s.pan.set_value(pan)
        self.show_limited()
        self.refresh_groups()

    # ------------------------------------------------------------------ model updates
    def select_out(self, idx, by_pair=False):
        pair = idx if by_pair else self.submix.itemData(idx)
        self.st["selected"] = pair
        for s in self.row_out.strips:
            s.set_selected(s.pair == pair)
        i = self.submix.findData(pair)
        if i >= 0:
            self.submix.setCurrentIndex(i)
            hint = f"<b style='color:{ACCENT.name()}'>{self.submix.itemText(i)}</b>"
            src = self.st["mirror"].get(strip_key("out", pair))
            if src is not None:
                hint += f" (mirrors {self.label('out', 2 * src, True)})"
            hint = (f"Trim: faders move every submix · showing&nbsp; {hint}" if self.st["trim"]
                    else f"faders send to&nbsp; {hint}")
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
        moved = {strip.key: old}
        if strip.group and not (QApplication.keyboardModifiers() & Qt.ShiftModifier):
            before = {k: get_strip_db(self.st, k) for k in group_members(self.st, strip.key)}
            for key, val in group_follow(self.st, strip.key, old, db).items():
                moved[key] = before[key]
                if key in self.strips:
                    self.strips[key].set_fader(val)
        # Trim Gains: the other submixes follow (trim_sends), but not while a send is being
        # switched on from -∞ here, which would lift them all to +6 dB
        if self.st["trim"] and not strip.fader.dragged_from_off():
            for key, was in moved.items():
                now = get_strip_db(self.st, key)
                shown = trim_sends(self.st, key, was, now)
                if shown != now and key in self.strips:
                    self.strips[key].set_fader(shown)
        self.push_matrix()

    def set_send_at(self, kind, chans, pair, db):
        """Used by the matrix view: set the send level of chans into a given output pair."""
        p = submix_of(self.st, pair)        # a mirrored pair's cells are its source's
        for c in chans:
            self.st["sends"][kind][c][p][0] = None if db is None else round(db, 2)
        if p == submix_of(self.st, self.st["selected"]):
            self.refresh_sends(kind)
        self.push_matrix()

    def set_pan(self, kind, chans, pan):
        p = submix_of(self.st, self.st["selected"])
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
        set_stereo(self.st, kind, c, on)
        QTimer.singleShot(0, lambda: self.rebuild_sources(kind))
        self.push_matrix()

    # ------------------------------------------------------------------ device settings
    def channel_settings(self, strip, pos):
        """TotalMix's channel settings for a strip (Fireface 802, UCX II, 800, 400)."""
        popup = ChannelSettings(self.dev, self.st["hw"], strip.kind, strip.channels,
                                strip.name, self)
        popup.setAttribute(Qt.WA_DeleteOnClose)
        if self.dev.family == "former":   # these are all part of the configuration
            popup.changed.connect(lambda: self.st["hw"].update(edited=True))
        popup.changed.connect(self.push_matrix)
        popup.changed.connect(self.refresh_settings_buttons)
        popup.room_eq_requested.connect(lambda: self.open_fx_panel(strip, "room"))
        popup.move(pos)
        popup.show()

    def refresh_settings_buttons(self):
        f = self.st["hw"].get("fx") if self.dev.fx is not None else None
        for s in self.strips.values():
            own = fx.effect_channels(s.kind, s.channels)
            if s.kind == "play":
                s.set_settings_active(bool(f) and any(f["send"]["play"][c] is not None
                                                      for c in own))
                continue
            on = any(self.dev.channel_settings_active(self.st, s.kind, c) for c in s.channels)
            if f:
                values = f["ret"] if s.kind == "out" else f["send"]["in"]
                on = on or any(values[c] is not None for c in own) or (
                    s.kind == "out" and any(fx.room_active(f, c) for c in s.channels))
                s.set_fx_active(any(fx.eq_active(f, s.kind, c) for c in own),
                                any(fx.dyn_active(f, s.kind, c) for c in own),
                                [which for which in ("eq", "dyn")
                                 if (which, s.key) in self.fx_panels])
            s.set_settings_active(on)

    # ------------------------------------------------------------------ TotalMix FX effects
    def open_fx_panel(self, strip, which):
        """The strip's EQ or Dynamics panel, in the row right of the strip (EQ before Dynamics),
        or the Room EQ of a UCX II output in a window of its own. One per strip and kind."""
        key = (which, strip.key)
        panel = self.fx_panels.get(key)
        if panel is None:
            f, model = self.st["hw"]["fx"], self.dev.fx
            if which == "room":
                panel = RoomEqPanel(strip.name, f, model, strip.channels, self)
                panel.adjustSize()
                # beside the strip, kept on the screen
                pos = strip.mapToGlobal(strip.rect().topRight())
                screen = strip.screen().availableGeometry()
                x = min(pos.x() + px(4), screen.right() - panel.width())
                y = min(max(screen.top(), pos.y()), screen.bottom() - panel.height())
                panel.move(max(screen.left(), x), y)
            else:
                cls = EqPanel if which == "eq" else DynamicsPanel
                panel = cls(strip.name, f, model, strip.kind, strip.channels)
                panel.set_tag(strip.name, strip.tag_color())
                self.row_of(strip).dock(panel, strip.key, first=which == "eq")
            self.add_fx_panel(key, panel)
        if which == "room":
            panel.show()
            panel.raise_()
            panel.activateWindow()
        else:
            self.row_of(strip).reveal(panel)
        panel.setFocus()                    # Esc and Ctrl+W close it

    def toggle_fx_panel(self, strip, which):
        """The strip's EQ and D buttons open the panel and close it again."""
        panel = self.fx_panels.get((which, strip.key))
        if panel is None:
            self.open_fx_panel(strip, which)
        else:
            panel.close()

    def row_of(self, strip):
        return {"in": self.row_in, "play": self.row_play, "out": self.row_out}[strip.kind]

    def add_fx_panel(self, key, panel):
        panel.setAttribute(Qt.WA_DeleteOnClose)
        panel.changed.connect(self.fx_changed)
        panel.closed.connect(lambda: self.fx_panel_closed(key, panel))
        self.fx_panels[key] = panel
        self.refresh_settings_buttons()

    def fx_panel_closed(self, key, panel):
        if self.fx_panels.get(key) is panel:
            del self.fx_panels[key]
        which, strip, focus = key[0], self.strips.get(key[1]), self.focusWidget()
        if which == "unit":
            self.fx_btn.setChecked(False)
        elif strip is not None and focus is not None and (
                focus is panel or panel.isAncestorOf(focus)):
            (strip.eq_btn if which == "eq" else strip.dyn_btn).setFocus()   # back where it began
        self.refresh_settings_buttons()

    def close_fx_panel(self):
        """Esc and Ctrl+W with the keyboard outside a panel: close the one opened last."""
        shown = [p for p in self.fx_panels.values() if p.isVisible()]
        if shown:
            shown[-1].close()

    def close_stale_fx_panels(self):
        """After a rebuild (stereo link, speed change, a new name): close panels whose strip is
        gone or now covers other channels, and show the strip's name and colour on the others."""
        for (which, key), panel in list(self.fx_panels.items()):
            if which == "unit":
                continue
            strip = self.strips.get(key)
            if strip is None or strip.channels != panel.channels:
                panel.close()
            elif which != "room":
                panel.set_tag(strip.name, strip.tag_color())

    def show_fx_unit(self, on):
        """Reverb and Echo, at the end of the outputs row (the FX button)."""
        panel = self.fx_panels.get(("unit", ""))
        if panel is not None and self.tabs.currentIndex() == 1:
            self.tabs.setCurrentIndex(0)    # it is open behind the Matrix: show it
            self.fx_btn.setChecked(True)
            self.row_out.reveal(panel)
        elif not on:
            if panel is not None:
                panel.close()
        elif panel is None:
            self.tabs.setCurrentIndex(0)
            self.row_out.set_folded(False)
            panel = FxUnitPanel(self.st["hw"]["fx"], self.dev.fx)
            self.row_out.dock(panel)
            self.add_fx_panel(("unit", ""), panel)
            panel.setFocus()

    def fx_changed(self):
        self.push_matrix()
        self.refresh_settings_buttons()

    def copy_effects(self, strip):
        self.fx_clipboard = fx.copy_channel(self.st["hw"]["fx"], strip.kind, strip.channels[0])
        self.notify(f"Copied the effects of {strip.name}")

    def paste_effects(self, strip):
        fx.paste_channel(self.st["hw"]["fx"], strip.kind,
                         fx.effect_channels(strip.kind, strip.channels), self.fx_clipboard,
                         self.dev.fx)
        for which in ("eq", "dyn"):
            panel = self.fx_panels.get((which, strip.key))
            if panel is not None:
                panel.refresh()
        self.fx_changed()
        self.notify(f"Pasted effects into {strip.name}")

    def quit_app(self):
        """Quit for real: the close event saves the mix and the window, then the loop ends."""
        self._quitting = True
        self.close()
        QApplication.quit()

    def switch_device(self, key):
        if key == self.dev.key:
            return
        self.switch_to = key
        self.close()
        QApplication.quit()             # also ends app.exec() when started with --background

    def set_ui_scale(self, scale):
        """Save the scale and reopen the window at it (main() lays everything out again)."""
        save_ui_scale(scale)
        self.reopen_geometry = self.geometry()
        self.switch_to = self.dev.key
        self.close()
        QApplication.quit()             # also ends app.exec() when started with --background

    def restart_app(self):
        self.save()
        self.arc.disconnect_port()      # so the new process can open the ARC USB
        self.remote.stop()              # and bind the remote's port
        server = QApplication.instance().findChild(QLocalServer)
        if server is not None:
            server.close()              # or the new process would hand over to this one and exit
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
        self._group_menu(menu, "Fader group", strip.group, lambda n: self.set_group(strip, n))
        if strip.kind != "out":
            sel = self.st["selected"]
            self._group_menu(menu, f"Mute group in {self.label('out', 2 * sel, True)}",
                             mute_group(self.st, strip.key, submix_of(self.st, sel)),
                             lambda n: self.choose_mute_group(strip, n))
            self._group_menu(menu, "Solo group", self.st["solo_groups"].get(strip.key, 0),
                             lambda n: self.choose_solo_group(strip, n))
        menu.addAction("Fader to 0 dB", lambda: self.move_fader(strip, 0.0))
        menu.addAction("Fader to -∞", lambda: self.move_fader(strip, NEG_INF))
        if self.dev.fx is not None and strip.kind in ("in", "out"):
            menu.addSeparator()
            if not strip.folded:        # the panels open beside the strip's controls
                menu.addAction("EQ…", lambda: self.open_fx_panel(strip, "eq"))
                menu.addAction("Dynamics…", lambda: self.open_fx_panel(strip, "dyn"))
            menu.addAction("Copy effects", lambda: self.copy_effects(strip))
            paste = menu.addAction("Paste effects", lambda: self.paste_effects(strip))
            paste.setEnabled(self.fx_clipboard is not None)
        self._add_submix_tools(menu, strip)
        menu.addSeparator()
        menu.addAction("Hide channel", lambda: self.hide_strip(strip))
        menu.exec(pos)
        menu.deleteLater()

    def _group_menu(self, menu, title, current, choose):
        grp = menu.addMenu(title)
        for g in range(N_GROUPS + 1):
            a = (grp.addAction("None") if g == 0 else
                 grp.addAction(self._swatch(GROUP_COLOR[g].name()), f"Group {g}"))
            a.setCheckable(True)
            a.setChecked(current == g)
            a.triggered.connect(lambda _=False, n=g: choose(n))

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
        self.close_stale_fx_panels()        # the panels' name tags show the colour too
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

    # ------------------------------------------------------------------ mute and solo groups
    def choose_mute_group(self, strip, group):
        """The strip's send into the submix being edited goes into mute group group (0: none)."""
        set_mute_group(self.st, strip.key, submix_of(self.st, self.st["selected"]), group)
        self.push_matrix()

    def choose_solo_group(self, strip, group):
        if group:
            self.st["solo_groups"][strip.key] = group
        else:
            self.st["solo_groups"].pop(strip.key, None)
        self.refresh_groups()
        self.schedule_save()

    def solo_group_channels(self, group):
        """[(kind, channel)] of the strips in the solo group that are shown."""
        return [(s.kind, c) for key, g in self.st["solo_groups"].items() if g == group
                for s in [self.strips.get(key)] if s is not None for c in s.channels]

    def toggle_group(self, kind, group):
        """A mute group button switches its sends' mutes on or off; a solo group button solos
        its channels, or switches their solo off when they all are soloed, as TotalMix does."""
        if kind == "mute":
            grp = self.st["mute_groups"][group - 1]
            grp["on"] = not grp["on"] and bool(grp["sends"])
        else:
            chans = self.solo_group_channels(group)
            on = not all(self.st["solo"][k][c] for k, c in chans)
            for k, c in chans:
                self.st["solo"][k][c] = on
            self.sync_strips()
        self.push_matrix()

    def refresh_groups(self):
        """The group buttons, and the mute buttons of channels a mute group silences in the
        submix being edited."""
        sel = submix_of(self.st, self.st["selected"])
        for (kind, g), b in self.group_btns.items():
            if kind == "mute":
                sends = self.st["mute_groups"][g - 1]["sends"]
                on = self.st["mute_groups"][g - 1]["on"]
                names = [f"{self.key_name(k)} in {self.label('out', 2 * p, True)}"
                         for k, p in sends]
            else:
                chans = self.solo_group_channels(g)
                on = bool(chans) and all(self.st["solo"][k][c] for k, c in chans)
                names = [self.key_name(k) for k, n in self.st["solo_groups"].items() if n == g]
            b.setChecked(on)
            b.setEnabled(bool(names))
            b.setToolTip(f"{kind.title()} group {g}: {', '.join(names)}" if names else
                         f"{kind.title()} group {g} is empty · add channels from their "
                         "right-click menu")
        for s in self.strips.values():
            if s.kind != "out":
                g = mute_group(self.st, s.key, sel)
                s.set_group_muted(g if g and self.st["mute_groups"][g - 1]["on"] else 0)

    def key_name(self, key):
        """The name of the strip with this key, as it shows (or would)."""
        kind, c = parse_key(key)
        return self.label(kind, c, len(strip_channels(self.st, kind, c)) == 2)

    # ------------------------------------------------------------------ Trim, Cue and submixes
    def set_trim(self, on):
        self.st["trim"] = on
        self.select_out(self.st["selected"], by_pair=True)     # the rows say so
        self.schedule_save()

    def set_cue(self, pair, on):
        """TotalMix's Cue: one output's submix on Main Out at a time, until its Cue is pressed
        again or another output's is."""
        self.cue = pair if on else None
        self.push_matrix()

    def refresh_cue(self):
        """Cue ends when its output becomes Main Out or goes out of sight."""
        main = control_room.control_room(self.st)["main"]
        if self.cue == main or self.cue not in self.out_pairs():
            self.cue = None
        for s in self.row_out.strips:
            s.cue.blockSignals(True)
            s.cue.setChecked(s.pair == self.cue)
            s.cue.setEnabled(s.pair != main)
            s.cue.blockSignals(False)
            s.show_flags()                  # a folded strip's edge

    def _add_submix_tools(self, menu, strip):
        """TotalMix's copy, paste and mirror of an output's submix, and copy, paste and clear
        of a channel's sends."""
        menu.addSeparator()
        if strip.kind == "out":
            clip = self.mix_clipboard
            menu.addAction("Copy mix", lambda: self.copy_mix(strip))
            menu.addAction(f"Paste mix from {clip[0]}" if clip else "Paste mix",
                           lambda: self.paste_mix(strip)).setEnabled(clip is not None)
            mirrors = self.st["mirror"].get(strip.key)
            if mirrors is not None:
                menu.addAction(f"Remove mirror of {self.label('out', 2 * mirrors, True)}",
                               lambda: self.change_mix(lambda st: remove_mirror(st, strip.pair),
                                                       f"{strip.name} has its own mix again"))
            else:
                menu.addAction(f"Mirror mix of {clip[0]}" if clip else "Mirror mix",
                               lambda: self.mirror_mix(strip)).setEnabled(
                    clip is not None and submix_of(self.st, clip[1]) != strip.pair)
        else:
            clip = self.sends_clipboard
            menu.addAction("Copy sends", lambda: self.copy_sends(strip))
            menu.addAction(f"Paste sends from {clip[0]}" if clip else "Paste sends",
                           lambda: self.change_mix(
                               lambda st: paste_sends(st, strip.kind, strip.channels[0], clip[1]),
                               f"Pasted the sends of {clip[0]} into {strip.name}")).setEnabled(
                clip is not None)
            menu.addAction("Clear sends…", lambda: self.clear_sends(strip))

    def change_mix(self, change, message):
        """Make a submix tool's change on a copy of the mix and use it as a recall would, so
        that Restore previous mix can take it back."""
        work = {**self.st, **extract_mix(self.st)}
        change(work)
        self.replace_mix(extract_mix(work), message, self.st["active_slot"])

    def copy_mix(self, strip):
        self.mix_clipboard = (strip.name, strip.pair, copy_mix(self.st, strip.pair))
        self.notify(f"Copied the mix of {strip.name}")

    def paste_mix(self, strip):
        name, _pair, mix = self.mix_clipboard
        self.change_mix(lambda st: paste_mix(st, strip.pair, mix),
                        f"Pasted the mix of {name} into {strip.name}")

    def mirror_mix(self, strip):
        name, pair, _mix = self.mix_clipboard
        self.change_mix(lambda st: set_mirror(st, strip.pair, pair),
                        f"{strip.name} mirrors {name}")

    def copy_sends(self, strip):
        self.sends_clipboard = (strip.name, copy_sends(self.st, strip.kind, strip.channels[0]))
        self.notify(f"Copied the sends of {strip.name}")

    def clear_sends(self, strip):
        if ask(self, "Clear sends", f"Switch off every send of {strip.name}, in every submix?",
               "Clear"):
            self.change_mix(lambda st: clear_sends(st, strip.kind, strip.channels[0]),
                            f"Cleared the sends of {strip.name}")

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
        self.replace_mix(self.presets.slots[i]["mix"], f"Recalled “{self.presets.name(i)}”", i)

    def replace_mix(self, mix, message, slot=None):
        """Recall, Import, Reset and Restore all end here. The mix that is replaced is written
        to one file first, overwritten each time, so that a slip can be taken back with
        "Restore previous mix". Sends on channels this interface doesn't have are switched off,
        so a mix from a bigger interface leaves no routing that no strip shows."""
        try:
            export_mix(previous_mix_file(self.dev), "Previous mix", extract_mix(self.st))
        except OSError:
            pass                    # a full disk must not stop a recall
        apply_mix(self.st, mix)
        clear_sends_outside(self.st, self.dev.all_channels)
        self.st["active_slot"] = slot
        self.rebuild_all()
        self.refresh_slots()
        self.push_matrix()
        self.notify(message)

    def restore_previous_mix(self):
        try:
            _name, mix = import_mix(previous_mix_file(self.dev))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Restore previous mix",
                                f"Could not restore the previous mix:\n{reason(e)}")
            return
        self.replace_mix(mix, "Restored the previous mix")

    def _add_restore_action(self, menu):
        menu.addSeparator()
        a = menu.addAction("Restore previous mix", self.restore_previous_mix)
        a.setToolTip("The mix as it was before the last recall, import, reset, paste or clear")
        a.setEnabled(previous_mix_file(self.dev).exists())
        menu.setToolTipsVisible(True)

    def slot_menu(self, i):
        menu = QMenu(self)
        menu.addAction("Store current mix here…", lambda: self.store_slot(i))
        if self.presets.slots[i]:
            menu.addAction("Recall", lambda: self.recall_slot(i))
            menu.addAction("Rename…", lambda: self._rename_slot(i))
            menu.addSeparator()
            menu.addAction("Clear…", lambda: self.clear_slot(i))
        self._add_restore_action(menu)
        menu.exec(self.slot_btns[i].mapToGlobal(self.slot_btns[i].rect().bottomLeft()))
        menu.deleteLater()

    def _rename_slot(self, i):
        name, ok = QInputDialog.getText(self, "Rename preset", f"Name for slot {i + 1}:",
                                        text=self.presets.name(i))
        if ok and name.strip():
            self.presets.rename(i, name.strip())
            self.refresh_slots()

    def clear_slot(self, i):
        if ask(self, "Clear preset", f"Clear “{self.presets.name(i)}” from slot {i + 1}? "
               "This can't be undone.", "Clear"):
            self.presets.clear(i)
            if self.st.get("active_slot") == i:
                self.st["active_slot"] = None       # nothing is recalled from an empty slot
                self.schedule_save()
            self.refresh_slots()

    def export_mix(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export mix", "mix.ofmix.json",
                                              "Openface Mixer mix (*.json)")
        if not path:
            return
        slot = self.st.get("active_slot")
        try:
            export_mix(path, self.presets.name(slot) if slot is not None else "Mix",
                       extract_mix(self.st))
        except OSError as e:
            QMessageBox.warning(self, "Export mix", f"Could not export to {path}:\n{reason(e)}")

    def import_mix(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import mix", "", "Openface Mixer mix (*.json)")
        if not path:
            return
        try:
            name, mix = import_mix(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import mix", f"Could not import {path}:\n{reason(e)}")
            return
        self.replace_mix(mix, f"Imported “{name}”")

    def reset_mix(self):
        if not ask(self, "Reset mix", "Reset all routing to the default (playback n → output n, "
                   "no input monitoring, no groups)?", "Reset mix"):
            return
        self.replace_mix(extract_mix(default_state(self.dev.phones_pair(1))),
                         "Mix reset to default")

    # ------------------------------------------------------------------ engine sync and saving
    def push_matrix(self):
        self.refresh_cue()
        gains, out_gains = control_room.apply(self.st, compute_matrix(self.st),
                                              compute_out_gains(self.st), cue=self.cue)
        self.engine.write_matrix(gains, out_gains)
        self.engine.write_device(device_block(self.st, self.dev))
        if self.dev.family == "hdspe":
            self._limited = hdspe.limited(gains, out_gains, N_SRC)
            self.show_limited()
        self.matrix.update()
        self.control.refresh()
        self.refresh_groups()
        self.update_arc_leds()
        self.schedule_save()

    def show_limited(self):
        """An HDSPe card has no output fader: its output level is part of every send, and a
        send stops at +6 dB. Mark the faders that ask for more than that."""
        if self.dev.family != "hdspe":
            return
        outs = {o for o, _src in self._limited}
        sel = self.st["selected"]
        for s in self.strips.values():
            if s.kind == "out":
                on = not outs.isdisjoint(s.channels)
                s.set_limited("Some sends to this output are held at +6 dB, the most the card "
                              "can do. Lower this level to get them back." if on else "")
            else:
                base = 0 if s.kind == "in" else N_IN
                on = any((o, base + c) in self._limited
                         for o in (2 * sel, 2 * sel + 1) for c in s.channels)
                s.set_limited("Held at +6 dB: this send and the output level add up to more "
                              "than the card can do." if on else "")

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
        if action in ("phones", "phones2"):         # a lit key again: back to Main Out
            cr["encoder"] = "main" if arc.key_lit(self.st, action, None) else action
            self.update_arc_leds()
            self.schedule_save()
            return
        control_room.toggle(self.st, action)
        self.push_matrix()

    def arc_encoder(self, clicks):
        """The encoder turns the Main Out (or Phones) master."""
        key = strip_key("out", control_room.encoder_pair(self.st))
        old = get_strip_db(self.st, key)
        _pair, db = control_room.step_volume(self.st, clicks)
        self.out_moved(key, old, db)

    def recall_main(self):
        """TotalMix's Recall: Main Out goes to the stored listening level."""
        cr = control_room.control_room(self.st)
        key = strip_key("out", cr["main"])
        old = get_strip_db(self.st, key)
        set_strip_db(self.st, key, cr["recall_db"])
        self.out_moved(key, old, cr["recall_db"])

    def out_moved(self, key, old, db):
        """An output master was set without its fader: show it, and let its fader group follow
        as when the fader is moved with the mouse, also where a strip is hidden."""
        for k, val in {key: db, **group_follow(self.st, key, old, db)}.items():
            if k in self.strips:
                self.strips[k].set_fader(val)
        self.push_matrix()

    def arc_status(self, status):
        self.control.set_arc_status(status)
        if status == "connected":
            self.update_arc_leds()

    def update_arc_leds(self):
        slot = self.st.get("active_slot")
        if slot is not None and not self.presets.slots[slot]:
            slot = None                 # an empty slot's key stays dark, whatever the state says
        self.arc.set_leds([arc.key_lit(self.st, a, slot) for a in arc.DEFAULT_KEYS])

    def schedule_save(self):
        self._save_timer.start()

    def save(self):
        save_state(self.st, self.dev)

    def ensure_engine(self):
        if self.engine.outdated():
            self.restart_engine()   # an older engine from before an upgrade is still running
        elif self.engine.status() is None:
            self.start_engine()

    def start_engine(self):
        """Starts the engine and returns at once. poll_engine then looks for it every 50 ms, for
        1.5 s at most, without calling it stopped meanwhile, and sends it the mix as it is by
        then."""
        self.save()   # so a freshly started engine loads our matrix
        ok, msg = self.engine.start()
        # shown in the Status section instead of an alert at launch
        self.engine_error = None if ok else msg
        self._engine_due = time.monotonic() + 1.5 if ok else 0.0
        self.pace_timers()

    def restart_engine(self):
        self.engine_error = None
        self.engine.stop()
        self._engine_up = False     # the next one to answer is new, and is sent the mix
        self._engine_due = time.monotonic() + 2.0
        self._start_timer.start()   # once the old engine has had time to go

    # ------------------------------------------------------------------ network remote (OSC)
    def apply_remote_settings(self):
        r = remote_settings(self.st)
        self.remote.levels_on = r["levels"]
        self.remote.set_target(r["host"], r["out_port"])
        port = self.remote.sock.localPort() if self.remote.sock else None
        if not r["enabled"]:
            self.remote.stop()
        elif port != r["port"]:
            self.remote.start(r["port"])
        self.pace_timers()
        self.schedule_save()

    def remote_snapshot(self):
        filled = {i for i in range(N_SLOTS) if self.presets.slots[i]}
        return osc_remote.snapshot(self.st, self.dev, self.mode, filled, self._hw_active)

    def remote_message(self, address, args):
        """A controller on the network changed something (osc_remote.apply says what)."""
        res = osc_remote.apply(self.st, self.dev, self.mode, address, args)
        if res.kind == "changed":
            for kind in ("in", "play"):
                self.solo_as_shown(kind)        # a remote can't solo what is hidden here
            if res.selected is not None and res.selected != self.submix.currentData():
                self.select_out(res.selected, by_pair=True)
            self.sync_strips()
            self.push_matrix()
        elif res.kind == "stereo":
            self.toggle_stereo(*res.value)
        elif res.kind == "recall":
            if self.presets.slots[res.value]:
                self.recall_slot(res.value)
        elif res.kind == "store":
            i = res.value
            self.presets.store(i, self.presets.name(i) or f"Mix {i + 1}", extract_mix(self.st))
            self.st["active_slot"] = i
            self.refresh_slots()
            self.schedule_save()

    def sync_strips(self):
        """Show the model's faders, mutes and solos on the strips (after a remote change)."""
        for s in self.strips.values():
            s.set_fader(get_strip_db(self.st, s.key))
            if s.kind == "out":
                mute = self.st["out"][s.pair]["mute"]
            else:
                mute = self.st["mute"][s.kind][s.channels[0]]
                s.solo.blockSignals(True)
                s.solo.setChecked(self.st["solo"][s.kind][s.channels[0]])
                s.solo.blockSignals(False)
            s.mute.blockSignals(True)
            s.mute.setChecked(mute)
            s.mute.blockSignals(False)
            s.show_flags()

    # ------------------------------------------------------------------ polling
    def poll_engine(self):
        s = self.engine.status()
        if self._engine_due:
            if s is None and time.monotonic() < self._engine_due:
                return                  # still starting
            self._engine_due = 0.0
            self.pace_timers()
        if s is not None and not self._engine_up:
            self.push_matrix()          # a (re)started engine: the mix as it is now
        self._engine_up = s is not None
        self.settings.update_hw_mixer(s, self.engine_error)
        sep = "<span style='color:#2a4552'>&nbsp;&nbsp;·&nbsp;&nbsp;</span>"
        if s is None:
            txt = light(RED) + "&nbsp; Engine stopped"
            self.status.setToolTip("The engine is not running · click for details")
        else:
            rate = f"{s['rate'] / 1000:g} kHz" if s["rate"] else "— kHz"
            limit = f"/{self.dev.max_routes}" if self.dev.max_routes else ""
            routes = (f"{s['hw_nodes']}{limit} routes" if s["hw"] in ("active", "no-nodes")
                      else "no reply" if s["hw"] == "no-reply" else "offline")
            txt = (light(hw_color(s["hw"])) + "&nbsp; HW DSP" +
                   ("" if self._status_short else f"{sep}{rate}{sep}{routes}"))
            self.status.setToolTip(f"Hardware DSP: {rate}, {routes} · click for details")
            if s["rate"] and speed_mode(s["rate"]) != self.mode:
                self.rate, self.mode = s["rate"], speed_mode(s["rate"])
                self.rebuild_all()
        self.status.setText(txt)
        active = s is not None and s["hw"] == "active"
        if active and not self._hw_active:
            self.engine.enable()        # the interface is there: restore its mix at every login
        self._hw_active = active
        if self.dev.family == "latter":
            if active and self.dev.bus == "FireWire":
                self.follow_unit(s["dev_status"])
            # "no-reply" is connected too: the clock shows as unknown, not as not connected
            connected = active or (s is not None and s["hw"] == "no-reply")
            self.settings.update_latter_status(s["dev_status"] if connected else None)
        elif self.dev.family == "former":
            if active:
                self.follow_unit(s["dev_status2"])
            self.settings.update_former_status((s["dev_status"], s["dev_status2"])
                                               if active else None)

    def follow_unit(self, status):
        """A FireWire Fireface's configuration is write only and holds the clock source. Until
        the user changes a setting here, show what the unit reports and write nothing (the
        protocol module's config_ready), so connecting never changes the unit's clock."""
        hw = self.st["hw"]
        if self.dev.proto.config_ready(hw):
            return
        before = dict(hw)
        self.dev.proto.settings_from_status(hw, status)
        if hw != before:
            self.settings.refresh_fw_settings()
            self.push_matrix()

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
        self.remote.add_peaks("in", self.channels("in"), src)
        self.remote.add_peaks("play", self.channels("play"), src[N_IN:])
        self.remote.add_peaks("out", self.channels("out"), out)

    def closeEvent(self, e):
        self.save()
        size = (self.normalGeometry() if self.isMaximized() else self.geometry()).size()
        save_window(size.width(), size.height(), self.sidebar.is_open())
        if self.background and self.switch_to is None and not self._quitting:
            e.ignore()                  # keep mixing and serving the remote; reopen to show
            self.hide()
            return
        super().closeEvent(e)


def _single_instance(app, dev):
    """Make this the only running Openface Mixer window for dev (each device has its own engine,
    mix and window, so one per device may run). If one is already running, ask it to show its
    window and return None; otherwise return the server that answers later starts."""
    name = f"{APP_ID}-{os.getuid()}-{dev.key}"
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(300):
        probe.write(b"show\n")
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return None
    QLocalServer.removeServer(name)            # left behind by a crashed instance
    server = QLocalServer(app)
    if not server.listen(name):
        print(f"openface-mixer: single-instance socket {name}: {server.errorString()}",
              file=sys.stderr)
    return server


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "openface-mixer.svg")))
    args = app.arguments()[1:]
    if "--device" in args and args.index("--device") + 1 < len(args):
        dev = DEVICES.get(args[args.index("--device") + 1])
        if dev is None:
            sys.exit(f"--device: one of {', '.join(DEVICES)}")
    else:
        dev = detect(load_device_choice())
    background = "--background" in args
    app.setQuitOnLastWindowClosed(not background)
    current = {}

    def show_window():
        w = current.get("window")
        if w is not None:
            w.show()
            w.raise_()
            w.activateWindow()

    def claim(dev):
        """This process becomes dev's window, or hands over to the one already running."""
        server = _single_instance(app, dev)
        if server is None:
            sys.exit(0)
        server.newConnection.connect(lambda: (server.nextPendingConnection().deleteLater(),
                                              show_window()))
        return server

    server = claim(dev)
    geometry = None
    while True:
        apply_theme(app, load_ui_scale())   # the window is laid out at the saved UI scale
        save_device_choice(dev)
        w = MainWindow(dev, background)
        current["window"] = w
        if geometry is not None:
            w.setGeometry(geometry)
        if not background:
            w.show()
        code = app.exec()
        if w.switch_to is None:
            sys.exit(code)
        background = False               # the user switched devices in the window: show the next
        app.setQuitOnLastWindowClosed(True)
        dev = DEVICES[w.switch_to]
        geometry = w.reopen_geometry
        w.arc.disconnect_port()      # so the next window can open the ARC USB
        w.remote.stop()              # and the next one can bind the remote's port
        w.deleteLater()
        if w.dev is not dev:         # the other device may already have a window: show that
            server.close()
            server.deleteLater()
            server = claim(dev)
