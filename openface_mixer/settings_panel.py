"""Right-hand panel: device choice, the device's clock/port settings, card profile and hardware
mixer status."""
import html
import threading

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from . import hdspe, updater
from .config import UI_SCALES, load_update_prefs, save_ui
from .devices import DEVICES, group_title
from .hardware import Hardware, set_card_profile
from .theme import SANS, Section, caption_html, font, key_label, px

UDEV_RULES = {"digiface": "70-rme-digiface.rules", "ff802": "70-rme-fireface.rules",
              "ff800": "70-rme-fireface.rules", "ff400": "70-rme-fireface.rules"}


def udev_rule_hint(dev):
    if dev.key not in UDEV_RULES:     # plain ALSA devices (PCIe cards, USB MIDI)
        return ("Openface Mixer opens the device's ALSA devices in /dev/snd. "
                "Make sure your user may use audio devices (the <tt>audio</tt> group, or a "
                "local login session).")
    rule = UDEV_RULES[dev.key]
    if updater.packaged():
        return (f"Openface Mixer needs write access to the {dev.name}'s {dev.bus} device. The "
                "package installed the udev rule for it. Reconnect the interface, or restart "
                "the computer, so the rule applies.")
    return (f"Openface Mixer needs write access to the {dev.name}'s {dev.bus} device. Install "
            f"the udev rule once:<br><tt>sudo install -m644 packaging/{rule} "
            "/etc/udev/rules.d/</tt><br>"
            "<tt>sudo udevadm control --reload &amp;&amp; sudo udevadm trigger</tt>")


CYAN, AMBER, RED, OFF = "#2ad4ff", "#ffb020", "#ff2a4f", "#6f8a94"


def light(color, text, on=True):
    """A status value: a light, filled when things are fine or busy and an empty ring when
    not, then the words (the state never depends on telling the colours apart)."""
    return f"<span style='color:{color}'>{'●' if on else '○'}</span>&nbsp;{text}"


def hw_state(dev, state):
    """(short value for the Hardware mixer row, what to do about it or "")."""
    short = dev.name.replace("RME ", "")
    hint = dev.missing_hint() if state == "no-device" else ""
    return {
        "starting": (light(AMBER, "Connecting…"), ""),
        "no-device": (light(RED, "Not found", False), f"{short} not found on {dev.bus}."
                      + (hint and f"<br>{hint}")),
        "no-access": (light(RED, "No permission", False),
                      f"No permission to open the {short}.<br>" + udev_rule_hint(dev)),
        "busy": (light(RED, "In use", False),
                 f"The {short} driver refuses mixer changes while playback and recording are "
                 "open in two different programs. Let one program (PipeWire or JACK) use both."
                 if dev.bus == "PCIe" else
                 f"The {short} mixer interface is in use by another program."),
        "active": (light(CYAN, "Active"), ""),
        "no-nodes": (light(AMBER, "Active, routes full"),
                     f"This mix needs more than {dev.max_routes} routes; some sends are "
                     "missing."),
        "error": (light(RED, f"{dev.bus} error", False),
                  f"{dev.bus} error talking to the {short}; retrying."),
        "wrong-driver": (light(AMBER, "Check driver", False),
                         f"This {short} looks like a card from 2022 on. Those need the snd-hdspe "
                         "driver, and this one runs on snd-hdspm, so the mixer is left alone. "
                         "Install snd-hdspe and blacklist snd-hdspm (see the README), or choose "
                         "Use anyway if the card works for you as it is."),
        "no-reply": (light(AMBER, "Connected, no reply from the DSP"),
                     f"The {short} is connected but has not answered, so changes may not "
                     "reach it."),
    }.get(state, (state, ""))


def read_failure(hw, short):
    """(short text, tooltip) for the Sample rate row when the card's settings can't be shown
    (hw: the Hardware after a read() that gave nothing usable)."""
    if hw.card is None:
        return f"{short} not found", ""
    if hw.error == "missing":
        return "amixer is not installed", "Install the alsa-utils package."
    if hw.error == "timeout":
        return "amixer did not answer", ""
    return "Can't read settings", hw.error or "The card's ALSA controls are not the expected ones."


def write_failure(why, dev, tool="amixer"):
    """The toast for a setting tool could not change (why: hardware.run()'s answer)."""
    if why == "missing":
        why = f"{tool} is not installed"
    elif why == "timeout":
        why = f"{tool} did not answer"
    elif dev.family == "hdspe" and "busy" in why:
        why = "the driver is busy while playback and recording are open in two programs"
    else:   # "amixer: Control hw:1 element write error: Device or resource busy": the reason
        why = why.rsplit(": ", 1)[-1]
    return f"Not changed: {why}"


def ask(parent, title, text, action):
    """A question with a button named for what it does and Cancel (the safe default), instead
    of Yes / No."""
    box = QMessageBox(QMessageBox.Question, title, text, parent=parent)
    go = box.addButton(action, QMessageBox.AcceptRole)
    box.setDefaultButton(box.addButton(QMessageBox.Cancel))
    box.exec()
    return box.clickedButton() is go


class Led(QLabel):
    """Lock state: a filled light when locked or in sync, an empty ring when not, so it reads
    without telling the colours apart."""
    COLORS = {"No Lock": "#6f8a94", "Lock": "#ffb020", "Sync": "#2ad4ff"}

    def set_state(self, s):
        c = self.COLORS.get(s, "#6f8a94")
        dot = "●" if s in ("Lock", "Sync") else "○"
        self.setText(f"<span style='color:{c}'>{dot}</span> {s or '—'}")


class SyncLight(QLabel):
    """A small light beside a clock source combo: green while the interface actually runs
    from the chosen source, dark while it does not (or the state is unknown). The tooltip
    names the current source; item is the combo's entry for it where the two differ."""
    ON, OFF = "#56e39f", "#6f8a94"

    def __init__(self, combo, parent=None):
        super().__init__(parent)
        self.combo = combo
        self.current = self.item = None
        self.setFont(font(SANS, 9))
        combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self.refresh()

    def set_current(self, source, item=None):
        self.current, self.item = source, item or source
        self.refresh()

    def refresh(self):
        on = self.current not in (None, "—") and self.item == self.combo.currentText()
        # filled and green while in use, an empty ring otherwise (shape, not only colour)
        self.setText(f"<span style='color:{self.ON if on else self.OFF}'>{'●' if on else '○'}"
                     "</span>")
        self.setToolTip(("Running from this source" if on else
                         f"Running from: {self.current}") if self.current else
                        "Current source unknown")


class Sidebar(QFrame):
    """The settings panel docked to the window's right edge: one dark column with the sections
    stacked and divided by hairlines, slid in and out by the Settings button."""
    closed = Signal()
    WIDTH = 268

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.full_width = px(self.WIDTH) + px(8)     # room for the scroll bar
        self.setFixedWidth(self.full_width)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(px(14), px(12), px(8), px(10))
        title = QLabel(caption_html("Settings", QColor("white")))
        head.addWidget(title, 1)
        close = QToolButton()
        close.setText("×")
        close.setObjectName("sidebarclose")
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("Hide settings (Ctrl+,)")
        close.clicked.connect(self.closed.emit)
        head.addWidget(close)
        lay.addLayout(head)
        # the panel scrolls instead of squashing its sections when the window is short
        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(scroll, 1)
        self._anim = QPropertyAnimation(self, b"maximumWidth", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._open = True

    def is_open(self):
        return self._open

    def set_open(self, on, animate=True):
        if on == self._open:
            return
        self._open = on
        self.setMinimumWidth(0)
        self._anim.stop()
        if not animate:
            self.setMaximumWidth(self.full_width if on else 0)
            return
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(self.full_width if on else 0)
        self._anim.start()


class SettingsPanel(QWidget):
    device_selected = Signal(str)    # device key
    hw_changed = Signal()            # a device setting (Fireface) changed in st["hw"]
    notice = Signal(str)             # a short message for the window to show
    card_chosen = Signal()           # another card of the model (HDSPe): the engine restarts
    shown_changed = Signal()         # a SHOW option changed st["hidden"]
    names_changed = Signal(bool)     # custom channel names on / off (st["custom_names"])
    ui_scale_changed = Signal(float) # chosen scale; the window reopens laid out at it
    restart_requested = Signal()
    update_waiting = Signal(bool)    # a check found an update (the gear button shows a dot)
    _update_result = Signal(str, object)  # (step, result or UpdateError), from a worker thread
    UPDATE_PROMPT = "Check GitHub for a newer Openface Mixer."
    STARTUP_CHECK_MS = 5000          # the quiet check, once the window has settled

    def __init__(self, hw, dev, st, ui_scale=1.0, parent=None):
        super().__init__(parent)
        self.hw = hw
        self.dev = dev
        self.st = st
        self.setFixedWidth(px(268))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        box = Section("DEVICE")
        bl = QVBoxLayout(box)
        self.device = QComboBox()
        for d in DEVICES.values():
            self.device.addItem(d.name, d.key)
        self.device.setCurrentIndex(self.device.findData(dev.key))
        self.device.activated.connect(lambda i: self.device_selected.emit(self.device.itemData(i)))
        bl.addWidget(self.device)
        self.card_combo = QComboBox()    # shown while there are several cards of an HDSPe model
        self.card_combo.setToolTip("Which of these cards to mix")
        self.card_combo.activated.connect(self._card_chosen)
        self.card_combo.hide()
        bl.addWidget(self.card_combo)
        lay.addWidget(box)
        self._card_name = None
        self._updating = False

        # one value per row, as the other sections, so the state can be read at a glance; what
        # to do about a problem goes in the note below the rows
        eng = Section("STATUS")
        el = QGridLayout(eng)
        if dev.key == "digiface":
            self._build_card_profile(el)
        self.hw_label, self.routes_label, self.eng_label, self.sink_label = (
            self._status_row(el, key) for key in ("Hardware mixer", "Routes", "Engine",
                                                  "Playback sink"))
        self.status_note = QLabel()
        self.status_note.setObjectName("note")
        self.status_note.setWordWrap(True)
        self.status_note.setTextInteractionFlags(Qt.TextSelectableByMouse)   # commands to copy
        self.status_note.hide()
        el.addWidget(self.status_note, el.rowCount(), 0, 1, 2)
        self.use_btn = QPushButton("Use anyway")      # for the HDSPe driver warning
        self.use_btn.setToolTip("Let Openface Mixer drive this card on snd-hdspm from now on")
        self.use_btn.clicked.connect(lambda: self._set_hw("use_anyway", True))
        self.use_btn.hide()
        el.addWidget(self.use_btn, el.rowCount(), 0, 1, 2)
        self.eng_btn = QPushButton("Restart engine")
        el.addWidget(self.eng_btn, el.rowCount(), 0, 1, 2)
        el.setColumnStretch(1, 1)
        lay.addWidget(eng)

        if dev.family == "latter":
            self._build_latter(lay)
        elif dev.family == "former":
            self._build_former(lay)
        elif dev.family == "hdspe":
            self._build_hdspe(lay)
        else:
            self._build_digiface(lay)
        self._control_room_at = lay.count()   # add_control_room() slots its section here

        self._build_ui_scale(lay, ui_scale)
        self._build_mix(lay)
        self._build_updates(lay)

        eng.setToolTip(
            f"All mixing happens in the {dev.name}'s own DSP, like TotalMix: input monitoring "
            "has no added latency and the mix keeps running with the computer idle or the "
            f"engine stopped.\n\nPlayback {dev.sink_channels} = the Openface Mixer Playback "
            "sink. Other playback channels: send apps to the interface's own output channels "
            "(Pro Audio profile) with qpwgraph/Helvum.")
        lay.addStretch()

    def add_control_room(self, section):
        """The main window's Control Room section, between the device sections and Display."""
        self.layout().insertWidget(self._control_room_at, section)

    def add_before_updates(self, section):
        """A section of the main window's (the network remote), just above Updates."""
        self.layout().insertWidget(self.layout().indexOf(self._updates_box), section)

    def _build_ui_scale(self, lay, scale):
        self._scale_at_start = scale
        disp = Section("DISPLAY")
        dl = QGridLayout(disp)
        dl.addWidget(key_label("UI scale"), 0, 0)
        self.scale_combo = QComboBox()
        for s in UI_SCALES:
            self.scale_combo.addItem(f"{s * 100:g}%", s)
        if all(abs(scale - s) > 1e-6 for s in UI_SCALES):
            self.scale_combo.addItem(f"{scale * 100:g}%", scale)
        self.scale_combo.setCurrentIndex(self._scale_index(scale))
        self.scale_combo.activated.connect(self._scale_activated)
        dl.addWidget(self.scale_combo, 0, 1)
        self.scale_combo.setToolTip("Lays the whole window out larger or smaller, on top of the "
                                    "desktop's display scale. Applies right away.")
        self.names_box = QCheckBox("Custom channel names")
        self.names_box.setChecked(self.st["custom_names"])
        self.names_box.setToolTip("Show the names given with right-click > Rename, or the "
                                  "standard channel names.")
        self.names_box.toggled.connect(self.names_changed.emit)
        dl.addWidget(self.names_box, 1, 0, 1, 2)
        # SHOW: hide whole channel groups (an unused ADAT port, ...) from the mixer and matrix
        dl.addWidget(key_label("Show"), 2, 0, 1, 2)
        show = QGridLayout()
        show.setHorizontalSpacing(px(8))
        show.setVerticalSpacing(px(3))
        for i, g in enumerate(self.dev.channel_groups()):
            cb = QCheckBox(group_title(g))
            cb.setChecked(g not in self.st["hidden"])
            cb.toggled.connect(lambda on, grp=g: self._show_toggled(grp, on))
            show.addWidget(cb, i // 3, i % 3)
        dl.addLayout(show, 3, 0, 1, 2)
        lay.addWidget(disp)

    def _build_mix(self, lay):
        """Export / import a mix as a file, or start over (the buttons are wired by the main
        window)."""
        box = Section("MIX")
        ml = QVBoxLayout(box)
        self.export_btn = QPushButton("Export mix…")
        self.export_btn.setToolTip("Save the routing, levels and groups to a file")
        self.import_btn = QPushButton("Import mix…")
        self.reset_btn = QPushButton("Reset mix to default…")
        self.reset_btn.setToolTip("Playback n → output n, no input monitoring, no groups")
        for b in (self.export_btn, self.import_btn, self.reset_btn):
            ml.addWidget(b)
        lay.addWidget(box)

    def _show_toggled(self, group, on):
        hidden = [g for g in self.st["hidden"] if g != group]
        self.st["hidden"] = hidden if on else hidden + [group]
        self.shown_changed.emit()

    def _scale_index(self, scale):
        for i in range(self.scale_combo.count()):
            if abs(float(self.scale_combo.itemData(i)) - scale) < 1e-6:
                return i
        return 0

    def _scale_activated(self, i):
        scale = float(self.scale_combo.itemData(i))
        if abs(scale - self._scale_at_start) > 1e-6:
            self.ui_scale_changed.emit(scale)

    def _build_updates(self, lay):
        box = Section("UPDATES")
        ul = QGridLayout(box)
        self._updates_box = box
        self._source = updater.install_source()
        self._pending = None, []     # (release or None, its changes) the last check found
        self.version_label = QLabel(updater.version_text(self._source))
        self.version_label.setObjectName("head")
        self.version_label.setTextInteractionFlags(Qt.TextSelectableByMouse)  # for a bug report
        ul.addWidget(self.version_label, 0, 0, 1, 2)
        self.update_label = QLabel(self.UPDATE_PROMPT)
        self.update_label.setWordWrap(True)
        ul.addWidget(self.update_label, 1, 0, 1, 2)
        at_start, latest = load_update_prefs()
        channel_key = key_label("Update to")
        ul.addWidget(channel_key, 2, 0)
        self.channel = QComboBox()
        self.channel.addItem("Releases", "releases")
        self.channel.addItem("Latest changes", "latest")
        self.channel.setCurrentIndex(int(latest))
        self.channel.setToolTip("Releases are versions that have been tried. Latest changes "
                                "brings every change as soon as it is made, for testers.")
        self.channel.activated.connect(self._channel_chosen)
        ul.addWidget(self.channel, 2, 1)
        self.auto_check = QCheckBox("Check when the app starts")
        self.auto_check.setChecked(at_start)
        self.auto_check.toggled.connect(lambda on: save_ui(update_check=on))
        ul.addWidget(self.auto_check, 3, 0, 1, 2)
        self.update_btn = QPushButton("Check for updates")
        self.update_btn.clicked.connect(self._update_clicked)
        ul.addWidget(self.update_btn, 4, 0, 1, 2)
        ul.setColumnStretch(1, 1)
        lay.addWidget(box)
        self._update_result.connect(self._update_finished)
        if self._source is None:
            self.update_label.setText(updater.manual_hint())
            for w in (channel_key, self.channel, self.auto_check, self.update_btn):
                w.setVisible(False)
        else:
            QTimer(self, singleShot=True, interval=self.STARTUP_CHECK_MS,
                   timeout=self._startup_check).start()

    def _startup_check(self):
        """Look for an update once, saying nothing unless there is one."""
        if self.auto_check.isChecked() and self.update_btn.isEnabled() and not self._pending[1]:
            self._run_update_step("quiet")

    def _channel_chosen(self, i):
        save_ui(update_channel=self.channel.itemData(i))
        self._show_pending(None, [], quiet=True)
        self._run_update_step("check")      # what this channel has

    def _update_clicked(self):
        release, changes = self._pending
        what = f"version {release}" if release else f"{len(changes)} update(s)"
        if not changes:
            self._run_update_step("check")
        elif ask(self, "Update Openface Mixer",
                 f"Install {what} and restart? Your mixes are backed up first, and the engines "
                 "restart with the new version.", "Update and restart"):
            self._run_update_step("update")

    def _run_update_step(self, step):
        """Check ("quiet": the one at startup, which keeps a failure to itself) or update, on
        a thread; _update_finished gets the result."""
        src, installed = self._source
        releases = self.channel.currentData() != "latest"
        job = ((lambda: updater.update(src, releases)) if step == "update" else
               (lambda: updater.check(src, installed, releases, step == "quiet")))
        self.update_btn.setEnabled(False)
        self.channel.setEnabled(False)
        self.update_label.setText("Updating…" if step == "update" else "Checking…")

        def run():
            try:
                result = job()
            except Exception as e:  # noqa: BLE001 - shown to the user
                result = e if isinstance(e, updater.UpdateError) else updater.UpdateError(str(e))
            try:
                self._update_result.emit(step, result)
            except RuntimeError:    # the panel went away (device switch) while this ran
                pass
        threading.Thread(target=run, daemon=True).start()

    def _update_finished(self, step, result):
        self.update_btn.setEnabled(True)
        self.channel.setEnabled(True)
        if isinstance(result, updater.UpdateError):
            if step == "quiet":
                self.update_label.setText(self.UPDATE_PROMPT)
                return
            self.update_label.setText(str(result).splitlines()[0])
            if step == "update":
                QMessageBox.warning(self, "Update Openface Mixer", str(result))
            return
        if step == "update":
            self.restart_requested.emit()
            return
        self.version_label.setText(updater.version_text(self._source))  # a new tag may name it
        self._show_pending(*result, quiet=step == "quiet")

    def _show_pending(self, release, changes, quiet):
        """What a check found: the update's changes, or nothing (said only when asked)."""
        self._pending = release, changes
        if changes:
            head = (f"Version {release} is available:" if release else
                    f"{len(changes)} update(s) on GitHub:")
            latest = "<br>".join(f"• {html.escape(s)}" for s in changes[:5])
            more = f"<br>…and {len(changes) - 5} more" if len(changes) > 5 else ""
            self.update_label.setText(f"{head}<br>{latest}{more}")
        else:
            self.update_label.setText(self.UPDATE_PROMPT if quiet else
                                      "Openface Mixer is up to date.")
        self.update_btn.setText("Update and restart…" if changes else "Check for updates")
        self.update_waiting.emit(bool(changes))

    # ------------------------------------------------------------------ Digiface USB
    def _build_digiface(self, lay):
        clock = Section("CLOCK")
        cl = QGridLayout(clock)
        cl.addWidget(key_label("Clock source"), 0, 0)
        self.sync_src = QComboBox()
        self.sync_src.activated.connect(lambda i: self._write("Sync Source", i))
        cl.addWidget(self.sync_src, 0, 1)
        self.cur_src = SyncLight(self.sync_src)    # lit while the device runs from that source
        cl.addWidget(self.cur_src, 0, 2)
        cl.addWidget(key_label("Sample rate"), 1, 0)
        self.rate = QLabel("—")
        cl.addWidget(self.rate, 1, 1)
        cl.setColumnStretch(1, 1)
        lay.addWidget(clock)

        ports = Section("OPTICAL PORTS")
        ports.setObjectName("ports")      # 10 px grid: four rows in little height
        pl = QGridLayout(ports)
        for c, h in enumerate(["", "Input", "Status", "Rate", "Output"]):
            lbl = QLabel(h)
            lbl.setObjectName("hint")
            pl.addWidget(lbl, 0, c)
        self.port_widgets = []
        for i in range(4):
            fmt, led, rate, out = QLabel("—"), Led(), QLabel("—"), QComboBox()
            out.addItems(["ADAT", "S/PDIF"])
            out.setToolTip(f"Output {i + 1} format")
            out.activated.connect(lambda v, n=i + 1: self._write(f"Output {n} Format", v))
            for c, w in enumerate([key_label(f"{i + 1}"), fmt, led, rate, out]):
                pl.addWidget(w, i + 1, c)
            self.port_widgets.append((fmt, led, rate, out))
        pl.setColumnStretch(4, 1)
        lay.addWidget(ports)

    @staticmethod
    def _status_row(grid, key):
        r = grid.rowCount()
        grid.addWidget(key_label(key), r, 0, Qt.AlignTop)
        value = QLabel("—")
        value.setWordWrap(True)
        grid.addWidget(value, r, 1)
        return value

    def show_status_note(self, *notes):
        """The STATUS section's note: what is wrong and how to fix it (empty: hidden)."""
        text = "<br><br>".join(n for n in notes if n)
        self.status_note.setText(text)
        self.status_note.setVisible(bool(text))

    def _build_card_profile(self, grid):
        """The Digiface's PipeWire profile, at the top of the STATUS section."""
        self.profile = self._status_row(grid, "Card profile")
        self.recording = self._status_row(grid, "Recording")
        self.pro_btn = QPushButton("Record all inputs (Pro Audio)…")
        self.pro_btn.clicked.connect(self._enable_pro)
        grid.addWidget(self.pro_btn, grid.rowCount(), 0, 1, 2)

    # ------------------------------------------------------------------ Fireface 802, UCX II
    def _build_latter(self, lay):
        """TotalMix's Settings dialog for the 802 and UCX II: clock, sync inputs and
        AES/S/PDIF/optical options, from st["hw"]. Over FireWire the clock is the unit's own
        until a setting is changed here; the options can't be read from the unit."""
        hw, proto = self.st["hw"], self.dev.proto
        self.ff_clock = QComboBox()
        for key, label, _ in proto.CLOCK_SOURCES:
            self.ff_clock.addItem(label, key)
        self.ff_clock.activated.connect(
            lambda i: self._set_hw("clock", self.ff_clock.itemData(i)))
        # over USB the DSP reports its clock setting and rate, not the inputs' lock state
        self._build_fw_clock(lay, self.ff_clock,
                             () if self.dev.bus == "USB" else proto.STATUS_INPUTS)

        opts = Section("OPTIONS")
        ol = QGridLayout(opts)
        rows = proto.OPTION_ROWS
        for r, (label, key, items) in enumerate(rows):
            combo = QComboBox()
            for text, value in items:
                combo.addItem(text, value)
            combo.setCurrentIndex(max(0, combo.findData(hw[key])))
            combo.activated.connect(lambda i, c=combo, k=key: self._set_hw(k, c.itemData(i)))
            ol.addWidget(key_label(label), r, 0)
            ol.addWidget(combo, r, 1)
        single = QCheckBox("Word clock out: single speed")
        single.setChecked(hw["word_single"])
        single.toggled.connect(lambda on: self._set_hw("word_single", on))
        ol.addWidget(single, len(rows), 0, 1, 2)
        lay.addWidget(opts)
        self.refresh_fw_settings()

    def _build_fw_clock(self, lay, clock_combo, status_inputs, clock_label="Clock source"):
        """The CLOCK and INPUT STATUS boxes of a FireWire Fireface (and the RayDAT). Returns the
        CLOCK box's grid."""
        clock = Section("CLOCK")
        cl = QGridLayout(clock)
        cl.addWidget(key_label(clock_label), 0, 0)
        cl.addWidget(clock_combo, 0, 1)
        self.cur_src = SyncLight(clock_combo)      # lit while the device runs from that source
        cl.addWidget(self.cur_src, 0, 2)
        cl.addWidget(key_label("Sample rate"), 1, 0)
        self.rate = QLabel("—")
        cl.addWidget(self.rate, 1, 1)
        cl.setColumnStretch(1, 1)
        lay.addWidget(clock)

        sync = Section("INPUT STATUS")
        sl = QGridLayout(sync)
        self.sync_widgets = []
        self._sync_labels = {}
        for r, (label, *_unused) in enumerate(status_inputs):
            led, rate = Led(), QLabel("—")
            self._sync_labels[label] = key_label(label)
            sl.addWidget(self._sync_labels[label], r, 0)
            sl.addWidget(led, r, 1)
            sl.addWidget(rate, r, 2)
            led.set_state(None)
            self.sync_widgets.append((led, rate))
        if status_inputs:
            lay.addWidget(sync)
        return cl

    def _show_fw_status(self, st, why=None):
        """st: the device's decode_status() dict, or None while it is not connected (why:
        another reason it can't be shown, as read_failure())."""
        if st is None:
            text, tip = why or (f"{self.dev.name.replace('RME ', '')} not connected", "")
            self.cur_src.set_current(None)
            self.rate.setText(f"<span style='color:#ff2a4f'>{text}</span>")
            self.rate.setToolTip(tip)
            for led, rate in self.sync_widgets:
                led.set_state(None)
                rate.setText("—")
            return
        self.cur_src.set_current(st["source"])
        self.rate.setText(f"{st['rate'] / 1000:g} kHz" if st["rate"] else "—")
        self.rate.setToolTip("")
        for (led, rate), (_, state, r) in zip(self.sync_widgets, st["inputs"]):
            led.set_state(state)
            rate.setText(f"{r / 1000:g}k" if r else "—")

    def _set_hw(self, key, value):
        hw = self.st["hw"]
        hw[key] = value
        if "edited" in hw:     # FireWire: from now on the configuration is the user's
            hw["edited"] = True
        self.hw_changed.emit()

    def update_latter_status(self, word):
        """word: the 802's sync status register, or the USB engine's clock report
        (Engine.status()["dev_status"]), or None."""
        proto = self.dev.proto
        decode = proto.decode_usb_status if self.dev.bus == "USB" else proto.decode_status
        self._show_fw_status(None if word is None else decode(word))

    # ------------------------------------------------------------------ Fireface 800, 400
    def _build_former(self, lay):
        """TotalMix's Settings dialog for the 800 and 400: clock, sync inputs, levels and S/PDIF
        options. The clock and S/PDIF options are the unit's own until a setting is changed
        here."""
        proto = self.dev.proto
        self.ff800_combos = {}
        self._build_fw_clock(lay, self._ff800_combo("clock", [(label, key) for key, label, _ in
                                                               proto.CLOCK_SOURCES]),
                             proto.STATUS_INPUTS)

        levels = Section("LEVELS")
        ll = QGridLayout(levels)
        for r, (label, key, table) in enumerate(
                (("Line inputs", "in_level", proto.IN_LEVELS),
                 ("Line outputs", "out_level", proto.OUT_LEVELS),
                 ("Phones", "phones_level", getattr(proto, "PHONES_LEVELS", None)))):
            if table:
                ll.addWidget(key_label(label), r, 0)
                ll.addWidget(self._ff800_combo(key, [(l, k) for k, l, *_ in table]), r, 1)
        lay.addWidget(levels)

        opts = Section("OPTIONS")
        ol = QGridLayout(opts)
        rows = (("S/PDIF in", "spdif_in", (("Coaxial", "coax"), ("Optical", "optical"))),
                ("Optical out", "opt_out", (("ADAT", "adat"), ("S/PDIF", "spdif"))),
                ("S/PDIF out", "spdif_pro", (("Consumer", False), ("Professional", True))))
        for r, (label, key, items) in enumerate(rows):
            ol.addWidget(key_label(label), r, 0)
            ol.addWidget(self._ff800_combo(key, items), r, 1)
        r = len(rows)
        for key, label in (("emphasis", "S/PDIF out emphasis"),
                           ("non_audio", "S/PDIF out non-audio"),
                           ("word_single", "Word clock out: single speed")):
            box = QCheckBox(label)
            box.toggled.connect(lambda on, k=key: self._set_hw(k, on))
            ol.addWidget(box, r, 0, 1, 2)
            self.ff800_combos[key] = box
            r += 1
        lay.addWidget(opts)
        self.refresh_fw_settings()

    def _ff800_combo(self, key, items):
        combo = QComboBox()
        for text, value in items:
            combo.addItem(text, value)
        combo.activated.connect(lambda i: self._set_hw(key, combo.itemData(i)))
        self.ff800_combos[key] = combo
        return combo

    def refresh_fw_settings(self):
        """Show st["hw"] (after it was filled in from the unit's status). The clock stays empty
        while the unit runs from a source we don't know."""
        hw = self.st["hw"]
        if self.dev.family == "latter":
            self.ff_clock.setCurrentIndex(self.ff_clock.findData(hw["clock"]) if hw["clock"]
                                          else -1)
            return
        for key, w in self.ff800_combos.items():
            w.blockSignals(True)
            if isinstance(w, QCheckBox):
                w.setChecked(hw[key])
            elif key == "clock" and not hw[key]:
                w.setCurrentIndex(-1)
            else:
                w.setCurrentIndex(max(0, w.findData(hw[key])))
            w.blockSignals(False)

    def update_former_status(self, words):
        """words: the two status quadlets (Engine.status() dev_status, dev_status2), or None."""
        self._show_fw_status(None if words is None else self.dev.proto.decode_status(*words))

    # ------------------------------------------------------------------ HDSPe cards
    def _build_hdspe(self, lay):
        """TotalMix's Settings dialog for an HDSPe card, from the driver's ALSA controls: clock
        mode, preferred sync reference, internal rate, levels and S/PDIF/AES/word clock options,
        input status. The drivers and cards name the controls differently (hdspe.DRIVERS);
        options a card lacks are hidden."""
        model = self.dev.model
        combos = {}
        for role in hdspe.COMBOS:
            combo = QComboBox()
            combo.activated.connect(lambda i, r=role: self._write_role(r, i))
            combos[role] = combo
        self.rd_combos = combos
        clock = self._build_fw_clock(lay, combos["clock"], model.status_inputs, "Clock mode")
        clock.addWidget(key_label("Prefer"), 2, 0)
        clock.addWidget(combos["pref"], 2, 1)
        clock.addWidget(key_label("Internal rate"), 3, 0)
        clock.addWidget(combos["internal"], 3, 1)

        opts = Section("OPTIONS")
        ol = QGridLayout(opts)
        self.rd_combo_labels = {}
        for r, (role, label) in enumerate(hdspe.OPTION_COMBOS):
            self.rd_combo_labels[role] = key_label(label)
            ol.addWidget(self.rd_combo_labels[role], r, 0)
            ol.addWidget(combos[role], r, 1)
        r = len(hdspe.OPTION_COMBOS)
        self.rd_toggles = {}
        for name, label in hdspe.TOGGLES:
            box = QCheckBox(label)
            box.toggled.connect(lambda on, n=name: self._write(n, "on" if on else "off"))
            ol.addWidget(box, r, 0, 1, 2)
            self.rd_toggles[name] = box
            r += 1
        self.rd_info = QLabel()
        self.rd_info.setObjectName("hint")
        self.rd_info.setWordWrap(True)
        ol.addWidget(self.rd_info, r, 0, 1, 2)
        lay.addWidget(opts)
        self.rd_opts = opts             # hidden until the card is found: its options vary
        self._rd_ctrls = None
        self.update_hdspe(None)

    def _write_role(self, role, value):
        name, _c = hdspe.control(self._rd_ctrls, role)
        if name:
            self._write(name, value)

    def _card_chosen(self, i):
        self.hw.exact = self.st["hw"]["card"] = self.card_combo.itemText(i)
        self.hw_changed.emit()
        self.card_chosen.emit()

    def _show_cards(self):
        cards = Hardware.find_cards(self.dev.alsa_card)
        names = [name for _n, name in cards]
        if [self.card_combo.itemText(i) for i in range(self.card_combo.count())] != names:
            self.card_combo.clear()
            self.card_combo.addItems(names)
        self.card_combo.setVisible(len(names) > 1)
        if not self.card_combo.view().isVisible():
            self.card_combo.setCurrentIndex(next((i for i, (n, _name) in enumerate(cards)
                                                  if n == self.hw.card), -1))

    def update_hdspe(self, ctrls):
        """ctrls: the card's ALSA controls (Hardware.read()), or None if they can't be read."""
        self._show_cards()
        st = hdspe.decode_status(ctrls, self.dev.model)
        usable = True
        if st:
            drv = Hardware.card_driver(self.hw.card) or st["driver"]
            usable = (self.st["hw"]["use_anyway"]
                      or hdspe.driver_usable(drv, Hardware.pci_vendor(self.hw.card)))
        self._rd_ctrls = ctrls if st else None
        self._updating = True
        try:
            for role, combo in self.rd_combos.items():
                _name, c = hdspe.control(ctrls, role) if st else (None, None)
                # snd-hdspe can't change the internal rate while audio runs (as in hdspeconf)
                combo.setEnabled(bool(c) and usable and c["rw"]
                                 and not (role == "internal" and st["running"]
                                          and st["driver"] == hdspe.HDSPE))
                if role in self.rd_combo_labels:
                    combo.setVisible(bool(c))
                    self.rd_combo_labels[role].setVisible(bool(c))
                if not c:
                    continue
                texts = [hdspe.display_item(t) for t in c["items"]]
                if [combo.itemText(i) for i in range(combo.count())] != texts:
                    combo.clear()
                    combo.addItems(texts)
                if not combo.view().isVisible() and c["value"] is not None:
                    combo.setCurrentIndex(c["value"])
            for name, box in self.rd_toggles.items():
                c = ctrls.get(name) if st else None
                box.setVisible(bool(c))
                box.setEnabled(bool(c) and usable)
                if c and c["value"] is not None:
                    box.setChecked(bool(c["value"]))
        finally:
            self._updating = False
        self.rd_opts.setVisible(st is not None)
        if st is None:
            self.rd_info.setText("")
            self._show_fw_status(None, read_failure(self.hw, "Card"))
            return
        if not usable:
            self.rd_info.setText(f"<span style='color:{AMBER}'>Settings are off: see the driver "
                                 "note under Status.</span>")
        else:
            info = "snd-hdspe driver" if st["driver"] == hdspe.HDSPE else "snd-hdspm driver"
            if st["firmware"]:
                info += f", firmware {st['firmware']}"
            self.rd_info.setText(info)
        self.cur_src.set_current(st["source"], st["mode"])
        self.rate.setText(f"{st['rate'] / 1000:g} kHz" if st["rate"] else "—")
        self.rate.setToolTip("")
        shown = {label: (state, rate) for label, state, rate in st["inputs"]}
        for (led, rate), (label, *_unused) in zip(self.sync_widgets, self.dev.model.status_inputs):
            state, r = shown.get(label, (None, None))
            led.set_state(state)
            rate.setText(r.replace(" kHz", "k") if r else "—")
            # rows for the optional TCO module only show when the driver reports it
            for w in (led, rate, self._sync_labels[label]):
                w.setVisible(label in shown or label != "TCO")

    def _write(self, name, value):
        if not self._updating:
            self._report(self.hw.write(name, value))

    def _report(self, why, tool="amixer"):
        if why:
            self.notice.emit(write_failure(why, self.dev, tool))

    def _enable_pro(self):
        if not self._card_name:
            return
        if ask(self, "Switch profile",
               "Switch the Digiface to the Pro Audio profile?\n\n"
               "Apps can then record all of its hardware inputs. Its output device gets a new "
               "name ending in pro-output-0, so apps that play to it directly may need it "
               "chosen again. Audio may drop out for a moment.", "Switch profile"):
            self._report(set_card_profile(self._card_name, "pro-audio"), "pactl")

    def update_hw(self, ctrls):
        self._updating = True
        try:
            if ctrls is None:
                text, tip = read_failure(self.hw, "Digiface")
                self.rate.setText(f"<span style='color:#ff2a4f'>{text}</span>")
                self.rate.setToolTip(tip)
                return
            self.rate.setToolTip("")
            src = ctrls.get("Sync Source")
            if src:
                if self.sync_src.count() != len(src["items"]):
                    self.sync_src.clear()
                    self.sync_src.addItems(src["items"])
                if not self.sync_src.view().isVisible():
                    self.sync_src.setCurrentIndex(src["value"])
            cs = ctrls.get("Current Sync Source")
            if cs:
                self.cur_src.set_current(cs["items"][cs["value"]])
            r = ctrls.get("Current Rate")
            if r:
                self.rate.setText(f"{r['value'] / 1000:g} kHz")
            for i, (fmt, led, rate, out) in enumerate(self.port_widgets):
                n = i + 1
                sync = ctrls.get(f"Input {n} Sync")
                state = sync["items"][sync["value"]] if sync else None
                led.set_state(state)
                f = ctrls.get(f"Input {n} Format")
                locked = state in ("Lock", "Sync")
                fmt.setText(f["items"][f["value"]] if f and locked else "—")
                ir = ctrls.get(f"Input {n} Rate")
                rate.setText(f"{ir['value'] / 1000:g}k" if ir and locked and ir["value"] else "—")
                of = ctrls.get(f"Output {n} Format")
                if of and not out.view().isVisible():
                    out.setCurrentIndex(of["value"])
        finally:
            self._updating = False

    def update_hw_mixer(self, status, engine_error=None):
        """status: Engine.status() dict, or None if the engine is not running (engine_error:
        why it could not be started)."""
        if status is None:
            self.hw_label.setText("—")
            self.routes_label.setText("—")
            self.eng_label.setText(light(RED, "Stopped", False))
            self.sink_label.setText("—")
            self.show_status_note(html.escape(engine_error) if engine_error else
                                  "The hardware keeps its last mix. Start the engine to "
                                  "change it.")
            self.use_btn.hide()
            self.eng_btn.setText("Start engine")
            return
        value, note = hw_state(self.dev, status["hw"])
        self.hw_label.setText(value)
        self.use_btn.setVisible(status["hw"] == "wrong-driver")
        if status["hw"] in ("active", "no-nodes"):
            limit = f" of {self.dev.max_routes}" if self.dev.max_routes else ""
            self.routes_label.setText(f"{status['hw_nodes']}{limit}")
            self.hw_label.setToolTip("Meters come from the interface" if status["hw_levels"]
                                     else "")
        else:
            self.routes_label.setText("—")
        self.eng_label.setText(light(CYAN, "Running") if status["processing"] else
                               light(AMBER, "Not responding"))
        self.sink_label.setText(
            f"Linked to playback {self.dev.sink_channels}" if status["sink_linked"] else
            light(AMBER, f"Not linked to the {self.dev.name.replace('RME ', '')}", False))
        self.show_status_note(note)
        self.eng_btn.setText("Restart engine")

    def update_profile(self, name, profile, has_in):
        self._card_name = name
        if not name:
            self.profile.setText(light(OFF, "Not in PipeWire", False))
            self.recording.setText("—")
            self.pro_btn.setVisible(False)
            return
        self.profile.setText(html.escape(profile or "—"))
        self.recording.setText(light(CYAN, "All inputs") if has_in else
                               light(AMBER, "No inputs (monitoring still works)", False))
        self.pro_btn.setVisible(not has_in)
