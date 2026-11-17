"""Control room panel (Dim, Recall, Mono, Speaker B, Talkback, External Input) and the ARC USB
link.

ArcLink finds the ARC USB, reads its keys and encoder, and lights its LEDs; MainWindow decides
what each key does. ControlRoomPanel is the settings-panel box with the same switches on screen.
"""
from PySide6.QtCore import QObject, QSocketNotifier, Qt, QTimer, Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QMenu, QPushButton,
)

from . import arc
from .control_room import MAX_PHONES, NO_PAIR, control_room, has_speaker_b
from .model import FADER_MAX_DB, FADER_MIN_DB, NEG_INF, parse_db
from .theme import Section, key_label, px

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


class Amount(QDoubleSpinBox):
    """A level in dB. It takes a typed value when Enter is pressed, not digit by digit, and
    the wheel only once it has been clicked: over it, the wheel scrolls the settings."""

    def __init__(self, low, high, step):
        super().__init__()
        self.setRange(low, high)
        self.setSingleStep(step)
        self.setDecimals(1)
        self.setSuffix(" dB")
        self.setKeyboardTracking(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.lineEdit().setTextMargins(px(5), 0, 0, 0)      # in line with the lists' text

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()

    # A comma and a point both mean the decimal mark, as in a fader's level box, whatever the
    # system's language: Qt alone reads "-7,5" as -75 in English and "2.5" as 25 in German.
    def validate(self, text, pos):
        typed = text.removesuffix(self.suffix()).strip()
        if parse_db(typed) not in (None, NEG_INF):
            return QValidator.Acceptable, text, pos
        partly = all(c in "+-−.,0123456789" for c in typed)
        return (QValidator.Intermediate if partly else QValidator.Invalid), text, pos

    def valueFromText(self, text):
        db = parse_db(text.removesuffix(self.suffix()))
        return self.value() if db in (None, NEG_INF) else db


class Several(QComboBox):
    """A list to tick several output pairs in. It looks like the lists that take one: closed,
    it shows what is chosen; open, it is a menu, filled by fill(menu) each time."""

    def __init__(self, fill):
        super().__init__()
        self.addItem("")
        self.menu = QMenu(self)
        self.menu.aboutToShow.connect(lambda: fill(self.menu))

    def showPopup(self):
        self.menu.popup(self.mapToGlobal(self.rect().bottomLeft()))


class ControlRoomPanel(Section):
    """Switches, assignments and amounts. Emits changed() after editing st["control_room"],
    and recall() when Main Out should go to the Recall level."""
    changed = Signal()
    recall = Signal()
    STATUS = {
        "connected": "<span style='color:#2ad4ff'>●</span> ARC USB connected",
        "absent": "<span style='color:#6f8a94'>○</span> ARC USB not connected",
        "no-access": "<span style='color:#ff2a4f'>●</span> No permission to open the ARC USB "
                     "(add yourself to the <tt>audio</tt> group)",
        "busy": "<span style='color:#ff2a4f'>●</span> ARC USB is in use by another program",
    }
    # (key in the control room, label, kind of control), in the order they are shown
    ROWS = (("main", "Main Out", "one"), ("main_b", "Speaker B", "one"),
            ("phones", "Phones", "several"), ("dim_db", "Dim", "db"),
            ("recall_db", "Recall level", "db"), ("talkback_src", "Talkback mic", "one"),
            ("talkback_dests", "Talkback to", "several"), ("talkback_dim_db", "Talkback dim", "db"),
            ("ext_src", "External In", "one"))
    TIPS = {
        "phones": f"Up to {MAX_PHONES} headphone outputs. Talkback goes to all of them",
        "dim_db": "How far Dim turns Main Out down",
        "recall_db": "The Main Out level that Recall goes back to",
        "talkback_dests": "Talkback always goes to the Phones. Tick other outputs to add them",
        "talkback_dim_db": "How far the mix on those outputs is turned down while you talk",
    }

    def __init__(self, st, parent=None):
        super().__init__("CONTROL ROOM", parent)
        self.st = st
        self._updating = False
        self.pairs = []                # (label, index) of the output pairs, see set_channels
        g = QGridLayout(self)
        self.buttons = {}
        for i, (key, text) in enumerate(SWITCHES):
            b = QPushButton(text)
            b.setCheckable(True)
            b.clicked.connect(lambda on, k=key: self._switch(k, on))
            g.addWidget(b, i // 3, i % 3)
            self.buttons[key] = b
        recall = QPushButton("Recall")
        recall.setToolTip("Set Main Out to the Recall level")
        recall.clicked.connect(self.recall)
        g.addWidget(recall, 1, 2)
        self.combos, self.choices, self.amounts = {}, {}, {}
        for r, (key, text, kind) in enumerate(self.ROWS, start=2):
            g.addWidget(key_label(text), r, 0)
            if kind == "one":
                w = self.combos[key] = QComboBox()
                w.activated.connect(lambda _i, k=key: self._assign(k))
            elif kind == "several":
                w = self.choices[key] = Several(lambda menu, k=key: self.fill_menu(k, menu))
            else:
                w = self.amounts[key] = (Amount(FADER_MIN_DB, FADER_MAX_DB, 0.5)
                                         if key == "recall_db" else Amount(FADER_MIN_DB, 0.0, 1.0))
                w.valueChanged.connect(lambda v, k=key: self._set(k, round(v, 1)))
            w.setToolTip(self.TIPS.get(key, ""))
            g.addWidget(w, r, 1, 1, 2)
        self.arc_label = QLabel(self.STATUS["absent"])
        self.arc_label.setObjectName("hint")
        self.arc_label.setWordWrap(True)
        g.addWidget(self.arc_label, r + 1, 0, 1, 3)

    def set_channels(self, pairs, inputs, input_pairs):
        """pairs / inputs / input_pairs: lists of (label, index) for the combos."""
        self._updating = True
        self.pairs = pairs
        for key, items in (("main", pairs), ("main_b", [("Off", NO_PAIR)] + pairs),
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
        for key, spin in self.amounts.items():
            spin.setValue(cr[key])
        names = dict((idx, label) for label, idx in self.pairs)
        for key, c in self.choices.items():
            # Talkback goes to the Phones first: they are named there too
            chosen = [p for p in dict.fromkeys(cr["phones"] + cr[key]) if p in names]
            more = f" +{len(chosen) - 1}" if len(chosen) > 1 else ""
            c.setItemText(0, names[chosen[0]] + more if chosen else "None")
        self._updating = False

    def fill_menu(self, key, menu):
        """The menu of a "several" row: every output pair, ticked if it is chosen. The Phones
        can't be unticked under "Talkback to", and no more than MAX_PHONES can be Phones."""
        cr = control_room(self.st)
        menu.clear()
        full = key == "phones" and len(cr[key]) >= MAX_PHONES
        for label, idx in self.pairs:
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(idx in cr[key] or idx in cr["phones"])
            a.setEnabled(idx in cr[key] or not (full or idx in cr["phones"]))
            a.triggered.connect(lambda on, p=idx: self._set(     # in the order they are ticked
                key, [q for q in cr[key] if q != p] + [p] * on))

    def set_arc_status(self, s):
        self.arc_label.setText(self.STATUS.get(s, s))

    def _switch(self, key, on):
        control_room(self.st)[key] = on
        self.changed.emit()

    def _set(self, key, value):
        if not self._updating:
            control_room(self.st)[key] = value
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
