"""Custom-painted mixer controls: meter, fader, pan knob, channel strip and strip row.

All sizes go through theme.px(), so the strips are laid out at the chosen UI scale."""
import html
import math
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from .model import FADER_MIN_DB, NEG_INF, db2pos, fader_db, fmt_db, lin2db, parse_db, pos2db
from .theme import (
    CYAN, DIM, GROUP_COLOR, HAIR, KIND_COLOR, MONO, MONO_STACK, MUTED, RED, SANS, SOLO, TEXT,
    caption_html, font, icon_pixmap, px,
)
from .wheel import Wheel
from PySide6.QtGui import QIcon
from PySide6.QtGui import QFont

QWIDGETSIZE_MAX = 16777215      # Qt's "no maximum size"


class Meter(QWidget):
    FLOOR_DB = -80.0
    peak_changed = Signal(float)   # max peak (dBFS) since last reset

    def __init__(self, channels=1, parent=None):
        super().__init__(parent)
        self.levels = [0.0] * channels
        self.holds = [(0.0, 0.0)] * channels  # (level, time)
        self.clip = [False] * channels
        self.max_peak = 0.0
        self._shown_peak = None
        self.bar = px(4)                      # bar width; one pixel gap between bars
        self.horizontal = None
        self.set_horizontal(False)
        self.setToolTip("Click to reset peak / clip")

    def set_horizontal(self, on):
        """Upright beside the fader, or on its side under the name of a folded strip."""
        if on == self.horizontal:
            return
        self.horizontal = on
        across = (self.bar + 1) * len(self.levels) + 1
        self.setMinimumSize(0, 0)
        self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        if on:
            self.setFixedHeight(across)
        else:
            self.setFixedWidth(across)
            self.setMinimumHeight(px(60))

    def push(self, peaks, dt):
        now = time.monotonic()
        fall = 10 ** (-26.0 * dt / 20.0)  # 26 dB/s release
        floor = 10 ** (self.FLOOR_DB / 20.0)
        before = (self.levels[:], [h[0] for h in self.holds], self.clip[:])
        for i, p in enumerate(peaks):
            v = max(p, self.levels[i] * fall)
            v = v if v >= floor else 0.0    # below the scale: drawn as empty anyway
            self.levels[i] = v
            if v >= self.holds[i][0] or now - self.holds[i][1] > 1.5:
                self.holds[i] = (v, now)
            if p >= 0.999:
                self.clip[i] = True
            self.max_peak = max(self.max_peak, p)
        shown = round(lin2db(self.max_peak), 1) if self.max_peak > 1e-4 else None  # floor -80 dB
        if shown != self._shown_peak:
            self._shown_peak = shown
            self.peak_changed.emit(NEG_INF if shown is None else shown)
        # ~80 meters at 30 fps: repaint only the ones whose picture changed (silent ones don't)
        if (self.levels, [h[0] for h in self.holds], self.clip) != before:
            self.update()

    def reset_peak(self):
        self.clip = [False] * len(self.clip)
        self.max_peak = 0.0
        self._shown_peak = None
        self.peak_changed.emit(NEG_INF)
        self.update()

    def mousePressEvent(self, e):
        self.reset_peak()

    def _frac(self, lin):
        db = lin2db(lin)
        if db == NEG_INF or db < self.FLOOR_DB:
            return 0.0
        # same shape as the fader scale so meter and fader read alike
        return db2pos(min(0.0, db)) / db2pos(0.0)

    def paintEvent(self, e):
        p = QPainter(self)
        width, height = self.width(), self.height()
        if self.horizontal:     # drawn upright and turned: the clip lights are at the right end
            p.translate(width, 0)
            p.rotate(90)
            width, height = height, width
        b = self.bar
        clip_h = px(3)
        top, h = clip_h + px(2), height - clip_h - px(4)
        grad = QLinearGradient(0, top, 0, top + h)
        grad.setColorAt(0.0, QColor("#ff2a4f"))
        grad.setColorAt(0.06, QColor("#ffb020"))
        grad.setColorAt(0.2, QColor("#7fe9ff"))
        grad.setColorAt(0.45, QColor("#2ad4ff"))
        grad.setColorAt(1.0, QColor("#006b88"))
        for i, lv in enumerate(self.levels):
            x = 1 + i * (b + 1)
            p.fillRect(QRectF(x, top, b, h), QColor("#01070b"))
            fh = self._frac(lv) * h
            p.fillRect(QRectF(x, top + h - fh, b, fh), grad)
            hf = self._frac(self.holds[i][0]) * h
            if hf > 0:
                p.fillRect(QRectF(x, top + h - hf, b, max(1.0, px(1.5))), QColor("#f5f5f7"))
            p.fillRect(QRectF(x, 0, b, clip_h), QColor("#ff2a4f") if self.clip[i] else QColor("#0f2430"))
        # faint segment lines for a hardware feel
        p.setPen(QPen(QColor(1, 7, 11, 110), 1))
        step = px(3)
        for y in range(top + h - step, top, -step):
            p.drawLine(0, y, width, y)


class LevelEdit(QLineEdit):
    """A value typed in place, over a rect of parent (a strip's readouts, a matrix cell, a knob's
    value). Enter hands the text to on_enter, which returns False to keep typing (not a
    number); Esc or a click elsewhere leaves the value alone. Either key gives the keyboard
    back to owner."""

    def __init__(self, parent, rect, text, on_enter, owner=None):
        super().__init__(text, parent)
        self.on_enter, self.owner = on_enter, owner or parent
        self.setAlignment(Qt.AlignCenter)
        self.setContextMenuPolicy(Qt.PreventContextMenu)   # a right-click that opens it stops here
        self.setStyleSheet(f"background:#01070b;color:#eef6f8;border:1px solid "
                           f"rgba(42,212,255,0.8);border-radius:{px(3)}px;padding:0;"
                           f"font-family:{MONO_STACK};font-size:{px(9)}px")
        self.setGeometry(rect)
        self.selectAll()
        self.show()
        self.setFocus()

    def event(self, e):
        # while typing, Esc belongs to the editor, not to a shortcut of the window
        if e.type() == QEvent.ShortcutOverride and e.key() == Qt.Key_Escape:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        enter = e.key() in (Qt.Key_Return, Qt.Key_Enter)
        if enter and self.on_enter(self.text()) is False:
            self.selectAll()
        elif enter or e.key() == Qt.Key_Escape:
            self.owner.setFocus()
            self._end()
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._end()

    def _end(self):
        self.hide()
        self.deleteLater()


class Fader(QWidget):
    changed = Signal(float)
    type_requested = Signal()      # Enter: type the level (the strip shows the editor)
    SCALE = (6, 0, -5, -10, -15, -20, -30, -40, -60)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pos = db2pos(0.0)
        self.setMinimumHeight(px(60))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setFixedWidth(px(34))
        self._drag = None
        self._wheel = Wheel()
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)    # arrow keys move the fader that was clicked
        self.setToolTip("Drag, scroll or use the arrow keys · Ctrl = fine\n"
                        "Double-click, Alt-click or Home = 0 dB · End = -∞ · Enter = type a level")

    def db(self):
        return pos2db(self.pos)

    def set_db(self, db):
        self.pos = db2pos(db)
        self.update()

    def _track(self):
        return float(px(12)), float(self.height() - px(12))

    def _set_pos(self, p):
        p = max(0.0, min(1.0, p))
        if p != self.pos:
            self.pos = p
            self.update()
            self.changed.emit(self.db())

    def mousePressEvent(self, e):
        if e.modifiers() & Qt.AltModifier:
            self._set_pos(db2pos(0.0))
            return
        self._drag = (e.position().y(), self.pos)

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        top, bot = self._track()
        scale = 0.15 if e.modifiers() & Qt.ControlModifier else 1.0
        dy = self._drag[0] - e.position().y()
        self._set_pos(self._drag[1] + dy / (bot - top) * scale)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def dragged_from_off(self):
        """True during a drag that started at -∞."""
        return self._drag is not None and self._drag[1] == db2pos(NEG_INF)

    def mouseDoubleClickEvent(self, e):
        self._set_pos(db2pos(0.0))

    def wheelEvent(self, e):
        n = self._wheel.steps(e)
        if n:
            self.step(n * (0.5 if e.modifiers() & Qt.ControlModifier else 1.0))

    def keyPressEvent(self, e):
        fine = e.modifiers() & Qt.ControlModifier
        steps = {Qt.Key_Up: 0.5 if fine else 1.0, Qt.Key_Down: -0.5 if fine else -1.0,
                 Qt.Key_PageUp: 6.0, Qt.Key_PageDown: -6.0}
        if e.key() in steps:
            self.step(steps[e.key()])
        elif e.key() == Qt.Key_Home:
            self._set_pos(db2pos(0.0))
        elif e.key() == Qt.Key_End:
            self._set_pos(db2pos(NEG_INF))
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.type_requested.emit()
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.update()

    def step(self, step):
        """Move by step dB (from -∞, a step up lands on the bottom of the scale)."""
        db = self.db()
        if db == NEG_INF:
            db = FADER_MIN_DB if step > 0 else NEG_INF
        else:
            db = db + step
        self.move_to(db)

    def move_to(self, db):
        """Go to db, kept to the fader's range, as a drag there would (emits changed)."""
        self._set_pos(db2pos(fader_db(db)))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        top, bot = self._track()
        tx = px(9)   # track centre
        # printed scale between fader and meter, as in Pro Tools
        p.setFont(font(SANS, 8, QFont.Medium))
        last = None
        for db in self.SCALE:
            y = bot - db2pos(db) * (bot - top)
            if last is not None and y - last < px(10) and db != 0:
                continue   # short fader: drop marks that would overlap
            last = y
            p.setPen(QColor("#e6f3f6") if db == 0 else QColor(MUTED))
            p.drawText(QRectF(tx + px(9), y - px(6), px(15), px(12)), Qt.AlignRight | Qt.AlignVCenter,
                       str(abs(db)))
        # groove
        gw = px(3)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#01070b"))
        p.drawRoundedRect(QRectF(tx - gw / 2, top - px(4), gw, bot - top + px(8)), gw / 2, gw / 2)
        y = bot - self.pos * (bot - top)
        if self.pos > 0:
            p.setBrush(QColor(42, 212, 255, 70))
            p.drawRoundedRect(QRectF(tx - gw / 2, y, gw, bot - y + px(4)), gw / 2, gw / 2)
        # cap: flat, dark, with a cyan index line
        cw, ch = px(16), px(24)
        cap = QRectF(tx - cw / 2, y - ch / 2, cw, ch)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawRoundedRect(cap.translated(0, px(2)), px(3), px(3))
        g = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        if self.isEnabled():
            g.setColorAt(0.0, QColor("#1a3b4c"))
            g.setColorAt(0.5, QColor("#0b1d29"))
            g.setColorAt(1.0, QColor("#133243"))
        else:
            g.setColorAt(0.0, QColor("#0b1a24"))
            g.setColorAt(1.0, QColor("#08141c"))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawRoundedRect(cap, px(3), px(3))
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        for dy in (-px(7), -px(4), px(4), px(7)):
            p.drawLine(QPointF(cap.left() + px(4), y + dy), QPointF(cap.right() - px(4), y + dy))
        p.setPen(QPen(CYAN, max(1.0, px(1.5))))
        p.drawLine(QPointF(cap.left() + px(2), y), QPointF(cap.right() - px(2), y))
        if self.hasFocus():       # the keys act on this fader
            p.setPen(QPen(QColor(42, 212, 255, 200), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(cap.adjusted(-px(2), -px(2), px(2), px(2)), px(4), px(4))


class Knob(QWidget):
    """Small pan knob: dark dial, white pointer, arc from centre."""
    changed = Signal(float)
    text_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setFixedSize(px(26), px(26))
        self._drag = None
        self._wheel = Wheel()
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.set_value(0.0)

    def set_value(self, v):
        self.value = v
        self.setToolTip(f"Pan {self.text()} · drag, scroll or Left/Right · double-click "
                        "or Home to centre")
        self.text_changed.emit(self.text())
        self.update()

    def text(self):
        v = round(self.value * 64)
        return "C" if v == 0 else (f"L{-v}" if v < 0 else f"R{v}")

    def mousePressEvent(self, e):
        if e.modifiers() & Qt.AltModifier:
            self.mouseDoubleClickEvent(e)
            return
        self._drag = (e.position(), self.value)

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        d = (e.position().x() - self._drag[0].x()) - (e.position().y() - self._drag[0].y())
        scale = 0.25 if e.modifiers() & Qt.ControlModifier else 1.0
        v = max(-1.0, min(1.0, self._drag[1] + d / (80.0 * px(1)) * scale))
        if abs(v) < 0.02:
            v = 0.0
        if v != self.value:
            self.set_value(v)
            self.changed.emit(v)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.set_value(0.0)
        self.changed.emit(0.0)

    def wheelEvent(self, e):
        n = self._wheel.steps(e)
        if n:
            self.nudge(n)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Right, Qt.Key_Up):
            self.nudge(1)
        elif e.key() in (Qt.Key_Left, Qt.Key_Down):
            self.nudge(-1)
        elif e.key() == Qt.Key_Home:
            self.mouseDoubleClickEvent(e)
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.update()

    def nudge(self, n):
        v = max(-1.0, min(1.0, self.value + n / 32))
        self.set_value(v)
        self.changed.emit(v)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = px(11)
        ring = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setPen(QPen(QColor("#01070b"), px(2), Qt.SolidLine, Qt.RoundCap))
        p.drawArc(ring, 225 * 16, -270 * 16)
        if self.value:
            p.setPen(QPen(CYAN, px(2), Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring, 90 * 16, int(-self.value * 135 * 16))
        br = px(8)
        body = QRectF(c.x() - br, c.y() - br, 2 * br, 2 * br)
        g = QLinearGradient(body.topLeft(), body.bottomLeft())
        g.setColorAt(0, QColor("#0f2838"))
        g.setColorAt(1, QColor("#040e16"))
        p.setBrush(g)
        p.setPen(QPen(QColor(42, 212, 255, 200) if self.hasFocus() else QColor(255, 255, 255, 40),
                      1))
        p.drawEllipse(body)
        a = math.radians(90 - self.value * 135)
        p.setPen(QPen(QColor("#f5f5f7"), px(2), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(c.x() + px(3) * math.cos(a), c.y() - px(3) * math.sin(a)),
                   QPointF(c.x() + px(7) * math.cos(a), c.y() - px(7) * math.sin(a)))


def small_button(text, color="#2ad4ff", fg="#021012", tip="", width=26):
    """The square strip buttons (S / M / ST): quiet when off, filled with their colour when on."""
    b = QToolButton()
    b.setText(text)
    b.setCheckable(True)
    b.setFixedSize(QSize(px(width), px(20)))
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFont(font(SANS, 9, QFont.DemiBold))
    b.setStyleSheet(
        f"QToolButton{{background:#0a1821;color:#8aa3ab;border:1px solid rgba(255,255,255,0.09);"
        f"border-radius:{px(3)}px}}"
        "QToolButton:hover{background:#12293a;color:white;border-color:rgba(255,255,255,0.18)}"
        "QToolButton:pressed{background:#1a3b4c}"
        "QToolButton:focus{border-color:rgba(42,212,255,0.8)}"
        f"QToolButton:checked{{background:{color};color:{fg};border-color:{color}}}"
        "QToolButton:checked:pressed{background:#d9eef2}"
        "QToolButton:disabled{color:#2a4552}")
    return b


def fx_button_style(on, opened=False):
    """EQ / D: quiet when the effect is off, lit when it is on (they are not toggles: a click
    opens the panel, another closes it). opened: the panel is showing, so the button has a
    white edge."""
    lit = "background:#2ad4ff;color:#021012;border-color:#2ad4ff;" if on else ""
    if opened:
        lit += "border-color:#f5f8fa;" + ("" if on else "color:#f5f8fa;background:#12293a;")
    return (f"QToolButton{{background:#0a1821;color:#8aa3ab;border:1px solid "
            f"rgba(255,255,255,0.09);border-radius:{px(3)}px;{lit}}}"
            "QToolButton:hover{border-color:rgba(255,255,255,0.3);color:white}"
            "QToolButton:focus{border-color:rgba(42,212,255,0.8)}")


def fx_button(text, tip):
    b = QToolButton()
    b.setText(text)
    b.setFixedSize(QSize(px(26), px(18)))
    b.setToolTip(f"{tip} — click to open or close (lit while on)")
    b.setCursor(Qt.PointingHandCursor)
    b.setFont(font(SANS, 9, QFont.DemiBold))
    return b


def label_style(col, sel):
    """Channel name tag: dark tag, white text, a bar in the channel's colour underneath; the
    selected submix turns white."""
    base = (f"border:none;border-radius:{px(3)}px;padding:0 {px(2)}px;"
            f"border-bottom:{px(2)}px solid {col.name()};")
    if sel:
        return base + "background:#f5f8fa;color:#021012;"
    return base + "background:#01070b;color:#eef6f8;"


def lcd_style(bg="rgba(1,7,11,0.7)", fg="#9cc7cf"):
    return (f"background:{bg};color:{fg};border:1px solid {HAIR};border-radius:{px(3)}px;"
            f"font-family:{MONO_STACK};font-size:{px(8)}px;padding:0")


def lcd_label(text=""):
    l = QLabel(text)
    l.setAlignment(Qt.AlignCenter)
    l.setFixedHeight(px(15))
    l.setMinimumWidth(0)
    l.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    l.setStyleSheet(lcd_style())
    return l


class NameTag(QPushButton):
    """The strip's name tag; double-click renames the channel."""
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit()
        else:
            super().mouseDoubleClickEvent(e)


LEVEL_TIP = "Fader level (dB) · double-click to type a level"


class Strip(QFrame):
    """One mixer channel strip (mono or stereo), compact like a Pro Tools mix window strip:
    name tag, pan, solo/mute/stereo, fader with the scale beside its meter, and the level and
    peak readouts at the bottom.

    key identifies the strip in the model ("in:0", "play:2", "out:16"); see model.strip_key().
    """
    WIDTH = 62
    FOLDED = 42                          # height of a folded strip: its name tag over its meter
    clicked = Signal()
    flags_changed = Signal()             # folded: its solo, mute or clip mark changed
    rename_requested = Signal()
    context_requested = Signal(object)   # global QPoint, for the right-click menu
    settings_requested = Signal(object)  # global QPoint below the settings button
    eq_requested = Signal()              # the EQ button (TotalMix FX: Low Cut and EQ)
    dyn_requested = Signal()             # the D button (Dynamics and Auto Level)

    def __init__(self, key, title, channels, kind, show_pan=True, show_stereo=False,
                 show_solo=False, show_settings=False, standard=None, color=None, icon=None,
                 show_fx=False, show_cue=False, parent=None):
        super().__init__(parent)
        self.key = key
        self.name = title                    # shown name (custom or standard), unshortened
        self.standard = standard or title    # the standard name, e.g. "AD1 3/4"
        self.kind = kind
        self.channels = channels
        self.group = 0
        self.color = QColor(color) if color else None   # the channel's own colour, if given
        self.icon = icon                                # icon name (theme.CHANNEL_ICONS) or None
        self._with_settings = show_settings
        self._selected = False
        self.folded = False
        self.clipped = False                 # the peak readout is red (until the meter is clicked)
        self._flags = None                   # (solo, mute, clipped) as a folded strip shows them
        self.width_px = px(self.WIDTH)
        self.setFixedWidth(self.width_px)
        self.setObjectName("strip")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(3), px(4), px(3), px(4))
        lay.setSpacing(px(3))

        self.title = NameTag()
        self.title.setFixedHeight(px(20))
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.title.setFont(font(SANS, 10, QFont.DemiBold))
        self.title.setCursor(Qt.PointingHandCursor if kind == "out" else Qt.ArrowCursor)
        self.title.clicked.connect(self.clicked.emit)
        self.title.double_clicked.connect(self.rename_requested.emit)
        # channel settings (TotalMix's wrench): phase, level, 48V, ... where the device has them
        self.settings_btn = QToolButton()
        self.settings_btn.setText("⚙")
        self.settings_btn.setFixedSize(QSize(px(14), px(20)))
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setToolTip("Channel settings")
        self.settings_btn.clicked.connect(lambda: self.settings_requested.emit(
            self.settings_btn.mapToGlobal(self.settings_btn.rect().bottomLeft())))
        self.set_settings_active(False)
        title_row = QHBoxLayout()
        title_row.setSpacing(1)
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.settings_btn)
        self.settings_btn.setVisible(show_settings)
        lay.addLayout(title_row)

        # pan knob with its value beside it, as Pro Tools shows "<64" / "64>"
        self.pan = Knob()
        self.pan_text = QLabel(self.pan.text())
        self.pan_text.setFont(font(MONO, 8))
        self.pan_text.setStyleSheet("color:#7d9299")
        self.pan.text_changed.connect(self.pan_text.setText)
        if show_pan:
            knob_row = QHBoxLayout()
            knob_row.setSpacing(px(2))
            knob_row.addStretch()
            knob_row.addWidget(self.pan)
            knob_row.addWidget(self.pan_text)
            knob_row.addStretch()
            lay.addLayout(knob_row)
        else:
            self.pan.hide()
            self.pan_text.hide()

        btns = QHBoxLayout()
        btns.setSpacing(px(2))
        btns.addStretch()
        w = 17 if show_solo else 26   # three buttons have to fit the strip width
        self.solo = small_button("S", SOLO.name(), "#0b0f1c", width=w,
                                 tip="Solo in the current submix (solo-in-place)")
        self.solo.setVisible(show_solo)
        btns.addWidget(self.solo)
        self.mute = small_button("M", "#2ad4ff", tip="Mute", width=w)
        self._mute_style = self.mute.styleSheet()
        self._group_muted = 0
        btns.addWidget(self.mute)
        # an output's Cue: its submix plays on Main Out (TotalMix's Cue)
        self.cue = small_button("Cue", SOLO.name(), "#0b0f1c", width=w,
                                tip="Hear this output's submix on Main Out")
        self.cue.setVisible(show_cue)
        btns.addWidget(self.cue)
        self.stereo = small_button("ST", "#ffffff", "#0b0f1c", tip="Stereo-link this channel pair",
                                   width=w)
        self.stereo.setVisible(show_stereo)
        btns.addWidget(self.stereo)
        btns.addStretch()
        lay.addLayout(btns)

        # TotalMix FX: EQ and D open the strip's effect panels and light while an effect is on
        self.eq_btn = fx_button("EQ", "Low Cut and EQ")
        self.eq_btn.clicked.connect(self.eq_requested.emit)
        self.dyn_btn = fx_button("D", "Dynamics and Auto Level")
        self.dyn_btn.clicked.connect(self.dyn_requested.emit)
        if show_fx:
            fx_row = QHBoxLayout()
            fx_row.setSpacing(px(2))
            fx_row.addStretch()
            fx_row.addWidget(self.eq_btn)
            fx_row.addWidget(self.dyn_btn)
            fx_row.addStretch()
            lay.addLayout(fx_row)
        else:
            self.eq_btn.hide()
            self.dyn_btn.hide()
        self.set_fx_active(False, False)

        self._mid = mid = QHBoxLayout()
        mid.setSpacing(1)
        mid.setContentsMargins(0, 0, 0, 0)
        self.fader = Fader()
        self.meter = Meter(len(channels))
        mid.addStretch()
        mid.addWidget(self.fader)
        mid.addWidget(self.meter)
        mid.addStretch()
        lay.addLayout(mid, 1)

        readouts = QHBoxLayout()
        readouts.setSpacing(px(2))
        self.value = lcd_label("0.0")
        self.value.setToolTip(LEVEL_TIP)
        self._limited = ""
        self.peak = lcd_label("-∞")
        self.peak.setToolTip("Peak level (dBFS) — click the meter to reset")
        readouts.addWidget(self.value)
        readouts.addWidget(self.peak)
        lay.addLayout(readouts)

        self.fader.changed.connect(lambda db: self.value.setText(fmt_db(db)))
        self.fader.type_requested.connect(self.type_level)
        self.meter.peak_changed.connect(self._show_peak)
        for b in (self.solo, self.mute, self.cue):
            b.toggled.connect(lambda _on: self.show_flags())
        self._set_title()
        self.set_selected(False)

    def _set_title(self):
        """Shorten a long (custom) name to fit the tag beside its icon; the tooltip has it in
        full."""
        with_settings = self._with_settings and not self.folded
        width = self.width_px - px(10) - (px(15) if with_settings else 0)
        if self.icon:
            width -= px(16)
        self.title.setText(QFontMetrics(self.title.font()).elidedText(self.name, Qt.ElideRight,
                                                                      width))
        name = self.name if self.name == self.standard else f"{self.name} ({self.standard})"
        action = ("Click to edit this output's submix · " if self.kind == "out" else
                  "Click to unfold the row · " if self.folded else "")
        self.title.setToolTip(f"{name}\n{action}Double-click to rename · right-click for more")
        # names for screen readers (Orca reads these through AT-SPI)
        self.title.setAccessibleName(self.name)
        self.fader.setAccessibleName(f"{self.name} fader")
        self.pan.setAccessibleName(f"{self.name} pan")
        self.solo.setAccessibleName(f"Solo {self.name}")
        self.mute.setAccessibleName(f"Mute {self.name}")
        self.cue.setAccessibleName(f"Cue {self.name}")
        self.stereo.setAccessibleName(f"Stereo-link {self.name}")
        self.settings_btn.setAccessibleName(f"{self.name} settings")
        self.eq_btn.setAccessibleName(f"{self.name} EQ")
        self.dyn_btn.setAccessibleName(f"{self.name} dynamics")

    def _show_peak(self, db):
        self.peak.setText(fmt_db(db) if db != NEG_INF else "-∞")
        hot = db != NEG_INF and db >= -0.05
        self.peak.setStyleSheet(lcd_style("#ff2a4f", "white") if hot else lcd_style())
        if hot != self.clipped:
            self.clipped = hot
            self.show_flags()

    def set_folded(self, on):
        """Folded (see Row), the strip is its name tag over its meter laid on its side; the
        controls are put away."""
        if on == self.folded:
            return
        self.folded = on
        if on:
            for edit in self.findChildren(LevelEdit):   # a level being typed: left as it was
                edit._end()
            self._put_away = [w for w in (
                self.settings_btn, self.pan, self.pan_text, self.solo, self.mute, self.cue,
                self.stereo, self.eq_btn, self.dyn_btn, self.fader, self.value, self.peak)
                if not w.isHidden()]
        for w in self._put_away:
            w.setVisible(not on)
        self.meter.set_horizontal(on)
        self._mid.setStretchFactor(self.meter, int(on))
        self.setMinimumHeight(px(self.FOLDED) if on else 0)
        self.setMaximumHeight(px(self.FOLDED) if on else QWIDGETSIZE_MAX)
        self.title.setCursor(Qt.PointingHandCursor if on or self.kind == "out"
                             else Qt.ArrowCursor)
        self._set_title()
        self._flags = None
        self.show_flags()
        if not on:
            self.set_selected(self._selected)

    def show_flags(self):
        """A folded strip shows what its buttons and readouts would: an amber edge while it is
        soloed or cued, a red one once it clipped, a dimmed name while it is muted."""
        flags = (self.solo.isChecked() or self.cue.isChecked(), self.mute.isChecked(),
                 self.clipped)
        if self.folded and flags != self._flags:
            self._flags = flags
            self.set_selected(self._selected)
            self.flags_changed.emit()

    def set_group_muted(self, group):
        """Outline the mute button while mute group group (0: none) silences this channel in
        the submix being edited."""
        if group == self._group_muted:
            return
        self._group_muted = group
        self.mute.setStyleSheet(self._mute_style + (
            "QToolButton:!checked{border-color:#2ad4ff;color:#2ad4ff}" if group else ""))
        self.mute.setToolTip(f"Mute · muted in this submix by mute group {group}" if group
                             else "Mute")

    def set_settings_active(self, on):
        """Light the settings button while a channel setting (phase, 48V, ...) is on."""
        self.settings_btn.setStyleSheet(
            f"QToolButton{{background:#0a1821;border:1px solid rgba(255,255,255,0.09);"
            f"border-radius:{px(3)}px;font-size:{px(10)}px;"
            f"color:{RED.name() if on else '#7d9299'}}}"
            "QToolButton:hover{background:#12293a;color:white}")

    def set_fx_active(self, eq, dyn, opened=()):
        """Light EQ and D while their effect is on. opened: those of "eq" and "dyn" whose panel
        is showing."""
        for which, b, on in (("eq", self.eq_btn, eq), ("dyn", self.dyn_btn, dyn)):
            b.setProperty("on", on)
            b.setStyleSheet(fx_button_style(on, which in opened))

    def set_fader(self, db):
        self.fader.set_db(db)
        self.value.setText(fmt_db(db))

    def set_limited(self, why):
        """Mark the level readout, as a clipped peak, while the hardware can't play the level
        the fader shows (why: the tooltip saying so, "" when it can)."""
        if why != self._limited:
            self._limited = why
            self.value.setStyleSheet(lcd_style("#ff2a4f", "white") if why else lcd_style())
            self.value.setToolTip(why or LEVEL_TIP)

    def mouseDoubleClickEvent(self, e):
        if (e.button() == Qt.LeftButton and self.value.isVisible()
                and self.value.geometry().contains(e.position().toPoint())):
            self.type_level()
        else:
            super().mouseDoubleClickEvent(e)

    def type_level(self):
        """Type the fader's level over the readouts; it is applied as a drag to it would be."""
        def enter(text):
            db = parse_db(text)
            if db is None:
                return False
            self.fader.move_to(db)

        LevelEdit(self, self.value.geometry() | self.peak.geometry(), self.value.text(), enter,
                  self.fader)

    def contextMenuEvent(self, e):
        self.context_requested.emit(e.globalPos())

    def set_group(self, group):
        self.group = group or 0
        self.setToolTip(f"Fader group {self.group} (hold Shift to move this fader alone)"
                        if self.group else "")
        self.set_selected(self._selected)

    def set_color(self, color):
        """color: a QColor / "#rrggbb", or None for the row's default colour."""
        self.color = QColor(color) if color else None
        self.set_selected(self._selected)

    def set_icon(self, icon):
        """icon: a name from theme.CHANNEL_ICONS, or None."""
        self.icon = icon
        self._set_title()
        self.set_selected(self._selected)

    def _apply_icon(self, sel):
        size = px(13)
        pm = icon_pixmap(self.icon, "#021012" if sel else "#eef6f8", size) if self.icon else None
        self.title.setIcon(QIcon(pm) if pm else QIcon())
        self.title.setIconSize(QSize(size, size))

    def tag_color(self):
        return self.color or KIND_COLOR[self.kind]

    def set_selected(self, sel):
        self._selected = sel
        col = self.tag_color()
        # ink panel with a hairline edge, as the dashboard's tiles; selected strip gets a cyan edge
        border = "rgba(42,212,255,0.6)" if sel else HAIR
        top_border = (f"border-top:{px(2)}px solid {GROUP_COLOR[self.group].name()};"
                      if self.group else "")
        tag = label_style(col, sel)
        if self.folded:
            if self.clipped or self.solo.isChecked() or self.cue.isChecked():
                border = (RED if self.clipped else SOLO).name()
            if self.mute.isChecked() and not sel:
                tag += f"color:{MUTED};"
        self._apply_icon(sel)
        self.setStyleSheet(
            f"QFrame#strip{{background:qlineargradient(y1:0,y2:1,stop:0 rgba(4,12,18,0.62),"
            f"stop:0.7 rgba(4,12,18,0.93));border:1px solid {border};{top_border}"
            f"border-radius:{px(6)}px}}"
            f"QPushButton{{{tag}}}")


class RowTitle(QAbstractButton):
    """A row's title, and the button that folds the row (checked) or unfolds it: a mark that
    points down while the row is open and right while it is folded, a short bar in the row's
    colour and the title in tracked caps. Space and Enter work as a click."""

    def __init__(self, title, color, parent=None):
        super().__init__(parent)
        self.color = color
        self.setText(title.upper())
        f = font(SANS, 9, QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, px(2.4))
        self.setFont(f)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)          # repaint when the pointer comes and goes
        self.setAccessibleName(title)
        self.setToolTip("Fold or unfold this row (Space or Enter)")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(px(28) + self.fontMetrics().horizontalAdvance(self.text()), px(16))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = self.height() / 2
        p.setPen(QPen(QColor("white") if self.underMouse() else DIM, max(1.0, px(1.5)),
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, a, b = px(6), px(3), px(1.5)         # the mark's centre, half its span and depth
        p.drawPolyline(
            [QPointF(x - b, y - a), QPointF(x + b, y), QPointF(x - b, y + a)] if self.isChecked()
            else [QPointF(x - a, y - b), QPointF(x, y + b), QPointF(x + a, y - b)])
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawRoundedRect(QRectF(px(14), y - px(5), px(3), px(10)), px(1), px(1))
        p.setPen(DIM)
        p.drawText(QRectF(px(25), 0, self.width(), self.height()), Qt.AlignVCenter, self.text())
        if self.hasFocus():
            p.setPen(QPen(QColor(42, 212, 255, 200), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), px(3), px(3))


class Row(QWidget):
    """One row of strips under its title. A click on the title folds the row to its strips'
    names and meters, so the other rows get its height; another click unfolds it.

    An effects panel can be docked in the row, right of its strip: the strips after it move
    right and the row scrolls. A panel is a widget with a closed signal that deletes itself
    when it is closed (fx_panels.FxPanel)."""
    fold_changed = Signal()

    def __init__(self, kind, title, subtitle, color, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(4))
        self.kind = kind
        self.title = title
        self.folded = False
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(px(8))
        self.fold_btn = RowTitle(title, color)
        self.fold_btn.toggled.connect(self.set_folded)
        head.addWidget(self.fold_btn)
        self.head = QLabel()                  # the hint beside the title
        self.head.setTextFormat(Qt.RichText)
        # cut off at the row's end when it is long: names never widen the window
        self.head.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.set_subtitle(subtitle)
        head.addWidget(self.head, 1)
        self.head_row = head                  # the window may add controls at its end
        lay.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(self.scroll, 1)
        self.strips = []
        self.docks = {}             # docked panel -> key of the strip it is right of, or None
        self._stretch()

    def set_subtitle(self, text):
        self._subtitle = text
        self._show_head()

    def _show_head(self):
        """The hint beside the title. A folded row names the strips in it that are soloed or
        have clipped, which may be scrolled out of view."""
        text = self._subtitle
        if self.folded:
            if self.kind != "out":
                text = "folded · click a name to unfold" if self.strips else "folded"
            for word, color, strips in (
                    ("Solo", SOLO, [s for s in self.strips if s.solo.isChecked()]),
                    ("Cue", SOLO, [s for s in self.strips if s.cue.isChecked()]),
                    ("Clip", RED, [s for s in self.strips if s.clipped])):
                if strips:
                    names = ", ".join(html.escape(s.title.text()) for s in strips[:3])
                    more = f" +{len(strips) - 3}" if len(strips) > 3 else ""
                    text += (f"{'&nbsp;' * 5}{caption_html('■ ' + word, color)}&nbsp;"
                             f"<span style='color:{TEXT.name()}'>{names}{more}</span>")
        self.head.setText(f"<span style='color:{MUTED};font-size:{px(10)}px'>{text}</span>")

    def set_folded(self, on):
        if on == self.folded:
            return
        self.folded = on
        self.fold_btn.setChecked(on)
        for panel in list(self.docks):      # a folded row has no room for a panel
            panel.close()
        for s in self.strips:
            s.set_folded(on)
        self._stretch()
        self._fit()
        self._show_head()
        self.fold_changed.emit()

    def _stretch(self):
        """Open rows share the height that folded ones leave."""
        policy = QSizePolicy(QSizePolicy.Preferred,
                             QSizePolicy.Fixed if self.folded else QSizePolicy.Preferred)
        policy.setVerticalStretch(0 if self.folded else 1)
        self.setSizePolicy(policy)

    def _fit(self):
        """A folded row is as high as its folded strips, plus the scroll bar when they don't
        all fit."""
        if not self.folded:
            self.scroll.setMinimumHeight(0)
            self.scroll.setMaximumHeight(QWIDGETSIZE_MAX)
            return
        bar = self.scroll.horizontalScrollBar().sizeHint().height()
        inner = self.scroll.widget()
        wide = inner is not None and inner.sizeHint().width() > self.scroll.width()
        self.scroll.setFixedHeight(px(Strip.FOLDED) + (bar if wide else 0))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def dock(self, panel, key=None, first=False):
        """Put a panel in the row: right of the strip with that key, after the panels that are
        there (first: before them), or at the end of the row without a key. The row scrolls
        to show the panel and its strip."""
        # kept in the order they stand in, for set_strips() to put them back
        self.docks = {panel: key, **self.docks} if first else {**self.docks, panel: key}
        panel.closed.connect(lambda: self.docks.pop(panel, None))
        self._place(panel, first)
        panel.show()
        QTimer.singleShot(0, lambda: self.reveal(panel))    # once the row is laid out again

    def _place(self, panel, first=False):
        lay = self.scroll.widget().layout()
        strip = next((s for s in self.strips if s.key == self.docks[panel]), None)
        if strip is None:
            at = lay.count() - 1                            # before the stretch
        else:
            at = lay.indexOf(strip) + 1
            while not first and self.docks.get(lay.itemAt(at).widget()) == strip.key:
                at += 1
        lay.insertWidget(at, panel)

    def reveal(self, panel):
        """Scroll sideways as far as it takes to show the panel and, before all, its strip (a
        panel without one: its left end)."""
        if panel in self.docks:
            first = next((s for s in self.strips if s.key == self.docks[panel]), panel)
            bar = self.scroll.horizontalScrollBar()
            end = panel.x() + panel.width() + px(4) - self.scroll.viewport().width()
            bar.setValue(min(first.x() - px(4), max(bar.value(), end)))

    def set_strips(self, strips):
        for panel in self.docks:            # the panels outlive the strips' container
            panel.setParent(self)
        inner = QWidget()
        hl = QHBoxLayout(inner)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(px(4))
        for s in strips:
            s.set_folded(self.folded)
            s.flags_changed.connect(self._show_head)
            if self.kind != "out":          # (an output's name selects its submix)
                s.clicked.connect(lambda: self.set_folded(False))
            hl.addWidget(s)
        if not strips:      # every channel of the row is hidden: say how to get them back
            empty = QLabel(f"No {self.title.lower()} shown. Right-click here to show hidden "
                           "channels, or turn groups back on under Settings > Display.")
            empty.setObjectName("empty")
            empty.setAttribute(Qt.WA_TransparentForMouseEvents)   # right-click reaches the page
            hl.addSpacing(px(14))
            hl.addWidget(empty)
        hl.addStretch()
        at = self.scroll.horizontalScrollBar().value()
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(inner)
        self.strips = strips
        keys = {s.key for s in strips} | {None}
        for panel, key in list(self.docks.items()):
            if key in keys:
                self._place(panel)
                panel.show()
            else:                           # its strip is gone
                panel.close()
        inner.adjustSize()                  # so the row can go back to where it was scrolled to
        self.scroll.horizontalScrollBar().setValue(at)
        self._fit()
        self._show_head()
