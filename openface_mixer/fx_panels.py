"""TotalMix FX effect panels: a strip's EQ (with Low Cut) and Dynamics (with Auto Level), the
UCX II's Room EQ, and the Reverb / Echo unit. Laid out like TotalMix's own panels, with knobs that
show their value underneath and a response curve on top.

The panels edit st["hw"]["fx"] in place (see fx.py) and emit changed(). They open inside the
mixer row, right of their strip (widgets.Row.dock), one per strip and kind; Reverb / Echo opens
at the end of the outputs row. Only the Room EQ, which is too tall for a row, is a window of
its own.
"""
import math

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QBoxLayout, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from . import fx
from .model import parse_db
from .theme import CYAN, DIM, HAIR, MONO, SANS, TEXT, font, px
from .wheel import Wheel
from .widgets import LevelEdit, label_style

BOX_STYLE = ("QFrame#fxbox{background:rgba(255,255,255,0.02);"
             "border:1px solid rgba(255,255,255,0.08);border-radius:6px}")
PANEL_STYLE = "QWidget#fxpanel{background:#040e16}" + BOX_STYLE      # a window of its own


def docked_style():
    """In a row the panel is framed like the strips, and its sections' switches are square like
    the strips' buttons: quiet when off, cyan when on."""
    return (
        "QWidget#fxpanel{background:qlineargradient(y1:0,y2:1,stop:0 rgba(4,12,18,0.62),"
        f"stop:0.7 rgba(4,12,18,0.93));border:1px solid {HAIR};border-radius:{px(6)}px}}"
        "QWidget#fxpanel:focus{border-color:rgba(42,212,255,0.6)}" + BOX_STYLE +
        "QFrame#fxbox QPushButton{background:#0a1821;color:#8aa3ab;"
        f"border:1px solid rgba(255,255,255,0.09);border-radius:{px(3)}px;"
        f"padding:{px(2)}px {px(9)}px;font-size:{px(10)}px;font-weight:600}}"
        "QFrame#fxbox QPushButton:hover{border-color:rgba(255,255,255,0.3);color:white}"
        "QFrame#fxbox QPushButton:checked{background:#2ad4ff;color:#021012;border-color:#2ad4ff}"
        "QFrame#fxbox QPushButton:focus{border-color:rgba(42,212,255,0.8)}"
        f"QFrame#fxbox QComboBox{{font-size:{px(11)}px;padding:{px(2)}px {px(6)}px}}"
        f"QToolButton#fxclose{{border:none;color:{DIM.name()};font-size:{px(14)}px}}"
        "QToolButton#fxclose:hover,QToolButton#fxclose:focus{color:white}")


def fmt_hz(v):
    return f"{v / 1000:.1f}k" if v >= 1000 else f"{round(v)}"


def fmt_level(v, lo):
    return "-∞" if v <= lo else f"{v:+.1f}" if v else "0.0"


# ------------------------------------------------------------------ knob
class ValueKnob(QWidget):
    """A TotalMix-style rotary control: label above, value below. Drag up/down (Shift for fine
    steps), scroll, or use the arrow keys; double-click resets to the default, right-click or
    Enter types a value. name is what a screen reader calls it where the label alone is not
    enough ("Band 2 gain")."""
    changed = Signal(float)

    def __init__(self, label, lo, hi, value, default, step=1.0, fmt=None, log=False, unit="",
                 name=None, parent=None):
        super().__init__(parent)
        self.label, self.lo, self.hi, self.step = label, lo, hi, step
        self.name = name or label
        self.default, self.log, self.unit = default, log, unit
        self.fmt = fmt or (lambda v: f"{v:.{self._decimals()}f}")
        self.value = self._snap(value)
        self._drag = None
        self._wheel = Wheel()
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
        value = self.fmt(self.value)
        if not value.endswith(self.unit):      # "1.5s" already says it
            value += f" {self.unit}"
        self.setToolTip(f"{self.label}: {value}\nDrag or scroll (Shift: fine) · "
                        "double-click: default · right-click: type a value")
        self.setAccessibleName(f"{self.name} {value}")

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
        n = self._wheel.steps(e)
        if n:
            self.nudge(n, e.modifiers() & Qt.ShiftModifier)

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
        """Type the value in place (a dialog would close the channel settings popup)."""
        def enter(text):
            v = parse_db(text)
            # "off" is only a value for a knob that reads -∞ at the bottom (FX Send, a volume)
            if v is None or (math.isinf(v) and self.fmt(self.lo) != "-∞"):
                return False
            self.set_value(v, True)     # kept to the knob's range

        LevelEdit(self, QRect(0, px(44), self.width(), px(18)),
                  f"{self.value:.{self._decimals()}f}", enter)

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
            p.drawText(QPointF(x + px(2), self.height() - px(3)),
                       f"{f // 1000}k" if f >= 1000 else str(f))
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
        self.setFixedWidth(px(120))          # drawn as a square in the middle
        self.setMinimumHeight(px(84))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = h = min(self.width(), self.height())
        p.translate((self.width() - w) / 2, (self.height() - h) / 2)
        p.fillRect(QRectF(0, 0, w, h), QColor("#01070b"))

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
        p.setClipRect(QRectF(0, 0, w, h))
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
    """An effect's controls, one panel per strip and kind. It goes into a mixer row
    (widgets.Row.dock) under a header with the strip's name tag, what it is and a close button.
    floating: a tool window beside the mixer instead, for the Room EQ.

    A row can be low (a small window, three rows of strips), so a panel that is higher than
    TALL pixels at 100% has its layout for a tall space and one that is lower gets set_low()."""
    changed = Signal()
    closed = Signal()
    TALL = 0

    def __init__(self, name, what, parent=None, floating=False):
        super().__init__(parent, Qt.Tool if floating else Qt.Widget)
        self.setObjectName("fxpanel")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setWindowTitle(f"{name} — {what}" if name else what)
        self.lay = QVBoxLayout(self)
        self.knobs = {}           # key -> ValueKnob, to refresh after a paste or an outside change
        self.low = False
        if floating:
            self.setStyleSheet(PANEL_STYLE)
            self.lay.setContentsMargins(px(12), px(10), px(12), px(12))
            self.lay.setSpacing(px(8))
            return
        self.setStyleSheet(docked_style())
        self.setFocusPolicy(Qt.ClickFocus)      # Esc and Ctrl+W close the panel that was clicked
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.setMinimumHeight(px(100))          # the strips set the row's height, not the panel
        self.lay.setContentsMargins(px(5), px(4), px(5), px(5))
        self.lay.setSpacing(px(4))
        head = QHBoxLayout()
        head.setSpacing(px(8))
        self.tag = QLabel()
        self.tag.setFixedHeight(px(20))
        self.tag.setFont(font(SANS, 10, QFont.DemiBold))
        head.addWidget(self.tag)
        head.addWidget(caption(what))
        self.hint = QLabel()                    # takes the width that is left
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.hint.setStyleSheet(f"color:{DIM.name()};font-size:{px(10)}px")
        head.addWidget(self.hint, 1)
        close = QToolButton()
        close.setObjectName("fxclose")
        close.setText("×")
        close.setFixedSize(px(18), px(18))
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("Close (Esc)")
        close.setAccessibleName(f"Close {self.windowTitle()}")
        close.clicked.connect(self.close)
        head.addWidget(close)
        self.lay.addLayout(head)
        self.tag.setVisible(bool(name))         # (now that it has a parent)
        self.set_tag(name)

    def set_tag(self, name, color=None):
        """Show the strip's name on the tag, over a bar in the strip's colour."""
        self.tag.setText(name)
        self.tag.setStyleSheet(label_style(color or QColor(HAIR), False) + f"padding:0 {px(6)}px;")

    def sizeHint(self):
        return QSize(super().sizeHint().width(), self.minimumHeight())

    def set_low(self, low):
        """Lay the controls out for a low row (low) or a tall one."""

    def resizeEvent(self, e):
        super().resizeEvent(e)
        low = self.height() < px(self.TALL)
        if low != self.low:
            self.low = low
            self.set_low(low)

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)

    def event(self, e):
        # with the keyboard in the panel, Esc and Ctrl+W close it instead of going to a window
        # shortcut
        if e.type() == QEvent.ShortcutOverride and (
                e.key() == Qt.Key_Escape or e.matches(QKeySequence.Close)):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape or e.matches(QKeySequence.Close):     # Close is Ctrl+W
            self.close()
        else:
            super().keyPressEvent(e)

    def emit(self):
        self.changed.emit()
        for w in self.findChildren(CurveView) + self.findChildren(DynCurve):
            w.update()

    def knob(self, label, spec, value, on_change, fmt=None, log=False, unit="", step=None,
             name=None):
        """spec: (scale, low, high, default) as in fx.py."""
        scale, lo, hi, default = spec
        k = ValueKnob(label, lo, hi, value, default, step or 1 / scale, fmt, log, unit, name)
        k.changed.connect(on_change)
        return k

    def combo(self, name, items, index, on_change):
        """name: what a screen reader calls the list."""
        c = QComboBox()
        c.setAccessibleName(name)
        c.addItems(list(items))
        c.setCurrentIndex(max(0, min(len(items) - 1, int(index))))
        c.currentIndexChanged.connect(on_change)
        return c


# ------------------------------------------------------------------ channel EQ and dynamics
class StripPanel(FxPanel):
    """Base for the EQ and Dynamics panels of a strip: edits the strip's effects in fx[kind]
    (fx.effect_channels)."""

    def __init__(self, name, what, fxs, model, kind, channels, parent=None):
        super().__init__(name, what, parent)
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
        for c in fx.effect_channels(self.kind, self.channels):
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
    panel). In a low row the curve is left of the controls."""
    TALL = 252

    def __init__(self, name, fxs, model, kind, channels, parent=None):
        super().__init__(name, "EQ", fxs, model, kind, channels, parent)
        self.switches, self.combos = {}, {}
        curve = CurveView(lambda: fx.eq_filters(self.ch, self.m))
        curve.setMinimumSize(px(220), px(72))
        curve.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.body = QBoxLayout(QBoxLayout.TopToBottom)
        self.body.setSpacing(px(6))
        self.body.addWidget(curve, 1)

        row = QHBoxLayout()
        row.setSpacing(px(6))
        lc_btn = on_button("Low Cut", self.ch["lc"], "High-pass filter before the EQ")
        lc_btn.toggled.connect(lambda on: (self.set("lc", on), self._enable()))
        self.switches["lc"] = lc_btn
        lc, lcl, head = box(lc_btn)
        self.lc_knobs = QBoxLayout(QBoxLayout.LeftToRight)   # low: the slope under the knob
        self.lc_knobs.addWidget(self.param_knob(fx.LOWCUT, "freq", "Freq", log=True, fmt=fmt_hz,
                                                unit="Hz", name="Low Cut frequency"),
                                0, Qt.AlignHCenter)
        slope = self.combo("Low Cut slope", fx.SLOPES, self.ch["lc_slope"],
                           lambda i: self.set("lc_slope", i))
        slope.setToolTip("Slope")
        self.combos["lc_slope"] = slope
        self.slope_caption = caption("Slope")
        sl = QVBoxLayout()
        sl.addWidget(self.slope_caption)
        sl.addWidget(slope)
        sl.addStretch(1)
        self.lc_knobs.addLayout(sl)
        lcl.addLayout(self.lc_knobs)
        lcl.addStretch(1)
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
                t = self.combo(f"Band {b} type", types, self.ch[f"eq_t{b}"],
                               lambda i, k=f"eq_t{b}": (self.set(k, i), self._enable()))
                self.combos[f"eq_t{b}"] = t
                top.addWidget(t)
            top.addStretch(1)
            col.addLayout(top)
            knobs = QHBoxLayout()
            knobs.setSpacing(0)
            knobs.addWidget(self.param_knob(fx.EQ, f"g{b}", "Gain", fmt=db_fmt, unit="dB",
                                            name=f"Band {b} gain"))
            knobs.addWidget(self.param_knob(fx.EQ, f"f{b}", "Freq", log=True, fmt=fmt_hz,
                                            unit="Hz", name=f"Band {b} frequency"))
            knobs.addWidget(self.param_knob(fx.EQ, f"q{b}", "Q", name=f"Band {b} Q"))
            col.addLayout(knobs)
            bands.addLayout(col)
        eql.addLayout(bands)
        eql.addStretch(1)
        self.eq_box = eq
        row.addWidget(eq)
        self.body.addLayout(row)
        self.lay.addLayout(self.body, 1)
        self._enable()

    def set_low(self, low):
        self.body.setDirection(QBoxLayout.LeftToRight if low else QBoxLayout.TopToBottom)
        self.lc_knobs.setDirection(QBoxLayout.TopToBottom if low else QBoxLayout.LeftToRight)
        self.slope_caption.setVisible(not low)

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

    def __init__(self, name, fxs, model, kind, channels, parent=None):
        super().__init__(name, "Dynamics", fxs, model, kind, channels, parent)
        self.switches, self.combos = {}, {}
        row = QHBoxLayout()
        row.setSpacing(px(6))

        dyn_btn = on_button("Dynamics", self.ch["dyn"], "Compressor and expander")
        dyn_btn.toggled.connect(lambda on: (self.set("dyn", on), self._enable()))
        self.switches["dyn"] = dyn_btn
        # the switch is over the curve, so the two rows of knobs beside them fit in a low row
        dyn = QFrame()
        dyn.setObjectName("fxbox")
        body = QHBoxLayout(dyn)
        body.setContentsMargins(px(8), px(6), px(8), px(8))
        left = QVBoxLayout()
        left.setSpacing(px(4))
        left.addWidget(dyn_btn, 0, Qt.AlignLeft)
        self.curve = DynCurve(lambda: self.ch)
        left.addWidget(self.curve, 1)
        body.addLayout(left)
        grid = QGridLayout()
        grid.setSpacing(0)
        ms = lambda v: f"{round(v)}"   # noqa: E731
        items = (
            (0, 0, "cthr", "Comp Thr", dict(fmt=db_fmt, unit="dB", name="Compressor threshold")),
            (0, 1, "cratio", "Ratio", dict(fmt=lambda v: f"{v:.1f}:1", name="Compressor ratio")),
            (0, 2, "gain", "Gain", dict(fmt=db_fmt, unit="dB", name="Dynamics gain")),
            (1, 0, "ethr", "Exp Thr", dict(fmt=db_fmt, unit="dB", name="Expander threshold")),
            (1, 1, "eratio", "Ratio", dict(fmt=lambda v: f"1:{v:.1f}", name="Expander ratio")),
            (1, 2, "attack", "Attack", dict(fmt=ms, unit="ms", name="Dynamics attack")),
            (1, 3, "release", "Release", dict(fmt=ms, unit="ms", name="Dynamics release")),
        )
        for r, c, key, label, kw in items:
            grid.addWidget(self.param_knob(fx.DYNAMICS, key, label, **kw), r, c)
        body.addLayout(grid)
        body.setAlignment(grid, Qt.AlignVCenter)
        self.dyn_box = dyn
        row.addWidget(dyn)

        al_btn = on_button("Auto Level", self.ch["al"], "Limiter that keeps the level under "
                           "the headroom")
        al_btn.toggled.connect(lambda on: (self.set("al", on), self._enable()))
        self.switches["al"] = al_btn
        al, all_, head = box(al_btn)
        knobs = QHBoxLayout()
        knobs.setSpacing(0)
        knobs.addWidget(self.param_knob(fx.AUTOLEVEL, "maxgain", "Max Gain", fmt=db_fmt,
                                        unit="dB", name="Auto Level max gain"))
        knobs.addWidget(self.param_knob(fx.AUTOLEVEL, "headroom", "Headroom",
                                        fmt=lambda v: f"{v:.1f}", unit="dB",
                                        name="Auto Level headroom"))
        knobs.addWidget(self.param_knob(fx.AUTOLEVEL, "rise", "Rise Time",
                                        fmt=lambda v: f"{v:.1f}s", unit="s",
                                        name="Auto Level rise time"))
        all_.addLayout(knobs)
        all_.addStretch(1)
        self.al_box = al
        row.addWidget(al)
        self.lay.addLayout(row, 1)
        self._enable()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # the curve is as wide as the row lets it be high
        self.curve.setFixedWidth(max(px(96), min(px(200), self.height() - px(72))))

    def _enable(self):
        for w in self.dyn_box.findChildren(ValueKnob):
            w.setEnabled(self.ch["dyn"])
        for w in self.al_box.findChildren(ValueKnob):
            w.setEnabled(self.ch["al"])
        for w in self.findChildren(DynCurve):
            w.update()


# ------------------------------------------------------------------ room EQ (UCX II)
class RoomEqPanel(FxPanel):
    """The UCX II's Room EQ on an output: delay, volume calibration and 9 bands. Three rows of
    knobs under a curve are too tall for a mixer row, so it is a window of its own."""

    def __init__(self, name, fxs, model, channels, parent=None):
        super().__init__(name, "Room EQ", parent, floating=True)
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
                                lambda v: self.set("volcal", v), unit="dB", step=0.05,
                                fmt=lambda v: f"{v:+.2f}" if v else "0.00",
                                name="Volume calibration"))
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
                t = self.combo(f"Band {b} type", [short[n] for n in items], self.room[f"t{b}"],
                               lambda i, k=f"t{b}": self.set(k, i))
                t.setToolTip("Filter type")
                t.setFont(font(SANS, 9))
                t.setStyleSheet(f"QComboBox{{padding:{px(1)}px {px(3)}px;font-size:{px(9)}px}}"
                                "QComboBox::drop-down{width:0px;border:none}")
                t.setFixedWidth(px(62))
                grid.addWidget(t, 1, col, Qt.AlignHCenter)
            for r, (kind, label, name, kw) in enumerate((
                    ("g", "Gain", "gain", dict(fmt=db_fmt, unit="dB")),
                    ("f", "Freq", "frequency", dict(fmt=fmt_hz, log=True, unit="Hz")),
                    ("q", "Q", "Q", {}))):
                key = f"{kind}{b}"
                grid.addWidget(self.knob(label, fx.room_param(self.m, key), self.room[key],
                                         lambda v, k=key: self.set(k, v), name=f"Band {b} {name}",
                                         **kw), r + 2, col, Qt.AlignHCenter)
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
    returns of the outputs. The knobs of each are in two rows, or in one in a low row."""
    TALL = 240

    def __init__(self, fxs, model, parent=None):
        super().__init__("", "Reverb / Echo", parent)
        self.fx, self.m = fxs, model
        self.knobs = {}
        self.hint.setText("fed by FX Send (⚙) · heard through each output's FX Return")
        self.hint.setToolTip("Send to the effects with FX Send in each channel's settings (⚙), "
                             "and set how much each output hears with its FX Return.")
        row = QHBoxLayout()
        row.setSpacing(px(6))

        rv = self.fx["reverb"]
        self.rv_on = on_button("Reverb", rv["on"])
        self.rv_on.toggled.connect(lambda on: self.set("reverb", "on", on))
        box_r, rl, head = box(self.rv_on)
        rtype = self.combo("Reverb type", fx.REVERB_TYPES, rv["type"],
                           lambda i: self.set("reverb", "type", i))
        head.addWidget(rtype)
        grid = QGridLayout()
        grid.setSpacing(0)
        ms = lambda v: f"{round(v)}"     # noqa: E731
        for key, label, kw in (
            ("predelay", "PreDelay", dict(fmt=ms, unit="ms")),
            ("lowcut", "Low Cut", dict(fmt=fmt_hz, log=True, unit="Hz")),
            ("roomscale", "Room Scale", dict(fmt=lambda v: f"{v:.2f}")),
            ("highcut", "High Cut", dict(fmt=fmt_hz, log=True, unit="Hz")),
            ("smooth", "Smooth", dict(fmt=ms, unit="%")),
            ("width", "Width", dict(fmt=lambda v: f"{v:.2f}")),
            ("attack", "Attack", dict(fmt=ms, unit="ms")),
            ("hold", "Hold", dict(fmt=ms, unit="ms")),
            ("release", "Release", dict(fmt=ms, unit="ms")),
            ("time", "Time", dict(fmt=lambda v: f"{v:.1f}s", unit="s")),
            ("damp", "High Damp", dict(fmt=fmt_hz, log=True, unit="Hz")),
            ("volume", "Volume", dict(fmt=lambda v: fmt_level(v, fx.FX_MIN_DB), unit="dB")),
        ):
            self.unit_knob("reverb", fx.REVERB, key, label, **kw)
        self.rv_grid = grid
        rl.addLayout(grid)
        rl.addStretch(1)
        self.rv_box = box_r
        row.addWidget(box_r)

        ec = self.fx["echo"]
        self.ec_on = on_button("Echo", ec["on"])
        self.ec_on.toggled.connect(lambda on: self.set("echo", "on", on))
        box_e, el, head = box(self.ec_on)
        head.addWidget(self.combo("Echo type", fx.ECHO_TYPES, ec["type"],
                                  lambda i: self.set("echo", "type", i)))
        grid = QGridLayout()
        grid.setSpacing(0)
        # the 802 takes the delay in 1/100 s, the UCX II in ms: show the digits that are sent
        digits = next(round(math.log10(scale)) for k, scale, *_ in self.m.params(fx.ECHO)
                      if k == "delay")
        self.unit_knob("echo", fx.ECHO, "delay", "Delay", fmt=lambda v: f"{v:.{digits}f}s",
                       unit="s")
        self.unit_knob("echo", fx.ECHO, "feedback", "Feedback", fmt=ms, unit="%")
        self.unit_knob("echo", fx.ECHO, "width", "Width", fmt=lambda v: f"{v:.2f}")
        self.unit_knob("echo", fx.ECHO, "volume", "Volume",
                       fmt=lambda v: fmt_level(v, fx.FX_MIN_DB), unit="dB")
        self.ec_grid = grid
        hc = QHBoxLayout()
        hc.addWidget(caption("High Cut"))
        hc.addWidget(self.combo("Echo high cut", fx.ECHO_HIGHCUTS, ec["highcut"],
                                lambda i: self.set("echo", "highcut", i)))
        el.addLayout(grid)
        el.addLayout(hc)
        el.addStretch(1)
        self.ec_box = box_e
        row.addWidget(box_e)
        self.lay.addLayout(row, 1)
        self.set_low(False)
        self._enable()

    def set_low(self, low):
        for unit, grid, columns in (("reverb", self.rv_grid, 6), ("echo", self.ec_grid, 2)):
            knobs = [w for (u, _key), w in self.knobs.items() if u == unit]
            for i, w in enumerate(knobs):
                grid.removeWidget(w)
                grid.addWidget(w, 0 if low else i // columns, i if low else i % columns)

    def unit_knob(self, which, section, key, label, **kw):
        for k, scale, lo, hi, default in self.m.params(section):
            if k == key:
                w = self.knob(label, (scale, lo, hi, default), self.fx[which][key],
                              lambda v, u=which, kk=key: self.set(u, kk, v),
                              name=f"{which.capitalize()} {label.lower()}", **kw)
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

