"""TotalMix FX effect panels: a strip's EQ (with Low Cut) and Dynamics (with Auto Level), the
UCX II's Room EQ, and the Reverb / Echo unit. Laid out like TotalMix's own panels, with knobs that
show their value underneath and a response curve on top.

The panels edit st["hw"]["fx"] in place (see fx.py) and emit changed(); they stay open beside the
mixer like TotalMix's, one per strip and kind.
"""
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from . import fx
from .theme import CYAN, DIM, MONO, SANS, TEXT, font, px

PANEL_STYLE = ("QWidget#fxpanel{background:#040e16}"
               "QFrame#fxbox{background:rgba(255,255,255,0.02);"
               "border:1px solid rgba(255,255,255,0.08);border-radius:6px}")


def fmt_hz(v):
    return f"{v / 1000:.1f}k" if v >= 1000 else f"{round(v)}"


def fmt_level(v, lo):
    return "-∞" if v <= lo else f"{v:+.1f}" if v else "0.0"


# ------------------------------------------------------------------ knob
class ValueKnob(QWidget):
    """A TotalMix-style rotary control: label above, value below. Drag up/down (Shift for fine
    steps), scroll, or use the arrow keys; double-click resets to the default, right-click types a
    value."""
    changed = Signal(float)

    def __init__(self, label, lo, hi, value, default, step=1.0, fmt=None, log=False, unit="",
                 parent=None):
        super().__init__(parent)
        self.label, self.lo, self.hi, self.step = label, lo, hi, step
        self.default, self.log, self.unit = default, log, unit
        self.fmt = fmt or (lambda v: f"{v:.{self._decimals()}f}")
        self.value = self._snap(value)
        self._drag = None
        self.setFixedSize(px(52), px(62))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.SizeVerCursor)
        self._tip()

    def _decimals(self):
        return 0 if self.step >= 1 else 1 if self.step >= 0.1 else 2 if self.step >= 0.01 else 3

    def _snap(self, v):
        v = max(self.lo, min(self.hi, v))
        return round(round(v / self.step) * self.step, 6)

    def _pos(self, v):
        if self.log:
            return math.log(v / self.lo) / math.log(self.hi / self.lo)
        return (v - self.lo) / (self.hi - self.lo)

    def _val(self, pos):
        pos = max(0.0, min(1.0, pos))
        if self.log:
            return self.lo * (self.hi / self.lo) ** pos
        return self.lo + pos * (self.hi - self.lo)

    def _tip(self):
        self.setToolTip(f"{self.label}: {self.fmt(self.value)} {self.unit}".rstrip() +
                        "\nDrag or scroll (Shift: fine) · double-click: default · "
                        "right-click: type a value")
        self.setAccessibleName(f"{self.label} {self.fmt(self.value)} {self.unit}".rstrip())

    def set_value(self, v, emit=False):
        v = self._snap(v)
        if v == self.value:
            return
        self.value = v
        self._tip()
        self.update()
        if emit:
            self.changed.emit(v)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self._type_value()
        elif e.modifiers() & Qt.ControlModifier:
            self.set_value(self.default, True)
        else:
            self._drag = (e.position().y(), self._pos(self.value))

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        scale = 0.15 if e.modifiers() & Qt.ShiftModifier else 1.0
        dy = self._drag[0] - e.position().y()
        self.set_value(self._val(self._drag[1] + dy / px(160) * scale), True)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.set_value(self.default, True)

    def wheelEvent(self, e):
        self.nudge(1 if e.angleDelta().y() > 0 else -1, e.modifiers() & Qt.ShiftModifier)

    def keyPressEvent(self, e):
        fine = e.modifiers() & Qt.ShiftModifier
        if e.key() in (Qt.Key_Up, Qt.Key_Right):
            self.nudge(1, fine)
        elif e.key() in (Qt.Key_Down, Qt.Key_Left):
            self.nudge(-1, fine)
        elif e.key() == Qt.Key_PageUp:
            self.nudge(10, False)
        elif e.key() == Qt.Key_PageDown:
            self.nudge(-10, False)
        elif e.key() == Qt.Key_Home:
            self.set_value(self.default, True)
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._type_value()
        else:
            super().keyPressEvent(e)

    def nudge(self, n, fine=False):
        if self.log:      # frequencies move in musical steps: 1/12 octave, 1/48 fine
            v = self.value * 2 ** (n / (48 if fine else 12))
            if self._snap(v) == self.value:      # at low frequencies a step is under 1 Hz
                v = self.value + (1 if n > 0 else -1) * self.step
            self.set_value(v, True)
        else:
            self.set_value(self.value + n * self.step * (1 if fine else self._coarse()), True)

    def _coarse(self):
        span = (self.hi - self.lo) / self.step
        return 1 if span <= 100 else 5 if span <= 600 else 10

    def _type_value(self):
        v, ok = QInputDialog.getDouble(self, self.label, f"{self.label} ({self.unit or 'value'})",
                                       self.value, self.lo, self.hi, self._decimals())
        if ok:
            self.set_value(v, True)

    def changeEvent(self, e):
        super().changeEvent(e)
        self.update()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isEnabled()
        w = self.width()
        p.setFont(font(SANS, 8, QFont.DemiBold))
        p.setPen(QColor(TEXT if on else DIM).darker(100 if on else 140))
        p.drawText(QRectF(0, 0, w, px(12)), Qt.AlignCenter, self.label)
        c = QPointF(w / 2, px(30))
        r = px(13)
        ring = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setPen(QPen(QColor("#01070b"), px(3), Qt.SolidLine, Qt.RoundCap))
        p.drawArc(ring, 225 * 16, -270 * 16)
        pos = self._pos(self.value)
        zero = self._pos(0.0) if self.lo < 0 < self.hi and not self.log else 0.0
        arc_col = QColor(CYAN if on else DIM)
        p.setPen(QPen(arc_col, px(3), Qt.SolidLine, Qt.RoundCap))
        start = 225 - zero * 270
        p.drawArc(ring, int(start * 16), int(-(pos - zero) * 270 * 16))
        br = px(9)
        p.setBrush(QColor("#0f2838"))
        p.setPen(QPen(QColor(42, 212, 255, 200) if self.hasFocus() else QColor(255, 255, 255, 40),
                      1))
        p.drawEllipse(QRectF(c.x() - br, c.y() - br, 2 * br, 2 * br))
        a = math.radians(225 - pos * 270)
        p.setPen(QPen(QColor("#f5f5f7") if on else QColor(DIM), px(2), Qt.SolidLine,
                      Qt.RoundCap))
        p.drawLine(QPointF(c.x() + px(3) * math.cos(a), c.y() - px(3) * math.sin(a)),
                   QPointF(c.x() + px(8) * math.cos(a), c.y() - px(8) * math.sin(a)))
        p.setFont(font(MONO, 9))
        p.setPen(QColor("#9cc7cf") if on else QColor(DIM))
        p.drawText(QRectF(0, px(46), w, px(14)), Qt.AlignCenter, self.fmt(self.value))


# ------------------------------------------------------------------ curves
class CurveView(QWidget):
    """Frequency response, 20 Hz - 20 kHz on a log axis, ±20 dB, as TotalMix's EQ display."""
    FREQS = [20 * 1000 ** (i / 199) for i in range(200)]
    RANGE = 20.0

    def __init__(self, filters_fn, parent=None):
        super().__init__(parent)
        self.filters_fn = filters_fn
        self.setMinimumSize(px(300), px(120))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _x(self, f):
        return math.log(f / 20) / math.log(1000) * self.width()

    def _y(self, db):
        h = self.height()
        return h / 2 - max(-self.RANGE, min(self.RANGE, db)) / self.RANGE * (h / 2 - px(4))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#01070b"))
        p.setFont(font(MONO, 7))
        for f in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
            x = self._x(f)
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            p.setPen(QColor(DIM))
            p.drawText(QPointF(x + px(2), self.height() - px(3)), fmt_hz(f))
        for db in (-12, -6, 0, 6, 12):
            y = self._y(db)
            p.setPen(QPen(QColor(255, 255, 255, 40 if db == 0 else 18), 1))
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))
            if db:
                p.setPen(QColor(DIM))
                p.drawText(QPointF(px(2), y - px(2)), f"{db:+d}")
        db = fx.response(self.filters_fn(), self.FREQS)
        path = QPainterPath()
        for i, (f, d) in enumerate(zip(self.FREQS, db)):
            pt = QPointF(self._x(f), self._y(d))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setPen(QPen(CYAN, px(2)))
        p.drawPath(path)


class DynCurve(QWidget):
    """The compressor / expander transfer curve: input level across, output level up."""
    LO = -80.0

    def __init__(self, ch_fn, parent=None):
        super().__init__(parent)
        self.ch_fn = ch_fn
        self.setFixedSize(px(120), px(120))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#01070b"))
        w, h = self.width(), self.height()

        def pt(x, y):
            return QPointF((x - self.LO) / -self.LO * w, h - (y - self.LO) / -self.LO * h)

        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        for v in (-60, -40, -20):
            p.drawLine(pt(v, self.LO), pt(v, 0))
            p.drawLine(pt(self.LO, v), pt(0, v))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.DashLine))
        p.drawLine(pt(self.LO, self.LO), pt(0, 0))
        ch = self.ch_fn()
        xs = [self.LO + i * -self.LO / 120 for i in range(121)]
        ys = fx.dynamics_curve(ch, xs)
        path = QPainterPath()
        for i, (x, y) in enumerate(zip(xs, ys)):
            q = pt(x, max(self.LO, min(12.0, y)))
            path.moveTo(q) if i == 0 else path.lineTo(q)
        p.setClipRect(self.rect())
        p.setPen(QPen(CYAN if ch["dyn"] else QColor(DIM), px(2)))
        p.drawPath(path)


# ------------------------------------------------------------------ panel base
def on_button(text, checked, tip=""):
    """The section's power switch, lit like TotalMix's when on."""
    b = QPushButton(text)
    b.setCheckable(True)
    b.setChecked(checked)
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumWidth(px(64))
    return b


def box(title_widget):
    frame = QFrame()
    frame.setObjectName("fxbox")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(px(8), px(6), px(8), px(8))
    lay.setSpacing(px(4))
    head = QHBoxLayout()
    head.addWidget(title_widget)
    head.addStretch(1)
    lay.addLayout(head)
    return frame, lay, head


def caption(text):
    lbl = QLabel(text.upper())
    f = font(SANS, 9, QFont.Bold)
    f.setLetterSpacing(QFont.AbsoluteSpacing, px(1.8))
    lbl.setFont(f)
    lbl.setStyleSheet(f"color:{DIM.name()}")
    return lbl


class FxPanel(QWidget):
    """A tool window beside the mixer, one per strip and kind."""
    changed = Signal()
    closed = Signal()

    def __init__(self, title, parent=None):
        super().__init__(parent, Qt.Tool)
        self.setObjectName("fxpanel")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(PANEL_STYLE)
        self.setWindowTitle(title)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(px(12), px(10), px(12), px(12))
        self.lay.setSpacing(px(8))
        self.knobs = {}           # key -> ValueKnob, to refresh after a paste or an outside change

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)

    def emit(self):
        self.changed.emit()
        for w in self.findChildren(CurveView) + self.findChildren(DynCurve):
            w.update()

    def knob(self, label, spec, value, on_change, fmt=None, log=False, unit="", step=None):
        """spec: (scale, low, high, default) as in fx.py."""
        scale, lo, hi, default = spec
        k = ValueKnob(label, lo, hi, value, default, step or 1 / scale, fmt, log, unit)
        k.changed.connect(on_change)
        return k

    def combo(self, items, index, on_change):
        c = QComboBox()
        c.addItems(list(items))
        c.setCurrentIndex(max(0, min(len(items) - 1, int(index))))
        c.currentIndexChanged.connect(on_change)
        return c


# ------------------------------------------------------------------ channel EQ and dynamics
class StripPanel(FxPanel):
    """Base for the EQ and Dynamics panels of a strip: edits channels (both sides of a stereo
    strip) of fx[kind]."""

    def __init__(self, title, fxs, model, kind, channels, parent=None):
        super().__init__(title, parent)
        self.fx, self.m, self.kind, self.channels = fxs, model, kind, channels

    @property
    def ch(self):
        return self.fx[self.kind][self.channels[0]]

    def set(self, key, v):
        if isinstance(self.ch[key], bool):
            v = bool(v)
        elif isinstance(self.ch[key], int):
            v = int(round(v))
        else:
            v = round(float(v), 3)
        for c in self.channels:
            self.fx[self.kind][c][key] = v
        self.emit()

    def spec(self, section, key):
        for k, scale, lo, hi, default in self.m.params(section):
            if k == key:
                return scale, lo, hi, default
        raise KeyError(key)

    def param_knob(self, section, key, label, **kw):
        name = section[0]
        full = f"{name}_{key}"
        k = self.knob(label, self.spec(section, key), self.ch[full],
                      lambda v, f=full: self.set(f, v), **kw)
        self.knobs[full] = k
        return k

    def refresh(self):
        """Show the model again (after a paste)."""
        for key, k in self.knobs.items():
            k.set_value(self.ch[key])
        for key, b in getattr(self, "switches", {}).items():
            b.blockSignals(True)
            b.setChecked(self.ch[key])
            b.blockSignals(False)
        for key, c in getattr(self, "combos", {}).items():
            c.blockSignals(True)
            c.setCurrentIndex(self.ch[key])
            c.blockSignals(False)
        self._enable()
        for w in self.findChildren(CurveView) + self.findChildren(DynCurve):
            w.update()

    def _enable(self):
        pass


def db_fmt(v):
    return f"{v:+.1f}" if v else "0.0"


class EqPanel(StripPanel):
    """Low Cut and the 3-band parametric EQ, with the response curve on top (TotalMix's EQ
    panel)."""

    def __init__(self, title, fxs, model, kind, channels, parent=None):
        super().__init__(f"{title} — EQ", fxs, model, kind, channels, parent)
        self.switches, self.combos = {}, {}
        self.lay.addWidget(CurveView(lambda: fx.eq_filters(self.ch, self.m)))

        row = QHBoxLayout()
        row.setSpacing(px(8))
        lc_btn = on_button("Low Cut", self.ch["lc"], "High-pass filter before the EQ")
        lc_btn.toggled.connect(lambda on: (self.set("lc", on), self._enable()))
        self.switches["lc"] = lc_btn
        lc, lcl, head = box(lc_btn)
        knobs = QHBoxLayout()
        knobs.addWidget(self.param_knob(fx.LOWCUT, "freq", "Freq", log=True, fmt=fmt_hz,
                                        unit="Hz"))
        slope = self.combo(fx.SLOPES, self.ch["lc_slope"], lambda i: self.set("lc_slope", i))
        self.combos["lc_slope"] = slope
        sl = QVBoxLayout()
        sl.addWidget(caption("Slope"))
        sl.addWidget(slope)
        sl.addStretch(1)
        knobs.addLayout(sl)
        lcl.addLayout(knobs)
        self.lc_box = lc
        row.addWidget(lc)

        eq_btn = on_button("EQ", self.ch["eq"], "3-band parametric EQ")
        eq_btn.toggled.connect(lambda on: (self.set("eq", on), self._enable()))
        self.switches["eq"] = eq_btn
        eq, eql, head = box(eq_btn)
        bands = QHBoxLayout()
        bands.setSpacing(px(10))
        types1, types3 = self.m.eq_types
        for b, types in ((1, types1), (2, None), (3, types3)):
            col = QVBoxLayout()
            col.setSpacing(px(2))
            top = QHBoxLayout()
            top.addWidget(caption(f"Band {b}"))
            if types:
                t = self.combo(types, self.ch[f"eq_t{b}"],
                               lambda i, k=f"eq_t{b}": (self.set(k, i), self._enable()))
                self.combos[f"eq_t{b}"] = t
                top.addWidget(t)
            top.addStretch(1)
            col.addLayout(top)
            knobs = QHBoxLayout()
            knobs.setSpacing(0)
            knobs.addWidget(self.param_knob(fx.EQ, f"g{b}", "Gain", fmt=db_fmt, unit="dB"))
            knobs.addWidget(self.param_knob(fx.EQ, f"f{b}", "Freq", log=True, fmt=fmt_hz,
                                            unit="Hz"))
            knobs.addWidget(self.param_knob(fx.EQ, f"q{b}", "Q"))
            col.addLayout(knobs)
            bands.addLayout(col)
        eql.addLayout(bands)
        self.eq_box = eq
        row.addWidget(eq, 1)
        self.lay.addLayout(row)
        self._enable()

    def _enable(self):
        for w in self.lc_box.findChildren(ValueKnob) + self.lc_box.findChildren(QComboBox):
            w.setEnabled(self.ch["lc"])
        for w in self.eq_box.findChildren(ValueKnob) + self.eq_box.findChildren(QComboBox):
            w.setEnabled(self.ch["eq"])
        types1, types3 = self.m.eq_types
        # gain has no effect on a pass filter; TotalMix greys it too
        for b, types in ((1, types1), (3, types3)):
            name = types[min(self.ch[f"eq_t{b}"], len(types) - 1)]
            self.knobs[f"eq_g{b}"].setEnabled(self.ch["eq"] and "Pass" not in name)


class DynamicsPanel(StripPanel):
    """Dynamics (compressor and expander) and Auto Level, as TotalMix's D panel."""

    def __init__(self, title, fxs, model, kind, channels, parent=None):
        super().__init__(f"{title} — Dynamics", fxs, model, kind, channels, parent)
        self.switches, self.combos = {}, {}
        row = QHBoxLayout()
        row.setSpacing(px(8))

        dyn_btn = on_button("Dynamics", self.ch["dyn"], "Compressor and expander")
        dyn_btn.toggled.connect(lambda on: (self.set("dyn", on), self._enable()))
        self.switches["dyn"] = dyn_btn
        dyn, dl, head = box(dyn_btn)
        body = QHBoxLayout()
        body.addWidget(DynCurve(lambda: self.ch))
        grid = QGridLayout()
        grid.setSpacing(0)
        ms = lambda v: f"{round(v)}"   # noqa: E731
        items = (
            (0, 0, "cthr", "Comp Thr", dict(fmt=db_fmt, unit="dB")),
            (0, 1, "cratio", "Ratio", dict(fmt=lambda v: f"{v:.1f}:1")),
            (0, 2, "gain", "Gain", dict(fmt=db_fmt, unit="dB")),
            (1, 0, "ethr", "Exp Thr", dict(fmt=db_fmt, unit="dB")),
            (1, 1, "eratio", "Ratio", dict(fmt=lambda v: f"1:{v:.1f}")),
            (1, 2, "attack", "Attack", dict(fmt=ms, unit="ms")),
            (1, 3, "release", "Release", dict(fmt=ms, unit="ms")),
        )
        for r, c, key, label, kw in items:
            grid.addWidget(self.param_knob(fx.DYNAMICS, key, label, **kw), r, c)
        body.addLayout(grid)
        dl.addLayout(body)
        self.dyn_box = dyn
        row.addWidget(dyn, 1)

        al_btn = on_button("Auto Level", self.ch["al"], "Limiter that keeps the level under "
                           "the headroom")
        al_btn.toggled.connect(lambda on: (self.set("al", on), self._enable()))
        self.switches["al"] = al_btn
        al, all_, head = box(al_btn)
        knobs = QGridLayout()
        knobs.setSpacing(0)
        knobs.addWidget(self.param_knob(fx.AUTOLEVEL, "maxgain", "Max Gain", fmt=db_fmt,
                                        unit="dB"), 0, 0)
        knobs.addWidget(self.param_knob(fx.AUTOLEVEL, "headroom", "Headroom",
                                        fmt=lambda v: f"{v:.1f}", unit="dB"), 0, 1)
        knobs.addWidget(self.param_knob(fx.AUTOLEVEL, "rise", "Rise Time",
                                        fmt=lambda v: f"{v:.1f}s", unit="s"), 1, 0)
        all_.addLayout(knobs)
        all_.addStretch(1)
        self.al_box = al
        row.addWidget(al)
        self.lay.addLayout(row)
        self._enable()

    def _enable(self):
        for w in self.dyn_box.findChildren(ValueKnob):
            w.setEnabled(self.ch["dyn"])
        for w in self.al_box.findChildren(ValueKnob):
            w.setEnabled(self.ch["al"])
        for w in self.findChildren(DynCurve):
            w.update()


# ------------------------------------------------------------------ room EQ (UCX II)
class RoomEqPanel(FxPanel):
    """The UCX II's Room EQ on an output: delay, volume calibration and 9 bands."""

    def __init__(self, title, fxs, model, channels, parent=None):
        super().__init__(f"{title} — Room EQ", parent)
        self.fx, self.m, self.channels = fxs, model, channels
        self.lay.addWidget(CurveView(lambda: fx.room_filters(self.room, self.m)))
        top = QHBoxLayout()
        self.on = on_button("Room EQ", self.room["on"], "9-band EQ to correct the room")
        self.on.toggled.connect(lambda on: (self.set("on", on), self._enable()))
        top.addWidget(self.on)
        top.addSpacing(px(12))
        _, scale, lo, hi, default = fx.ROOM_DELAY
        top.addWidget(self.knob("Delay", (scale, lo, hi, default), self.room["delay"],
                                lambda v: self.set("delay", v), fmt=lambda v: f"{v:.1f}",
                                unit="ms"))
        _, scale, lo, hi, default = fx.VOLUME_CAL
        top.addWidget(self.knob("Vol. Cal.", (scale, lo, hi, default), self.room["volcal"],
                                lambda v: self.set("volcal", v), fmt=db_fmt, unit="dB",
                                step=0.05))
        top.addStretch(1)
        self.lay.addLayout(top)
        self.bands = QFrame()
        self.bands.setObjectName("fxbox")
        grid = QGridLayout(self.bands)
        grid.setContentsMargins(px(6), px(6), px(6), px(6))
        grid.setSpacing(0)
        types1, types3 = self.m.eq_types
        short = {"Peak": "Peak", "Low Shelf": "Lo Shelf", "High Shelf": "Hi Shelf",
                 "High Pass": "Hi Pass", "Low Pass": "Lo Pass"}
        for b in range(1, fx.ROOM_BANDS + 1):
            col = b - 1
            grid.setColumnMinimumWidth(col, px(64))
            band = QLabel(f"Band {b}")
            band.setFont(font(SANS, 9, QFont.Bold))
            band.setStyleSheet(f"color:{DIM.name()}")
            grid.addWidget(band, 0, col, Qt.AlignHCenter)
            if b in fx.ROOM_TYPE_BANDS:
                items = types1 if b == 1 else types3
                t = self.combo([short[n] for n in items], self.room[f"t{b}"],
                               lambda i, k=f"t{b}": self.set(k, i))
                t.setToolTip("Filter type")
                t.setFont(font(SANS, 9))
                t.setStyleSheet(f"QComboBox{{padding:{px(1)}px {px(3)}px;font-size:{px(9)}px}}"
                                "QComboBox::drop-down{width:0px;border:none}")
                t.setFixedWidth(px(62))
                grid.addWidget(t, 1, col, Qt.AlignHCenter)
            for r, (kind, label, kw) in enumerate((
                    ("g", "Gain", dict(fmt=db_fmt, unit="dB")),
                    ("f", "Freq", dict(fmt=fmt_hz, log=True, unit="Hz")),
                    ("q", "Q", {}))):
                key = f"{kind}{b}"
                grid.addWidget(self.knob(label, fx.room_param(self.m, key), self.room[key],
                                         lambda v, k=key: self.set(k, v), **kw), r + 2, col,
                               Qt.AlignHCenter)
        self.lay.addWidget(self.bands)
        self._enable()

    @property
    def room(self):
        return self.fx["room"][self.channels[0]]

    def set(self, key, v):
        cur = self.room[key]
        v = bool(v) if isinstance(cur, bool) else int(round(v)) if isinstance(cur, int) else \
            round(float(v), 3)
        for c in self.channels:
            self.fx["room"][c][key] = v
        self.emit()

    def _enable(self):
        self.bands.setEnabled(self.room["on"])


# ------------------------------------------------------------------ reverb and echo
class FxUnitPanel(FxPanel):
    """TotalMix's FX panel: Reverb and Echo, fed by the FX sends and heard through the FX
    returns of the outputs."""

    def __init__(self, fxs, model, parent=None):
        super().__init__("Effects — Reverb / Echo", parent)
        self.fx, self.m = fxs, model
        self.knobs = {}
        row = QHBoxLayout()
        row.setSpacing(px(8))

        rv = self.fx["reverb"]
        self.rv_on = on_button("Reverb", rv["on"])
        self.rv_on.toggled.connect(lambda on: self.set("reverb", "on", on))
        box_r, rl, head = box(self.rv_on)
        rtype = self.combo(fx.REVERB_TYPES, rv["type"],
                           lambda i: self.set("reverb", "type", i))
        head.addWidget(rtype)
        grid = QGridLayout()
        grid.setSpacing(0)
        ms = lambda v: f"{round(v)}"     # noqa: E731
        layout = (
            (0, "predelay", "PreDelay", dict(fmt=ms, unit="ms")),
            (0, "lowcut", "Low Cut", dict(fmt=fmt_hz, log=True, unit="Hz")),
            (0, "roomscale", "Room Scale", dict(fmt=lambda v: f"{v:.2f}")),
            (0, "highcut", "High Cut", dict(fmt=fmt_hz, log=True, unit="Hz")),
            (0, "smooth", "Smooth", dict(fmt=ms, unit="%")),
            (0, "width", "Width", dict(fmt=lambda v: f"{v:.2f}")),
            (1, "attack", "Attack", dict(fmt=ms, unit="ms")),
            (1, "hold", "Hold", dict(fmt=ms, unit="ms")),
            (1, "release", "Release", dict(fmt=ms, unit="ms")),
            (1, "time", "Time", dict(fmt=lambda v: f"{v:.1f}s", unit="s")),
            (1, "damp", "High Damp", dict(fmt=fmt_hz, log=True, unit="Hz")),
            (1, "volume", "Volume", dict(fmt=lambda v: fmt_level(v, fx.FX_MIN_DB), unit="dB")),
        )
        col = [0, 0]
        for r, key, label, kw in layout:
            k = self.unit_knob("reverb", fx.REVERB, key, label, **kw)
            grid.addWidget(k, r, col[r])
            col[r] += 1
        rl.addLayout(grid)
        self.rv_box = box_r
        row.addWidget(box_r)

        ec = self.fx["echo"]
        self.ec_on = on_button("Echo", ec["on"])
        self.ec_on.toggled.connect(lambda on: self.set("echo", "on", on))
        box_e, el, head = box(self.ec_on)
        head.addWidget(self.combo(fx.ECHO_TYPES, ec["type"],
                                  lambda i: self.set("echo", "type", i)))
        grid = QGridLayout()
        grid.setSpacing(0)
        grid.addWidget(self.unit_knob("echo", fx.ECHO, "delay", "Delay",
                                      fmt=lambda v: f"{v:.2f}s", unit="s"), 0, 0)
        grid.addWidget(self.unit_knob("echo", fx.ECHO, "feedback", "Feedback",
                                      fmt=ms, unit="%"), 0, 1)
        grid.addWidget(self.unit_knob("echo", fx.ECHO, "width", "Width",
                                      fmt=lambda v: f"{v:.2f}"), 1, 0)
        grid.addWidget(self.unit_knob("echo", fx.ECHO, "volume", "Volume",
                                      fmt=lambda v: fmt_level(v, fx.FX_MIN_DB), unit="dB"), 1, 1)
        hc = QHBoxLayout()
        hc.addWidget(caption("High Cut"))
        hc.addWidget(self.combo(fx.ECHO_HIGHCUTS, ec["highcut"],
                                lambda i: self.set("echo", "highcut", i)))
        el.addLayout(grid)
        el.addLayout(hc)
        el.addStretch(1)
        self.ec_box = box_e
        row.addWidget(box_e)
        self.lay.addLayout(row)
        note = QLabel("Send to the effects with FX Send in each channel's settings (⚙), and set "
                      "how much each output hears with its FX Return.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{DIM.name()}")
        self.lay.addWidget(note)
        self._enable()

    def unit_knob(self, which, section, key, label, **kw):
        for k, scale, lo, hi, default in self.m.params(section):
            if k == key:
                w = self.knob(label, (scale, lo, hi, default), self.fx[which][key],
                              lambda v, u=which, kk=key: self.set(u, kk, v), **kw)
                self.knobs[(which, key)] = w
                return w
        raise KeyError(key)

    def set(self, unit, key, v):
        cur = self.fx[unit][key]
        self.fx[unit][key] = bool(v) if isinstance(cur, bool) else \
            int(round(v)) if isinstance(cur, int) else round(float(v), 3)
        self.emit()
        self._enable()

    def _enable(self):
        rv, ec = self.fx["reverb"], self.fx["echo"]
        for (unit, key), w in self.knobs.items():
            if unit == "reverb":
                w.setEnabled(rv["on"] and fx.reverb_uses(rv["type"], key))
            else:
                w.setEnabled(ec["on"])
        for w in self.rv_box.findChildren(QComboBox):
            w.setEnabled(rv["on"])
        for w in self.ec_box.findChildren(QComboBox):
            w.setEnabled(ec["on"])


# ------------------------------------------------------------------ in channel settings
class LevelKnob(ValueKnob):
    """FX Send / FX Return: -∞ (off) to 0 dB, None in the model when off."""

    def __init__(self, label, db, parent=None):
        super().__init__(label, fx.FX_MIN_DB, 0.0, fx.FX_MIN_DB if db is None else db, -10.0
                         if label == "FX Send" else 0.0, 0.5,
                         fmt=lambda v: fmt_level(v, fx.FX_MIN_DB), unit="dB", parent=parent)

    def level(self):
        return None if self.value <= fx.FX_MIN_DB else self.value

