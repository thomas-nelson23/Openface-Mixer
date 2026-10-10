"""Control room panel (Dim, Mono, Speaker B, Talkback, External Input) and the ARC USB link.

ArcLink finds the ARC USB, reads its keys and encoder, and lights its LEDs; MainWindow decides
what each key does. ControlRoomPanel is the settings-panel box with the same switches on screen.
"""
from PySide6.QtCore import QObject, QSocketNotifier, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QGroupBox, QLabel, QPushButton

from . import arc
from .control_room import NO_PAIR, control_room, has_speaker_b
from .theme import ACCENT

SWITCHES = (("dim", "Dim"), ("mono", "Mono"), ("speaker_b", "Speaker B"),
            ("talkback", "Talkback"), ("ext_in", "Ext In"))


class ArcLink(QObject):
    """Keeps the ARC USB open while it is plugged in. Emits key(index, pressed) and encoder(n)."""
    key = Signal(int, bool)
    encoder = Signal(int)
    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.port = None
        self.notifier = None
        self.status = ""
        self._leds = [None] * arc.N_KEYS
        QTimer(self, interval=2000, timeout=self.try_open).start()
        QTimer.singleShot(0, self.try_open)

    def _set_status(self, s):
        if s != self.status:
            self.status = s
            self.status_changed.emit(s)

    def try_open(self):
        if self.port is not None:
            return
        path = arc.find_port()
        if path is None:
            self._set_status("absent")
            return
        try:
            self.port = arc.ArcPort(path)
        except PermissionError:
            self._set_status("no-access")
            return
        except OSError:
            self._set_status("busy")
            return
        self.notifier = QSocketNotifier(self.port.fileno(), QSocketNotifier.Read, self)
        self.notifier.activated.connect(self._readable)
        self._leds = [None] * arc.N_KEYS
        self._set_status("connected")

    def disconnect_port(self):
        if self.notifier is not None:
            self.notifier.setEnabled(False)
            self.notifier.deleteLater()
            self.notifier = None
        if self.port is not None:
            self.port.close()
            self.port = None

    def _readable(self):
        try:
            msgs = self.port.read()
        except OSError:
            self.disconnect_port()
            self._set_status("absent")
            return
        for msg in msgs:
            ev = arc.decode(msg)
            if ev is None:
                continue
            if ev[0] == "key":
                self.key.emit(ev[1], ev[2])
            else:
                self.encoder.emit(ev[1])

    def set_leds(self, lit):
        """lit: one bool per key. Only changed LEDs are sent."""
        if self.port is None:
            return
        for i, on in enumerate(lit):
            if self._leds[i] != on:
                self._leds[i] = on
                self.port.write(arc.led_message(i, on))


class ControlRoomPanel(QGroupBox):
    """Switches and assignments. Emits changed() after editing st["control_room"]."""
    changed = Signal()
    STATUS = {
        "connected": "<span style='color:#34c759'>●</span> ARC USB connected",
        "absent": "<span style='color:#5b5b60'>●</span> ARC USB not connected",
        "no-access": "<span style='color:#ff6b5b'>●</span> No permission to open the ARC USB "
                     "(add yourself to the <tt>audio</tt> group)",
        "busy": "<span style='color:#ff6b5b'>●</span> ARC USB is in use by another program",
    }

    def __init__(self, st, parent=None):
        super().__init__("CONTROL ROOM", parent)
        self.st = st
        self._updating = False
        g = QGridLayout(self)
        self.buttons = {}
        for i, (key, text) in enumerate(SWITCHES):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setStyleSheet(f"QPushButton:checked {{ background:{ACCENT.name()}; color:#111; }}")
            b.clicked.connect(lambda on, k=key: self._switch(k, on))
            g.addWidget(b, i // 3, i % 3)
            self.buttons[key] = b
        self.combos = {}
        rows = (("main", "Main Out"), ("main_b", "Speaker B"), ("phones", "Phones"),
                ("talkback_src", "Talkback mic"), ("ext_src", "External In"))
        for r, (key, text) in enumerate(rows, start=2):
            g.addWidget(QLabel(text), r, 0)
            c = QComboBox()
            c.activated.connect(lambda _i, k=key: self._assign(k))
            g.addWidget(c, r, 1, 1, 2)
            self.combos[key] = c
        self.arc_label = QLabel(self.STATUS["absent"])
        self.arc_label.setWordWrap(True)
        g.addWidget(self.arc_label, r + 1, 0, 1, 3)

    def set_channels(self, pairs, inputs, input_pairs):
        """pairs / inputs / input_pairs: lists of (label, index) for the combos."""
        self._updating = True
        for key, items in (("main", pairs), ("main_b", [("Off", NO_PAIR)] + pairs), ("phones", pairs),
                           ("talkback_src", inputs), ("ext_src", input_pairs)):
            c = self.combos[key]
            c.clear()
            for label, idx in items:
                c.addItem(label, idx)
        self._updating = False
        self.refresh()

    def refresh(self):
        cr = control_room(self.st)
        self._updating = True
        for key, b in self.buttons.items():
            b.setChecked(bool(cr[key]))
        self.buttons["speaker_b"].setEnabled(has_speaker_b(self.st))
        for key, c in self.combos.items():
            i = c.findData(cr[key])
            if i >= 0:
                c.setCurrentIndex(i)
        self._updating = False

    def set_arc_status(self, s):
        self.arc_label.setText(self.STATUS.get(s, s))

    def _switch(self, key, on):
        control_room(self.st)[key] = on
        self.changed.emit()

    def _assign(self, key):
        if self._updating:
            return
        val = self.combos[key].currentData()
        if val is not None:
            cr = control_room(self.st)
            cr[key] = val
            if not has_speaker_b(self.st):
                cr["speaker_b"] = False
            self.changed.emit()
