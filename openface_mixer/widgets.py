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
from .theme import ACCENT, CYAN, DIM, GROUP_COLOR, HAIR, HAIR_STRONG, KIND_COLOR, MONO, RED, SOLO, caption_html


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
        self.setFixedWidth(5 * channels + 2)
        self.setMinimumHeight(60)
        self.setToolTip("Click to reset peak / clip")

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
        top, h = 5, self.height() - 7
        grad = QLinearGradient(0, top, 0, top + h)
        grad.setColorAt(0.0, QColor("#ff2a4f"))
        grad.setColorAt(0.06, QColor("#ffb020"))
        grad.setColorAt(0.2, QColor("#7fe9ff"))
        grad.setColorAt(0.45, QColor("#2ad4ff"))
        grad.setColorAt(1.0, QColor("#006b88"))
        for i, lv in enumerate(self.levels):
            x = 1 + i * 5
            p.fillRect(QRectF(x, top, 4, h), QColor("#01070b"))
            fh = self._frac(lv) * h
            p.fillRect(QRectF(x, top + h - fh, 4, fh), grad)
            hf = self._frac(self.holds[i][0]) * h
            if hf > 0:
                p.fillRect(QRectF(x, top + h - hf, 4, 1.5), QColor("#f5f5f7"))
            p.fillRect(QRectF(x, 0, 4, 3), QColor("#ff2a4f") if self.clip[i] else QColor("#0f2430"))
        # faint segment lines for a hardware feel
        p.setPen(QPen(QColor(1, 7, 11, 110), 1))
        for y in range(top + h - 3, top, -3):
            p.drawLine(0, y, self.width(), y)


class Fader(QWidget):
    changed = Signal(float)
    SCALE = (6, 0, -5, -10, -15, -20, -30, -40, -60)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pos = db2pos(0.0)
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setFixedWidth(34)
        self._drag = None
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Drag or scroll · Ctrl = fine · Double-click or Alt-click = 0 dB")

    def db(self):
        return pos2db(self.pos)

    def set_db(self, db):
        self.pos = db2pos(db)
        self.update()

    def _track(self):
        return 12.0, self.height() - 12.0

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
        tx = 9.0  # track centre
        # printed scale between fader and meter, as in Pro Tools
        f = QFont(self.font())
        f.setPointSizeF(6.0)
        p.setFont(f)
        last = None
        for db in self.SCALE:
            y = bot - db2pos(db) * (bot - top)
            if last is not None and y - last < 9 and db != 0:
                continue   # short fader: drop marks that would overlap
            last = y
            p.setPen(QColor("#ffffff") if db == 0 else QColor("#58707a"))
            p.drawText(QRectF(tx + 9, y - 6, 15, 12), Qt.AlignRight | Qt.AlignVCenter, str(abs(db)))
        # groove
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#01070b"))
        p.drawRoundedRect(QRectF(tx - 1.5, top - 4, 3, bot - top + 8), 1.5, 1.5)
        y = bot - self.pos * (bot - top)
        if self.pos > 0:
            p.setBrush(QColor(42, 212, 255, 70))
            p.drawRoundedRect(QRectF(tx - 1.5, y, 3, bot - y + 4), 1.5, 1.5)
        # cap: flat grey Pro Tools style with a white index line
        cap = QRectF(tx - 8, y - 12, 16, 24)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawRoundedRect(cap.translated(0, 2), 2, 2)
        g = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        if self.isEnabled():
            g.setColorAt(0.0, QColor("#163646"))
            g.setColorAt(0.5, QColor("#0a1c27"))
            g.setColorAt(1.0, QColor("#12303f"))
        else:
            g.setColorAt(0.0, QColor("#0b1a24"))
            g.setColorAt(1.0, QColor("#08141c"))
        p.setBrush(g)
        p.setPen(QPen(QColor(42, 212, 255, 90), 1))
        p.drawRoundedRect(cap, 2, 2)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        for dy in (-7, -4, 4, 7):
            p.drawLine(QPointF(cap.left() + 3, y + dy), QPointF(cap.right() - 3, y + dy))
        p.setPen(QPen(CYAN, 1.5))
        p.drawLine(QPointF(cap.left() + 1, y), QPointF(cap.right() - 1, y))


class Knob(QWidget):
    """Small pan knob: dark dial, white pointer, arc from centre."""
    changed = Signal(float)
    text_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setFixedSize(26, 26)
        self._drag = None
        self.setCursor(Qt.PointingHandCursor)
        self.set_value(0.0)

    def set_value(self, v):
        self.value = v
        self.setToolTip(f"Pan {self.text()} — drag, double-click to centre")
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
        r = 11.0
        ring = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setPen(QPen(QColor("#01070b"), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(ring, 225 * 16, -270 * 16)
        if self.value:
            p.setPen(QPen(CYAN, 2, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring, 90 * 16, int(-self.value * 135 * 16))
        body = QRectF(c.x() - 8, c.y() - 8, 16, 16)
        g = QLinearGradient(body.topLeft(), body.bottomLeft())
        g.setColorAt(0, QColor("#0d2433"))
        g.setColorAt(1, QColor("#040e16"))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawEllipse(body)
        a = math.radians(90 - self.value * 135)
        p.setPen(QPen(QColor("#f5f5f7"), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(c.x() + 3 * math.cos(a), c.y() - 3 * math.sin(a)),
                   QPointF(c.x() + 7 * math.cos(a), c.y() - 7 * math.sin(a)))


def small_button(text, color="#2ad4ff", fg="#021012", tip="", width=26):
    b = QToolButton()
    b.setText(text)
    b.setCheckable(True)
    b.setFixedSize(QSize(width, 16))
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    # square, filled buttons as in the original look, in the ocean colours
    b.setStyleSheet(
        "QToolButton{background:qlineargradient(y1:0,y2:1,stop:0 #12303f,stop:1 #0b2230);"
        "color:#b9d6dc;border:1px solid #01070b;border-radius:2px;font-size:9px;font-weight:bold}"
        "QToolButton:hover{background:#17394a;color:white}"
        f"QToolButton:checked{{background:{color};color:{fg};border-color:#01070b}}")
    return b


def label_style(col, sel):
    """Channel name tag: dark tag, white monospace text, a line in the channel's colour
    underneath; the selected submix turns white."""
    base = (f"border:none;border-radius:2px;padding:0 1px;font-family:{MONO};font-size:9px;"
            f"font-weight:700;border-bottom:2px solid {col.name()};")
    if sel:
        return base + "background:white;color:#021012;"
    return base + "background:#01070b;color:white;"


def lcd_style(bg="rgba(1,7,11,0.7)", fg="#9cc7cf"):
    return (f"background:{bg};color:{fg};border:1px solid {HAIR};border-radius:3px;"
            f"font-family:{MONO};font-size:8px;padding:0")


def lcd_label(text=""):
    l = QLabel(text)
    l.setAlignment(Qt.AlignCenter)
    l.setFixedHeight(15)
    l.setMinimumWidth(0)
    l.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    l.setStyleSheet(lcd_style())
    return l


class Strip(QFrame):
    """One mixer channel strip (mono or stereo), compact like a Pro Tools mix window strip:
    name tag, pan, solo/mute/stereo, fader with the scale beside its meter, and the level and
    peak readouts at the bottom.

    key identifies the strip in the model ("in:0", "play:2", "out:16"); see model.strip_key().
    """
    WIDTH = 62
    clicked = Signal()
    context_requested = Signal(object)   # global QPoint, for the right-click menu
    settings_requested = Signal(object)  # global QPoint below the settings button

    def __init__(self, key, title, channels, kind, show_pan=True, show_stereo=False,
                 show_solo=False, show_settings=False, parent=None):
        super().__init__(parent)
        self.key = key
        self.kind = kind
        self.channels = channels
        self.group = 0
        self._selected = False
        self.setFixedWidth(self.WIDTH)
        self.setObjectName("strip")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 4, 3, 4)
        lay.setSpacing(3)

        self.title = QPushButton(title)
        self.title.setFixedHeight(20)
        self.title.setMinimumWidth(0)
        self.title.setCursor(Qt.PointingHandCursor if kind == "out" else Qt.ArrowCursor)
        self.title.clicked.connect(self.clicked.emit)
        # channel settings (TotalMix's wrench): phase, level, 48V, ... where the device has them
        self.settings_btn = QToolButton()
        self.settings_btn.setText("⚙")
        self.settings_btn.setFixedSize(QSize(14, 20))
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
        self.pan_text.setStyleSheet(f"color:#7d9299;font-family:{MONO};font-size:8px")
        self.pan.text_changed.connect(self.pan_text.setText)
        if show_pan:
            knob_row = QHBoxLayout()
            knob_row.setSpacing(2)
            knob_row.addStretch()
            knob_row.addWidget(self.pan)
            knob_row.addWidget(self.pan_text)
            knob_row.addStretch()
            lay.addLayout(knob_row)
        else:
            self.pan.hide()
            self.pan_text.hide()

        btns = QHBoxLayout()
        btns.setSpacing(2)
        btns.addStretch()
        w = 17 if show_solo else 26   # three buttons have to fit the strip width
        self.solo = small_button("S", SOLO.name(), "#0b0f1c", width=w,
                                 tip="Solo in the current submix (solo-in-place)")
        self.solo.setVisible(show_solo)
        btns.addWidget(self.solo)
        self.mute = small_button("M", "#2ad4ff", tip="Mute", width=w)
        btns.addWidget(self.mute)
        self.stereo = small_button("ST", "#ffffff", "#0b0f1c", tip="Stereo-link this channel pair",
                                   width=w)
        self.stereo.setVisible(show_stereo)
        btns.addWidget(self.stereo)
        btns.addStretch()
        lay.addLayout(btns)

        mid = QHBoxLayout()
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
        readouts.setSpacing(2)
        self.value = lcd_label("0.0")
        self.value.setToolTip("Fader level (dB)")
        self.peak = lcd_label("-∞")
        self.peak.setToolTip("Peak level (dBFS) — click the meter to reset")
        readouts.addWidget(self.value)
        readouts.addWidget(self.peak)
        lay.addLayout(readouts)

        self.fader.changed.connect(lambda db: self.value.setText(fmt_db(db)))
        self.meter.peak_changed.connect(self._show_peak)
        self.set_selected(False)

    def _show_peak(self, db):
        self.peak.setText(fmt_db(db) if db != NEG_INF else "-∞")
        hot = db != NEG_INF and db >= -0.05
        self.peak.setStyleSheet(lcd_style("#ff2a4f", "white") if hot else lcd_style())

    def set_settings_active(self, on):
        """Light the settings button while a channel setting (phase, 48V, ...) is on."""
        self.settings_btn.setStyleSheet(
            "QToolButton{background:#0b2230;border:1px solid #01070b;border-radius:2px;"
            f"font-size:10px;color:{RED.name() if on else '#7d9299'}}}"
            "QToolButton:hover{background:#17394a;color:white}")

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
        # ink panel with a hairline edge, as the dashboard's tiles; selected strip gets a cyan edge
        border = "rgba(42,212,255,0.55)" if sel else HAIR
        top_border = (f"border-top:2px solid {GROUP_COLOR[self.group].name()};" if self.group else "")
        tag = label_style(col, sel)
        self.setStyleSheet(
            f"QFrame#strip{{background:qlineargradient(y1:0,y2:1,stop:0 rgba(4,12,18,0.62),"
            f"stop:0.7 rgba(4,12,18,0.93));border:1px solid {border};{top_border}border-radius:6px}}"
            f"QPushButton{{{tag}}}")


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
            f"<span style='color:{RED.name()};font-size:9px'>■</span>&nbsp;&nbsp;"
            f"{caption_html(self.title)}&nbsp;&nbsp;&nbsp;"
            f"<span style='color:#58707a;font-size:9px'>{text}</span>")

    def set_strips(self, strips):
        inner = QWidget()
        hl = QHBoxLayout(inner)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        for s in strips:
            hl.addWidget(s)
        hl.addStretch()
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(inner)
        self.strips = strips
