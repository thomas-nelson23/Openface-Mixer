"""Routing matrix view: columns = inputs/playback strips, rows = output pairs (TotalMix "Matrix")."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .model import FADER_MAX_DB, db2pos, fmt_db
from .theme import ACCENT, DIM, KIND_COLOR, SANS, TEXT, font, px


class MatrixView(QWidget):
    """TotalMix-style routing grid: columns = inputs/playback, rows = output pairs."""
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.CW, self.CH, self.LW, self.HH, self.GAP = px(48), px(24), px(84), px(44), px(10)
        self.cols, self.rows, self.n_in_cols = [], [], 0
        self.hover = None
        self.setMouseTracking(True)

    def rebuild(self):
        w = self.win
        self.cols = [("in", s.channels, s.name, s.color) for s in w.row_in.strips] + \
                    [("play", s.channels, s.name, s.color) for s in w.row_play.strips]
        self.rows = [(s.pair, s.name, s.color) for s in w.row_out.strips]
        self.n_in_cols = len(w.row_in.strips)
        self.setFixedSize(self._x(len(self.cols)) + 2, self.HH + len(self.rows) * self.CH + 2)
        self.update()

    def _x(self, ci):
        return self.LW + ci * self.CW + (self.GAP if ci >= self.n_in_cols else 0)

    def _cell(self, pos):
        x, y = pos.x(), pos.y()
        if y < self.HH or x < self.LW:
            return None
        ri = int((y - self.HH) // self.CH)
        for ci in range(len(self.cols)):
            if self._x(ci) <= x < self._x(ci) + self.CW:
                return (ci, ri) if ri < len(self.rows) else None
        return None

    def _gain(self, ci, ri):
        kind, chans, _, _ = self.cols[ci]
        return self.win.st["sends"][kind][chans[0]][self.rows[ri][0]][0]

    def _set(self, ci, ri, db):
        kind, chans, _, _ = self.cols[ci]
        self.win.set_send_at(kind, chans, self.rows[ri][0], db)

    def mousePressEvent(self, e):
        c = self._cell(e.position())
        if c is None:
            return
        if e.button() == Qt.RightButton:
            self._set(*c, None)
        elif e.button() == Qt.LeftButton:
            self._set(*c, 0.0 if self._gain(*c) is None else None)

    def wheelEvent(self, e):
        c = self._cell(e.position())
        if c is None:
            e.ignore()
            return
        g = self._gain(*c)
        up = e.angleDelta().y() > 0
        if g is None:
            g = -40.0 if up else None
        else:
            g += 1.0 if up else -1.0
            g = None if g < -90 else min(FADER_MAX_DB, g)
        self._set(*c, g)

    def mouseMoveEvent(self, e):
        c = self._cell(e.position())
        if c != self.hover:
            self.hover = c
            if c:
                g = self._gain(*c)
                self.setToolTip(f"{self.cols[c[0]][2]} → {self.rows[c[1]][1]}: {fmt_db(g)} dB\n"
                                "Click: on (0 dB) / off · Wheel: adjust · Right-click: off")
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setFont(font(SANS, 11))
        p.setRenderHint(QPainter.Antialiasing)
        sel = self.win.st["selected"]
        dim, txt = DIM, TEXT
        # group headers
        head_h = px(18)
        p.setFont(font(SANS, 8, QFont.DemiBold))
        if self.n_in_cols:
            p.setPen(KIND_COLOR["in"].lighter(130))
            p.drawText(QRectF(self._x(0), 0, self.n_in_cols * self.CW, head_h),
                       Qt.AlignLeft | Qt.AlignVCenter, "HARDWARE INPUTS")
        if len(self.cols) > self.n_in_cols:
            p.setPen(KIND_COLOR["play"].lighter(130))
            p.drawText(QRectF(self._x(self.n_in_cols), 0, px(400), head_h),
                       Qt.AlignLeft | Qt.AlignVCenter, "PLAYBACK")
        p.setFont(font(SANS, 11))
        for ci, (_, _, name, color) in enumerate(self.cols):
            lit = self.hover and self.hover[0] == ci
            p.setPen(txt if lit else (color or dim))
            p.drawText(QRectF(self._x(ci), head_h, self.CW, self.HH - head_h - px(2)), Qt.AlignCenter,
                       p.fontMetrics().elidedText(name, Qt.ElideRight, self.CW - px(2)))
        for ri, (pair, name, color) in enumerate(self.rows):
            y = self.HH + ri * self.CH
            if pair == sel:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(42, 212, 255, 30))
                p.drawRoundedRect(QRectF(0, y + 1, self.LW - px(6), self.CH - 2), px(4), px(4))
            lit = self.hover and self.hover[1] == ri
            p.setPen(ACCENT if pair == sel else (txt if lit else (color or dim)))
            p.drawText(QRectF(px(4), y, self.LW - px(8), self.CH), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(name, Qt.ElideRight, self.LW - px(8)))
            for ci in range(len(self.cols)):
                r = QRectF(self._x(ci) + 1, y + 1, self.CW - 2, self.CH - 2)
                g = self._gain(ci, ri)
                p.setPen(Qt.NoPen)
                if g is None:
                    if pair == sel:
                        p.setBrush(QColor("#06202e"))
                    else:
                        p.setBrush(QColor("#04121b") if (ci + ri) % 2 else QColor("#03101a"))
                    p.drawRoundedRect(r, px(3), px(3))
                else:
                    k = 0.45 + 0.55 * min(1.0, db2pos(g) / db2pos(0.0))
                    base = self.cols[ci][3] or KIND_COLOR[self.cols[ci][0]]
                    p.setBrush(QColor(int(base.red() * k), int(base.green() * k), int(base.blue() * k)))
                    p.drawRoundedRect(r, px(3), px(3))
                    p.setPen(QColor("#021012"))
                    p.drawText(r, Qt.AlignCenter, fmt_db(g))
                if self.hover == (ci, ri):
                    p.setPen(QPen(ACCENT, max(1.0, px(1.5))))
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(r, px(3), px(3))
