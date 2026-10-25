"""Custom-painted mixer controls: meter, fader, pan knob, channel strip and strip row.

All sizes go through theme.px(), so the strips are laid out at the chosen UI scale."""
import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout,
    QWidget,
)

from .model import FADER_MAX_DB, FADER_MIN_DB, NEG_INF, db2pos, fmt_db, lin2db, pos2db
from .theme import (
    CYAN, GROUP_COLOR, HAIR, KIND_COLOR, MONO, MONO_STACK, MUTED, RED, SANS, SOLO, caption_html,
    font, icon_pixmap, px,
)
from PySide6.QtGui import QIcon
from PySide6.QtGui import QFont


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
        self.setFixedWidth((self.bar + 1) * channels + 1)
        self.setMinimumHeight(px(60))
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
        b = self.bar
        clip_h = px(3)
        top, h = clip_h + px(2), self.height() - clip_h - px(4)
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
            p.drawLine(0, y, self.width(), y)


class Fader(QWidget):
    changed = Signal(float)
    SCALE = (6, 0, -5, -10, -15, -20, -30, -40, -60)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pos = db2pos(0.0)
        self.setMinimumHeight(px(60))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setFixedWidth(px(34))
        self._drag = None
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Drag or scroll · Ctrl = fine · Double-click or Alt-click = 0 dB")

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
        tx = px(9)   # track centre
        # printed scale between fader and meter, as in Pro Tools
        p.setFont(font(SANS, 7, QFont.Medium))
        last = None
        for db in self.SCALE:
            y = bot - db2pos(db) * (bot - top)
            if last is not None and y - last < px(9) and db != 0:
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


class Knob(QWidget):
    """Small pan knob: dark dial, white pointer, arc from centre."""
    changed = Signal(float)
    text_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setFixedSize(px(26), px(26))
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
        v = max(-1.0, min(1.0, self.value + (1 if e.angleDelta().y() > 0 else -1) / 32))
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
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
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
    b.setFixedSize(QSize(px(width), px(17)))
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFont(font(SANS, 9, QFont.DemiBold))
    b.setStyleSheet(
        f"QToolButton{{background:#0a1821;color:#8aa3ab;border:1px solid rgba(255,255,255,0.09);"
        f"border-radius:{px(3)}px}}"
        "QToolButton:hover{background:#12293a;color:white;border-color:rgba(255,255,255,0.18)}"
        f"QToolButton:checked{{background:{color};color:{fg};border-color:{color}}}")
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
                 show_solo=False, show_settings=False, standard=None, color=None, icon=None,
                 parent=None):
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
        self.width_px = px(self.WIDTH)
        self.setFixedWidth(self.width_px)
        self.setObjectName("strip")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(3), px(4), px(3), px(4))
        lay.setSpacing(px(3))

        self.title = QPushButton()
        self.title.setFixedHeight(px(20))
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.title.setFont(font(SANS, 10, QFont.DemiBold))
        self.title.setCursor(Qt.PointingHandCursor if kind == "out" else Qt.ArrowCursor)
        self.title.clicked.connect(self.clicked.emit)
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
        self._set_title(show_settings)

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
        readouts.setSpacing(px(2))
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

    def _set_title(self, with_settings):
        """Shorten a long (custom) name to fit the tag beside its icon; the tooltip has it in
        full."""
        width = self.width_px - px(10) - (px(15) if with_settings else 0)
        if self.icon:
            width -= px(16)
        self.title.setText(QFontMetrics(self.title.font()).elidedText(self.name, Qt.ElideRight,
                                                                      width))
        self.title.setToolTip(self.name if self.name == self.standard
                              else f"{self.name} ({self.standard})")

    def _show_peak(self, db):
        self.peak.setText(fmt_db(db) if db != NEG_INF else "-∞")
        hot = db != NEG_INF and db >= -0.05
        self.peak.setStyleSheet(lcd_style("#ff2a4f", "white") if hot else lcd_style())

    def set_settings_active(self, on):
        """Light the settings button while a channel setting (phase, 48V, ...) is on."""
        self.settings_btn.setStyleSheet(
            f"QToolButton{{background:#0a1821;border:1px solid rgba(255,255,255,0.09);"
            f"border-radius:{px(3)}px;font-size:{px(10)}px;"
            f"color:{RED.name() if on else '#7d9299'}}}"
            "QToolButton:hover{background:#12293a;color:white}")

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

    def set_color(self, color):
        """color: a QColor / "#rrggbb", or None for the row's default colour."""
        self.color = QColor(color) if color else None
        self.set_selected(self._selected)

    def set_icon(self, icon):
        """icon: a name from theme.CHANNEL_ICONS, or None."""
        self.icon = icon
        self._set_title(self._with_settings)
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
        self._apply_icon(sel)
        self.setStyleSheet(
            f"QFrame#strip{{background:qlineargradient(y1:0,y2:1,stop:0 rgba(4,12,18,0.62),"
            f"stop:0.7 rgba(4,12,18,0.93));border:1px solid {border};{top_border}"
            f"border-radius:{px(6)}px}}"
            f"QPushButton{{{tag}}}")


class Row(QWidget):
    def __init__(self, title, subtitle, color, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(4))
        self.title = title
        head = QHBoxLayout()
        head.setContentsMargins(px(2), 0, 0, 0)
        head.setSpacing(px(8))
        bar = QFrame()                        # a short bar in the row's colour
        bar.setFixedSize(px(3), px(10))
        bar.setStyleSheet(f"background:{color.name()};border-radius:{px(1)}px")
        head.addWidget(bar)
        self.head = QLabel()
        self.head.setTextFormat(Qt.RichText)
        self.set_subtitle(subtitle)
        head.addWidget(self.head, 1)
        lay.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(self.scroll, 1)
        self.strips = []

    def set_subtitle(self, text):
        # the title in tracked caps, then the hint
        self.head.setText(
            f"{caption_html(self.title)}&nbsp;&nbsp;&nbsp;"
            f"<span style='color:{MUTED};font-size:{px(10)}px'>{text}</span>")

    def set_strips(self, strips):
        inner = QWidget()
        hl = QHBoxLayout(inner)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(px(4))
        for s in strips:
            hl.addWidget(s)
        hl.addStretch()
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(inner)
        self.strips = strips
