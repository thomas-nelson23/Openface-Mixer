"""Routing matrix view: columns = inputs/playback strips, rows = output pairs (TotalMix "Matrix")."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .model import FADER_MAX_DB, db2pos, fmt_db
from .theme import ACCENT, DIM, KIND_COLOR, TEXT


class MatrixView(QWidget):
    """TotalMix-style routing grid: columns = inputs/playback, rows = output pairs."""
    CW, CH, LW, HH, GAP = 48, 24, 84, 44, 10

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.cols, self.rows, self.n_in_cols = [], [], 0
        self.hover = None
        self.setMouseTracking(True)

    def rebuild(self):
        w = self.win
        self.cols = [("in", s.channels, s.title.text()) for s in w.row_in.strips] + \
                    [("play", s.channels, s.title.text()) for s in w.row_play.strips]
        self.rows = [(s.pair, s.title.text()) for s in w.row_out.strips]
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
        kind, chans, _ = self.cols[ci]
        return self.win.st["sends"][kind][chans[0]][self.rows[ri][0]][0]

    def _set(self, ci, ri, db):
        kind, chans, _ = self.cols[ci]
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
        f = p.font()
        f.setPointSize(8)
        p.setFont(f)
        p.setRenderHint(QPainter.Antialiasing)
        sel = self.win.st["selected"]
        dim, txt = DIM, TEXT
        # group headers
        if self.n_in_cols:
            p.setPen(KIND_COLOR["in"].lighter(130))
            p.drawText(QRectF(self._x(0), 0, self.n_in_cols * self.CW, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       "HARDWARE INPUTS")
        if len(self.cols) > self.n_in_cols:
            p.setPen(KIND_COLOR["play"].lighter(130))
            p.drawText(QRectF(self._x(self.n_in_cols), 0, 400, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       "SOFTWARE PLAYBACK")
        for ci, (_, _, name) in enumerate(self.cols):
            p.setPen(txt if self.hover and self.hover[0] == ci else dim)
            p.drawText(QRectF(self._x(ci), 18, self.CW, self.HH - 20), Qt.AlignCenter, name)
        for ri, (pair, name) in enumerate(self.rows):
            y = self.HH + ri * self.CH
            if pair == sel:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(240, 164, 58, 45))
                p.drawRoundedRect(QRectF(0, y + 1, self.LW - 6, self.CH - 2), 4, 4)
            p.setPen(ACCENT if pair == sel else (txt if self.hover and self.hover[1] == ri else dim))
            p.drawText(QRectF(4, y, self.LW - 8, self.CH), Qt.AlignVCenter | Qt.AlignLeft, name)
            for ci in range(len(self.cols)):
                r = QRectF(self._x(ci) + 1, y + 1, self.CW - 2, self.CH - 2)
                g = self._gain(ci, ri)
                p.setPen(Qt.NoPen)
                if g is None:
                    if pair == sel:
                        p.setBrush(QColor("#3a3328"))
                    else:
                        p.setBrush(QColor("#28282b") if (ci + ri) % 2 else QColor("#252528"))
                    p.drawRoundedRect(r, 3, 3)
                else:
                    k = 0.45 + 0.55 * min(1.0, db2pos(g) / db2pos(0.0))
                    base = KIND_COLOR[self.cols[ci][0]]
                    p.setBrush(QColor(int(base.red() * k), int(base.green() * k), int(base.blue() * k)))
                    p.drawRoundedRect(r, 3, 3)
                    p.setPen(QColor("white"))
                    p.drawText(r, Qt.AlignCenter, fmt_db(g))
                if self.hover == (ci, ri):
                    p.setPen(QPen(ACCENT, 1.5))
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(r, 3, 3)
