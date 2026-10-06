"""Right-hand panel: Digiface clock/port settings, card profile and engine status."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QGroupBox, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .hardware import set_card_profile

UDEV_RULE_HINT = (
    "Openface Mixer needs write access to the Digiface's USB device. Install the udev rule "
    "once:<br><tt>sudo install -m644 packaging/70-rme-digiface.rules /etc/udev/rules.d/</tt><br>"
    "<tt>sudo udevadm control --reload &amp;&amp; sudo udevadm trigger</tt>")

HW_STATE_TEXT = {
    "off": "Hardware mixer not in use.",
    "no-device": "<span style='color:#ff6b5b'>Digiface not found on USB.</span>",
    "no-access": "<span style='color:#ff6b5b'>No permission to open the Digiface.</span><br>"
                 + UDEV_RULE_HINT,
    "busy": "<span style='color:#ff6b5b'>The Digiface mixer interface is in use by another "
            "program.</span>",
    "active": "<span style='color:#34c759'>Hardware mixer active</span>",
    "no-nodes": "<span style='color:#ffd60a'>Hardware mixer active, but this mix needs more "
                "than 2048 routes; some sends are missing.</span>",
    "error": "<span style='color:#ff6b5b'>USB error talking to the Digiface; retrying.</span>",
}


class Led(QLabel):
    COLORS = {"No Lock": "#5b5b60", "Lock": "#ffd60a", "Sync": "#34c759"}

    def set_state(self, s):
        c = self.COLORS.get(s, "#5b616a")
        self.setText(f"<span style='color:{c}'>●</span> {s or '—'}")


class SettingsPanel(QWidget):
    mode_changed = Signal(str)   # "hardware" or "software"

    def __init__(self, hw, parent=None):
        super().__init__(parent)
        self.hw = hw
        self.setFixedWidth(310)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 0, 0, 0)
        lay.setSpacing(10)

        clock = QGroupBox("CLOCK")
        cl = QGridLayout(clock)
        cl.addWidget(QLabel("Clock source"), 0, 0)
        self.sync_src = QComboBox()
        self.sync_src.activated.connect(lambda i: self._write("Sync Source", i))
        cl.addWidget(self.sync_src, 0, 1)
        cl.addWidget(QLabel("Current source"), 1, 0)
        self.cur_src = QLabel("—")
        cl.addWidget(self.cur_src, 1, 1)
        cl.addWidget(QLabel("Sample rate"), 2, 0)
        self.rate = QLabel("—")
        cl.addWidget(self.rate, 2, 1)
        lay.addWidget(clock)

        ports = QGroupBox("OPTICAL PORTS")
        pl = QGridLayout(ports)
        for c, h in enumerate(["", "In format", "In status", "In rate", "Out format"]):
            lbl = QLabel(f"<span style='color:#8e8e93;font-size:9px'>{h}</span>")
            pl.addWidget(lbl, 0, c)
        self.port_widgets = []
        for i in range(4):
            fmt, led, rate, out = QLabel("—"), Led(), QLabel("—"), QComboBox()
            out.addItems(["ADAT", "S/PDIF"])
            out.activated.connect(lambda v, n=i + 1: self._write(f"Output {n} Format", v))
            for c, w in enumerate([QLabel(f"{i + 1}"), fmt, led, rate, out]):
                pl.addWidget(w, i + 1, c)
            self.port_widgets.append((fmt, led, rate, out))
        lay.addWidget(ports)

        prof = QGroupBox("CARD PROFILE")
        vl = QVBoxLayout(prof)
        self.profile = QLabel("—")
        self.profile.setWordWrap(True)
        vl.addWidget(self.profile)
        self.pro_btn = QPushButton("Enable hardware inputs (Pro Audio profile)")
        self.pro_btn.clicked.connect(self._enable_pro)
        vl.addWidget(self.pro_btn)
        lay.addWidget(prof)

        eng = QGroupBox("MIXER ENGINE")
        el = QVBoxLayout(eng)
        self.mode = QComboBox()
        self.mode.addItem("Hardware DSP (zero latency)", "hardware")
        self.mode.addItem("Software (PipeWire)", "software")
        self.mode.activated.connect(lambda i: self.mode_changed.emit(self.mode.itemData(i)))
        el.addWidget(self.mode)
        self.hw_label = QLabel("—")
        self.hw_label.setWordWrap(True)
        el.addWidget(self.hw_label)
        self.eng_label = QLabel("—")
        self.eng_label.setWordWrap(True)
        el.addWidget(self.eng_label)
        self.eng_btn = QPushButton("Restart engine")
        el.addWidget(self.eng_btn)
        lay.addWidget(eng)

        note = QLabel(
            "<span style='color:#6e6e73;font-size:9px'>Hardware mode mixes in the Digiface's "
            "own DSP, like TotalMix: input monitoring has no added latency and the mix keeps "
            "running with the computer idle. Software mode mixes in PipeWire instead (latency "
            "equals your PipeWire round trip).<br><br>Playback 1/2 = the <i>Openface Mixer "
            "Playback</i> sink. Other playback channels: connect apps to "
            "<i>openface_mixer:play_N</i> with qpwgraph/Helvum.</span>")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch()
        self._card_name = None
        self._updating = False

    def _write(self, name, value):
        if not self._updating:
            self.hw.write(name, value)

    def _enable_pro(self):
        if not self._card_name:
            return
        r = QMessageBox.question(
            self, "Switch profile",
            "Switch the Digiface to the 'Pro Audio' profile?\n\n"
            "This exposes all 32 hardware inputs. The output device will be renamed to "
            "'…pro-output-0' (your existing combine-stream config already matches it). "
            "Audio may briefly drop out.")
        if r == QMessageBox.Yes:
            set_card_profile(self._card_name, "pro-audio")

    def update_hw(self, ctrls):
        self._updating = True
        try:
            if ctrls is None:
                self.rate.setText("<span style='color:#ff6b5b'>Digiface not found</span>")
                return
            src = ctrls.get("Sync Source")
            if src:
                if self.sync_src.count() != len(src["items"]):
                    self.sync_src.clear()
                    self.sync_src.addItems(src["items"])
                if not self.sync_src.view().isVisible():
                    self.sync_src.setCurrentIndex(src["value"])
            cs = ctrls.get("Current Sync Source")
            if cs:
                self.cur_src.setText(cs["items"][cs["value"]])
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

    def set_mode(self, mode):
        self.mode.setCurrentIndex(self.mode.findData(mode))

    def update_hw_mixer(self, status):
        """status: Engine.status() dict, or None if the engine is not running."""
        if status is None:
            self.hw_label.setText("—")
            return
        text = HW_STATE_TEXT.get(status["hw"], status["hw"])
        if status["hw"] in ("active", "no-nodes"):
            text += f"<br>{status['hw_nodes']} of 2048 routes in use"
        self.hw_label.setText(text)

    def update_profile(self, name, profile, has_in):
        self._card_name = name
        if not name:
            self.profile.setText("Digiface not present in PipeWire")
            self.pro_btn.setVisible(False)
            return
        ok = "<span style='color:#4cd964'>inputs available</span>" if has_in else \
            "<span style='color:#ffcc00'>no input device — input meters/monitoring disabled</span>"
        self.profile.setText(f"Active: <b>{profile}</b><br>{ok}")
        self.pro_btn.setVisible(not has_in)
