"""Per-channel settings popup, like TotalMix's channel settings (the wrench on each strip).

Shown for devices with channel settings (the Fireface 802): phase invert on every hardware input
and output, Level and Gain on AN 1-8 inputs, 48V and Inst on AN 9-12, and Level on AN 1-8
outputs. On a stereo strip the settings apply to both channels, except phase, which is per side.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QLabel, QVBoxLayout,
)

from . import fireface802 as ff


class ChannelSettings(QFrame):
    changed = Signal()

    def __init__(self, settings, kind, channels, title, parent=None):
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

    def _set(self, values, indices, v):
        for i in indices:
            values[i] = v
        self.changed.emit()
