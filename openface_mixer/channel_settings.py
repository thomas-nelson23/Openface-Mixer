"""Per-channel settings popup, like TotalMix's channel settings (the wrench on each strip).

Fireface 802: phase invert on every hardware input and output, Level and Gain on AN 1-8 inputs,
48V and Inst on AN 9-12, and Level on AN 1-8 outputs. On a stereo strip the settings apply to
both channels, except phase, which is per side.

Fireface 800: front/rear jack on AN 1, 7 and 8, 48V on AN 7-10, and Drive, Limiter and Speaker
Emulation on the AN 1 instrument input.

Fireface 400: 48V and gain on AN 1/2, instrument, pad and gain on AN 3/4.

Fireface UCX II: phase invert on every input and output, gain on AN 1-8, 48V on AN 1/2, Level on
AN 3-8, Hi-Z on AN 3/4, and Level on AN 1-6 and the phones outputs.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QLabel, QVBoxLayout,
)

from . import fireface400 as ff400
from . import fireface800 as ff800
from . import fireface802 as ff
from . import ucx2
from .theme import key_label, px


class ChannelSettings(QFrame):
    changed = Signal()

    def __init__(self, dev, settings, kind, channels, title, parent=None):
        """settings: the device settings dict (st["hw"]), edited in place."""
        super().__init__(parent, Qt.Popup)
        self.setObjectName("chansettings")
        self.setStyleSheet("QFrame#chansettings{background:#040e16;border:1px solid "
                           f"rgba(255,255,255,0.16);border-radius:{px(8)}px}}")
        self.s = settings
        self.kind = kind
        self.channels = channels
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(12), px(10), px(12), px(12))
        lay.setSpacing(px(8))
        lay.addWidget(QLabel(f"<b>{title}</b> <span style='color:#7d9299'>settings</span>"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(px(10))
        grid.setVerticalSpacing(px(5))
        lay.addLayout(grid)
        {ff800: self._build_ff800, ff400: self._build_ff400, ucx2: self._build_ucx2}.get(
            dev.proto, self._build_ff802)(grid)

    def _phase(self, grid, part):
        """Phase invert per side; returns the next grid row."""
        sides = ("L", "R") if len(self.channels) == 2 else ("",)
        for row, (c, side) in enumerate(zip(self.channels, sides)):
            box = QCheckBox(f"Phase invert {side}".strip())
            box.setChecked(part["phase"][c])
            box.toggled.connect(lambda on, ch=c: self._set(part["phase"], [ch], on))
            grid.addWidget(box, row, 0, 1, 2)
        return len(sides)

    def _combo(self, grid, row, label, items, values, indices):
        grid.addWidget(key_label(label), row, 0)
        combo = QComboBox()
        combo.addItems(items)
        combo.setCurrentIndex(values[indices[0]])
        combo.currentIndexChanged.connect(lambda i: self._set(values, indices, i))
        grid.addWidget(combo, row, 1)
        return row + 1

    def _gain(self, grid, row, values, indices, maximum, step=0.5, scale=1):
        """A gain spin box in dB for values stored in units of 1/scale dB."""
        grid.addWidget(key_label("Gain"), row, 0)
        gain = QDoubleSpinBox()
        gain.setRange(0.0, maximum)
        gain.setSingleStep(step)
        gain.setDecimals(1 if step < 1 else 0)
        gain.setSuffix(" dB")
        gain.setValue(values[indices[0]] / scale)
        gain.valueChanged.connect(
            lambda v: self._set(values, indices, round(v * scale) if scale != 1 else round(v, 1)))
        grid.addWidget(gain, row, 1)
        return row + 1

    def _check(self, grid, row, label, values, indices):
        box = QCheckBox(label)
        box.setChecked(values[indices[0]])
        box.toggled.connect(lambda on: self._set(values, indices, on))
        grid.addWidget(box, row, 0, 1, 2)
        return row + 1

    def _build_ff802(self, grid):
        kind, channels = self.kind, self.channels
        part = self.s["in" if kind == "in" else "out"]
        row = self._phase(grid, part)
        line = [c for c in channels if c < ff.N_LINE_IN]
        mic = [c - ff.N_LINE_IN for c in channels if ff.N_LINE_IN <= c < ff.N_LINE_IN + ff.N_MIC_IN]
        if kind == "in" and line:
            row = self._combo(grid, row, "Level", ff.IN_LEVELS, part["level"], line)
            row = self._gain(grid, row, part["gain"], line, ff.LINE_GAIN_MAX_DB)
        if kind == "in" and mic:
            row = self._check(grid, row, "48V phantom power", part["p48"], mic)
            row = self._check(grid, row, "Inst (Hi-Z)", part["inst"], mic)
        if kind == "out" and line:
            row = self._combo(grid, row, "Level", ff.OUT_LEVELS, part["level"], line)

    def _build_ff800(self, grid):
        s, row = self.s, 0
        for c in self.channels:
            name = ff800.chan_label("in", c)
            if c in ff800.JACK_INPUTS:
                j = ff800.JACK_INPUTS.index(c)
                grid.addWidget(key_label(f"{name} input"), row, 0)
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

    def _build_ff400(self, grid):
        s, row = self.s, 0
        mic = [ff400.MIC_INPUTS.index(c) for c in self.channels if c in ff400.MIC_INPUTS]
        inst = [ff400.INST_INPUTS.index(c) for c in self.channels if c in ff400.INST_INPUTS]
        if mic:
            row = self._check(grid, row, "48V phantom power", s["p48"], mic)
            row = self._gain(grid, row, s["mic_gain"], mic, ff400.MIC_GAIN_MAX, step=1)
        if inst:
            row = self._check(grid, row, "Instrument", s["inst"], inst)
            row = self._check(grid, row, "Pad", s["pad"], inst)
            row = self._gain(grid, row, s["line_gain"], inst, ff400.LINE_GAIN_MAX / 2, scale=2)

    def _build_ucx2(self, grid):
        kind, channels = self.kind, self.channels
        part = self.s["in" if kind == "in" else "out"]
        row = self._phase(grid, part)
        if kind == "in":
            analog = [c for c in channels if c < ucx2.N_ANALOG_IN]
            if analog:
                row = self._gain(grid, row, part["gain"], analog, ucx2.gain_max(analog[0]))
            mic = [ucx2.MIC_INPUTS.index(c) for c in channels if c in ucx2.MIC_INPUTS]
            if mic:
                row = self._check(grid, row, "48V phantom power", part["p48"], mic)
            level = [ucx2.LEVEL_INPUTS.index(c) for c in channels if c in ucx2.LEVEL_INPUTS]
            if level:
                row = self._combo(grid, row, "Level", ucx2.IN_LEVELS, part["level"], level)
            hiz = [ucx2.HIZ_INPUTS.index(c) for c in channels if c in ucx2.HIZ_INPUTS]
            if hiz:
                row = self._check(grid, row, "Hi-Z (instrument)", part["hiz"], hiz)
        else:
            analog = [c for c in channels if c < ucx2.N_LINE_OUT + 2]
            if analog:
                items = ucx2.OUT_LEVELS if analog[0] < ucx2.N_LINE_OUT else ucx2.PHONES_LEVELS
                row = self._combo(grid, row, "Level", items, part["level"], analog)

    def _set_key(self, key, v):
        self.s[key] = v
        self.changed.emit()

    def _set(self, values, indices, v):
        for i in indices:
            values[i] = v
        self.changed.emit()
