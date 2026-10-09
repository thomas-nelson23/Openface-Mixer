"""Per-channel settings popup, like TotalMix's channel settings (the wrench on each strip).

Fireface 802: phase invert on every hardware input and output, Level and Gain on AN 1-8 inputs,
48V and Inst on AN 9-12, and Level on AN 1-8 outputs. On a stereo strip the settings apply to
both channels, except phase, which is per side.

Fireface 800: front/rear jack on AN 1, 7 and 8, 48V on AN 7-10, and Drive, Limiter and Speaker
Emulation on the AN 1 instrument input.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QLabel, QVBoxLayout,
)

from . import fireface800 as ff800
from . import fireface802 as ff


class ChannelSettings(QFrame):
    changed = Signal()

    def __init__(self, dev, settings, kind, channels, title, parent=None):
        """settings: the device settings dict (st["hw"]), edited in place."""
        super().__init__(parent, Qt.Popup)
        self.setObjectName("chansettings")
        self.setStyleSheet("QFrame#chansettings{background:#2a2a2d;border:1px solid #111;"
                           "border-radius:4px}")
        self.s = settings
        self.kind = kind
        self.channels = channels
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel(f"<b>{title}</b> <span style='color:#8e8e93'>settings</span>"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        lay.addLayout(grid)
        if dev.key == "ff800":
            self._build_ff800(grid)
        else:
            self._build_ff802(grid)

    def _build_ff802(self, grid):
        settings, kind, channels = self.s, self.kind, self.channels
        row = 0
        part = settings["in" if kind == "in" else "out"]
        sides = ("L", "R") if len(channels) == 2 else ("",)
        for c, side in zip(channels, sides):
            box = QCheckBox(f"Phase invert {side}".strip())
            box.setChecked(part["phase"][c])
            box.toggled.connect(lambda on, ch=c: self._set(part["phase"], [ch], on))
            grid.addWidget(box, row, 0, 1, 2)
            row += 1

        c0 = channels[0]
        line = [c for c in channels if c < ff.N_LINE_IN]
        mic = [c - ff.N_LINE_IN for c in channels if ff.N_LINE_IN <= c < ff.N_LINE_IN + ff.N_MIC_IN]
        if kind == "in" and line:
            grid.addWidget(QLabel("Level"), row, 0)
            level = QComboBox()
            level.addItems(ff.IN_LEVELS)
            level.setCurrentIndex(part["level"][c0])
            level.currentIndexChanged.connect(lambda i: self._set(part["level"], line, i))
            grid.addWidget(level, row, 1)
            row += 1
            grid.addWidget(QLabel("Gain"), row, 0)
            gain = QDoubleSpinBox()
            gain.setRange(0.0, ff.LINE_GAIN_MAX_DB)
            gain.setSingleStep(0.5)
            gain.setDecimals(1)
            gain.setSuffix(" dB")
            gain.setValue(part["gain"][c0])
            gain.valueChanged.connect(lambda v: self._set(part["gain"], line, round(v, 1)))
            grid.addWidget(gain, row, 1)
            row += 1
        if kind == "in" and mic:
            p48 = QCheckBox("48V phantom power")
            p48.setChecked(part["p48"][mic[0]])
            inst = QCheckBox("Inst (Hi-Z)")
            inst.setChecked(part["inst"][mic[0]])
            p48.toggled.connect(lambda on: self._set(part["p48"], mic, on))
            inst.toggled.connect(lambda on: self._set(part["inst"], mic, on))
            grid.addWidget(p48, row, 0, 1, 2)
            grid.addWidget(inst, row + 1, 0, 1, 2)
            row += 2
        if kind == "out" and line:
            grid.addWidget(QLabel("Level"), row, 0)
            level = QComboBox()
            level.addItems(ff.OUT_LEVELS)
            level.setCurrentIndex(part["level"][c0])
            level.currentIndexChanged.connect(lambda i: self._set(part["level"], line, i))
            grid.addWidget(level, row, 1)
            row += 1

    def _build_ff800(self, grid):
        s, row = self.s, 0
        for c in self.channels:
            name = ff800.chan_label("in", c)
            if c in ff800.JACK_INPUTS:
                j = ff800.JACK_INPUTS.index(c)
                grid.addWidget(QLabel(f"{name} input"), row, 0)
                jack = QComboBox()
                for key, label in ff800.JACKS:
                    jack.addItem(label + (" (Inst)" if c == ff800.INST_IN and key == "front"
                                          else ""), key)
                jack.setCurrentIndex(max(0, jack.findData(s["jacks"][j])))
                jack.activated.connect(
                    lambda i, w=jack, j=j: self._set(s["jacks"], [j], w.itemData(i)))
                grid.addWidget(jack, row, 1)
                row += 1
            if c in ff800.P48_INPUTS:
                p = ff800.P48_INPUTS.index(c)
                box = QCheckBox(f"{name} 48V phantom power")
                box.setChecked(s["p48"][p])
                box.toggled.connect(lambda on, p=p: self._set(s["p48"], [p], on))
                grid.addWidget(box, row, 0, 1, 2)
                row += 1
            if c == ff800.INST_IN:
                for key, label in (("drive", "Drive (+25 dB)"), ("limiter", "Limiter"),
                                   ("speaker_emu", "Speaker emulation")):
                    box = QCheckBox(label)
                    box.setChecked(s[key])
                    box.toggled.connect(lambda on, k=key: self._set_key(k, on))
                    grid.addWidget(box, row, 0, 1, 2)
                    row += 1

    def _set_key(self, key, v):
        self.s[key] = v
        self.changed.emit()

    def _set(self, values, indices, v):
        for i in indices:
            values[i] = v
        self.changed.emit()
