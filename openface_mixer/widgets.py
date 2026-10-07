"""Custom-painted mixer controls: meter, fader, pan knob, channel strip and strip row."""
import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout,
    QWidget,
)

from .model import FADER_MAX_DB, FADER_MIN_DB, NEG_INF, db2pos, fmt_db, lin2db, pos2db
from .theme import ACCENT, DIM, GROUP_COLOR, KIND_COLOR, MONO, SOLO


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
        self.setFixedWidth(6 * channels + 2)
        self.setMinimumHeight(110)
        self.setToolTip("Click to reset peak / clip")

    def push(self, peaks, dt):
        now = time.monotonic()
        fall = 10 ** (-26.0 * dt / 20.0)  # 26 dB/s release
        for i, p in enumerate(peaks):
            v = max(p, self.levels[i] * fall)
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
        top, h = 5, self.height() - 7
        grad = QLinearGradient(0, top, 0, top + h)
        grad.setColorAt(0.0, QColor("#ff453a"))
        grad.setColorAt(0.06, QColor("#ffd60a"))
        grad.setColorAt(0.22, QColor("#b5e550"))
        grad.setColorAt(0.45, QColor("#34c759"))
        grad.setColorAt(1.0, QColor("#1f8a3c"))
        for i, lv in enumerate(self.levels):
            x = 1 + i * 6
            p.fillRect(QRectF(x, top, 5, h), QColor("#0d0d0e"))
            fh = self._frac(lv) * h
            p.fillRect(QRectF(x, top + h - fh, 5, fh), grad)
            hf = self._frac(self.holds[i][0]) * h
            if hf > 0:
                p.fillRect(QRectF(x, top + h - hf, 5, 1.5), QColor("#f5f5f7"))
            p.fillRect(QRectF(x, 0, 5, 3), QColor("#ff453a") if self.clip[i] else QColor("#3a3a3d"))
        # faint segment lines for a hardware feel
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        for y in range(top + h - 3, top, -3):
            p.drawLine(0, y, self.width(), y)


class Fader(QWidget):
    changed = Signal(float)
    SCALE = (6, 0, -5, -10, -15, -20, -30, -40, -60)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pos = db2pos(0.0)
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setFixedWidth(45)
        self._drag = None
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Drag or scroll · Ctrl = fine · Double-click or Alt-click = 0 dB")

    def db(self):
        return pos2db(self.pos)

    def set_db(self, db):
        self.pos = db2pos(db)
        self.update()

    def _track(self):
        return 14.0, self.height() - 14.0

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

    def mouseDoubleClickEvent(self, e):
        self._set_pos(db2pos(0.0))

    def wheelEvent(self, e):
        db = self.db()
        step = 0.5 if e.modifiers() & Qt.ControlModifier else 1.0
        step *= 1 if e.angleDelta().y() > 0 else -1
        if db == NEG_INF:
            db = FADER_MIN_DB if step > 0 else NEG_INF
        else:
            db = db + step
            if db < FADER_MIN_DB:
                db = NEG_INF
        self._set_pos(db2pos(min(FADER_MAX_DB, db)))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        top, bot = self._track()
        tx = 12.0  # track centre
        # printed scale on the right (Logic omits the minus signs)
        f = QFont(self.font())
        f.setPointSizeF(6.5)
        p.setFont(f)
        for db in self.SCALE:
            y = bot - db2pos(db) * (bot - top)
            p.setPen(QColor("#e0e0e4") if db == 0 else QColor("#76767c"))
            p.drawText(QRectF(tx + 13, y - 6, 17, 12), Qt.AlignRight | Qt.AlignVCenter, str(abs(db)))
        # groove
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0b0b0c"))
        p.drawRoundedRect(QRectF(tx - 2, top - 4, 4, bot - top + 8), 2, 2)
        # fill from bottom to the cap, like Logic's level line
        y = bot - self.pos * (bot - top)
        if self.pos > 0:
            p.setBrush(QColor(255, 255, 255, 38))
            p.drawRoundedRect(QRectF(tx - 2, y, 4, bot - y + 4), 2, 2)
        # cap: brushed-metal puck with grip lines
        cap = QRectF(tx - 10, y - 15, 20, 30)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawRoundedRect(cap.translated(0, 2), 4, 4)
        g = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        if self.isEnabled():
            g.setColorAt(0.0, QColor("#f2f2f4"))
            g.setColorAt(0.45, QColor("#b9b9be"))
            g.setColorAt(0.5, QColor("#9d9da3"))
            g.setColorAt(1.0, QColor("#c8c8cd"))
        else:
            g.setColorAt(0.0, QColor("#5a5a5e"))
            g.setColorAt(1.0, QColor("#444448"))
        p.setBrush(g)
        p.setPen(QPen(QColor("#141415"), 1))
        p.drawRoundedRect(cap, 4, 4)
        p.setPen(QPen(QColor(60, 60, 64, 160), 1))
        for dy in (-9, -6, 6, 9):
            p.drawLine(QPointF(cap.left() + 5, y + dy), QPointF(cap.right() - 5, y + dy))
        p.setPen(QPen(QColor("#1c1c1e"), 2))
        p.drawLine(QPointF(cap.left() + 2, y), QPointF(cap.right() - 2, y))


class Knob(QWidget):
    """Logic-style pan knob: dark dial, white pointer, arc from centre."""
    changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setFixedSize(34, 34)
        self._drag = None
        self.setCursor(Qt.PointingHandCursor)
        self.set_value(0.0)

    def set_value(self, v):
        self.value = v
        self.setToolTip(f"Pan {self.text()} — drag, double-click to centre")
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
        v = max(-1.0, min(1.0, self._drag[1] + d / 80.0 * scale))
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
        v = max(-1.0, min(1.0, self.value + (1 if e.angleDelta().y() > 0 else -1) / 32))
        self.set_value(v)
        self.changed.emit(v)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = 14.0
        ring = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setPen(QPen(QColor("#121213"), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(ring, 225 * 16, -270 * 16)
        if self.value:
            p.setPen(QPen(QColor("#8fc1ff"), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring, 90 * 16, int(-self.value * 135 * 16))
        body = QRectF(c.x() - 10, c.y() - 10, 20, 20)
        g = QLinearGradient(body.topLeft(), body.bottomLeft())
        g.setColorAt(0, QColor("#5b5b60"))
        g.setColorAt(1, QColor("#2b2b2e"))
        p.setBrush(g)
        p.setPen(QPen(QColor("#111"), 1))
        p.drawEllipse(body)
        a = math.radians(90 - self.value * 135)
        p.setPen(QPen(QColor("#f5f5f7"), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(c.x() + 3 * math.cos(a), c.y() - 3 * math.sin(a)),
                   QPointF(c.x() + 9 * math.cos(a), c.y() - 9 * math.sin(a)))


def small_button(text, color="#2f8fff", fg="white", tip="", width=26):
    b = QToolButton()
    b.setText(text)
    b.setCheckable(True)
    b.setFixedSize(QSize(width, 18))
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(
        "QToolButton{background:qlineargradient(y1:0,y2:1,stop:0 #4a4a4e,stop:1 #3a3a3e);"
        "color:#d6d6da;border:1px solid #151516;border-radius:4px;font-size:9px;font-weight:bold}"
        "QToolButton:hover{background:#505055}"
        f"QToolButton:checked{{background:{color};color:{fg};border-color:#151516}}")
    return b


def lcd_style(bg="#0f0f10", fg="#d8d8dc"):
    return (f"background:{bg};color:{fg};border:1px solid #070707;border-radius:3px;"
            f"font-family:{MONO};font-size:9px;padding:0 1px")


def lcd_label(text=""):
    l = QLabel(text)
    l.setAlignment(Qt.AlignCenter)
    l.setFixedHeight(16)
    l.setStyleSheet(lcd_style())
    return l


class Strip(QFrame):
    """One mixer channel strip (mono or stereo), laid out like a TotalMix channel strip, with
    its name tag on top.

    key identifies the strip in the model ("in:0", "play:2", "out:16"); see model.strip_key().
    """
    clicked = Signal()
    context_requested = Signal(object)   # global QPoint, for the right-click menu

    def __init__(self, key, title, channels, kind, show_pan=True, show_stereo=False,
                 show_solo=False, parent=None):
        super().__init__(parent)
        self.key = key
        self.kind = kind
        self.channels = channels
        self.group = 0
        self._selected = False
        self.setFixedWidth(76)
        self.setObjectName("strip")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 6, 5, 5)
        lay.setSpacing(4)

        self.title = QPushButton(title)
        self.title.setFixedHeight(22)
        self.title.setCursor(Qt.PointingHandCursor if kind == "out" else Qt.ArrowCursor)
        self.title.clicked.connect(self.clicked.emit)
        lay.addWidget(self.title)

        self.pan = Knob()
        knob_row = QHBoxLayout()
        knob_row.addStretch()
        knob_row.addWidget(self.pan)
        knob_row.addStretch()
        if show_pan:
            lay.addLayout(knob_row)
        else:
            self.pan.hide()
            lay.addSpacing(34)

        btns = QHBoxLayout()
        btns.setSpacing(3)
        btns.addStretch()
        w = 20 if show_solo else 26   # three buttons have to fit the strip width
        self.mute = small_button("M", "#2f8fff", tip="Mute", width=w)
        btns.addWidget(self.mute)
        self.solo = small_button("S", SOLO.name(), "#1c1c1e", width=w,
                                 tip="Solo in the current submix (solo-in-place)")
        self.solo.setVisible(show_solo)
        btns.addWidget(self.solo)
        self.stereo = small_button("ST", "#d8d8dc", "#1c1c1e", tip="Stereo-link this channel pair",
                                   width=w)
        self.stereo.setVisible(show_stereo)
        btns.addWidget(self.stereo)
        btns.addStretch()
        lay.addLayout(btns)

        readouts = QHBoxLayout()
        readouts.setSpacing(2)
        self.value = lcd_label("0.0")
        self.value.setToolTip("Fader level (dB)")
        self.peak = lcd_label("-∞")
        self.peak.setToolTip("Peak level (dBFS) — click the meter to reset")
        readouts.addWidget(self.value)
        readouts.addWidget(self.peak)
        lay.addLayout(readouts)

        mid = QHBoxLayout()
        mid.setSpacing(0)
        mid.setContentsMargins(0, 0, 0, 0)
        self.fader = Fader()
        self.meter = Meter(len(channels))
        mid.addWidget(self.fader)
        mid.addWidget(self.meter)
        mid.addStretch()
        lay.addLayout(mid, 1)

        self.fader.changed.connect(lambda db: self.value.setText(fmt_db(db)))
        self.meter.peak_changed.connect(self._show_peak)
        self.set_selected(False)

    def _show_peak(self, db):
        self.peak.setText(fmt_db(db) if db != NEG_INF else "-∞")
        hot = db != NEG_INF and db >= -0.05
        self.peak.setStyleSheet(lcd_style("#c0262b", "white") if hot else lcd_style())

    def set_fader(self, db):
        self.fader.set_db(db)
        self.value.setText(fmt_db(db))

    def contextMenuEvent(self, e):
        self.context_requested.emit(e.globalPos())

    def set_group(self, group):
        self.group = group or 0
        self.setToolTip(f"Fader group {self.group} (hold Shift to move this fader alone)"
                        if self.group else "")
        self.set_selected(self._selected)

    def set_selected(self, sel):
        self._selected = sel
        col = KIND_COLOR[self.kind]
        top, bot = ("#4a4a4f", "#38383c") if sel else ("#38383b", "#2b2b2e")
        border = ACCENT.name() if sel else "#141415"
        # fader groups show as a coloured bar along the top edge of the strip
        top_border = (f"border-top:4px solid {GROUP_COLOR[self.group].name()};" if self.group else "")
        tag = ACCENT if sel else col
        self.setStyleSheet(
            f"QFrame#strip{{background:qlineargradient(y1:0,y2:1,stop:0 {top},stop:1 {bot});"
            f"border:1px solid {border};{top_border}border-radius:6px}}"
            f"QPushButton{{background:qlineargradient(y1:0,y2:1,stop:0 {tag.lighter(115).name()},"
            f"stop:1 {tag.darker(125).name()});color:white;font-size:10px;font-weight:600;"
            f"border:1px solid #111;border-radius:4px}}"
            f"QPushButton:hover{{background:{tag.lighter(130).name()}}}")


class Row(QWidget):
    def __init__(self, title, subtitle, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.title = title
        self.head = QLabel()
        self.head.setTextFormat(Qt.RichText)
        self.set_subtitle(subtitle)
        lay.addWidget(self.head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(self.scroll, 1)
        self.strips = []

    def set_subtitle(self, text):
        self.head.setText(
            f"<span style='color:{DIM.name()};font-size:9px;font-weight:700;letter-spacing:1.5px'>"
            f"{self.title.upper()}</span>&nbsp;&nbsp;"
            f"<span style='color:#6e6e73;font-size:9px'>{text}</span>")

    def set_strips(self, strips):
        inner = QWidget()
        hl = QHBoxLayout(inner)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(2)
        for s in strips:
            hl.addWidget(s)
        hl.addStretch()
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(inner)
        self.strips = strips
