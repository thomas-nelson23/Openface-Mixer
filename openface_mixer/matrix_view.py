"""Routing matrix view: columns = inputs/playback strips, rows = output pairs (TotalMix "Matrix")."""
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget

from .model import (
    FADER_MAX_DB, NEG_INF, db2pos, fader_db, fmt_db, parse_db, pos2db, submix_of,
)
from .theme import ACCENT, DIM, KIND_COLOR, SANS, TEXT, font, px
from .wheel import Wheel
from .widgets import LevelEdit


class MatrixView(QWidget):
    """TotalMix-style routing grid: columns = inputs/playback, rows = output pairs. It lives in
    self.scroll; the column and row names stay in view while the cells scroll under them.

    A click turns an empty cell on at 0 dB and selects it (a cell that has a level is only
    selected), dragging up and down sets the level, double-click or Delete clears it. The wheel
    scrolls the grid, and changes a level only with Ctrl held. The arrow keys move the
    selection, Enter types a level."""
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.CW, self.CH, self.LW, self.HH, self.GAP = px(48), px(24), px(84), px(44), px(10)
        self.cols, self.rows, self.n_in_cols = [], [], 0
        self.hover = None
        self.sel = None           # the selected cell (column, row)
        self._drag = None         # (pointer y, fader position there) while a level is dragged
        self._wheel = Wheel()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self)
        self.scroll.setFrameShape(QFrame.NoFrame)
        for bar in (self.scroll.horizontalScrollBar(), self.scroll.verticalScrollBar()):
            bar.valueChanged.connect(lambda _: self.update())   # the names move to stay in view

    def rebuild(self):
        w = self.win
        self.cols = [("in", s.channels, s.name, s.color) for s in w.row_in.strips] + \
                    [("play", s.channels, s.name, s.color) for s in w.row_play.strips]
        self.rows = [(s.pair, s.name, s.color) for s in w.row_out.strips]
        self.n_in_cols = len(w.row_in.strips)
        if self.sel and not (self.sel[0] < len(self.cols) and self.sel[1] < len(self.rows)):
            self.sel = None
        self._drag = None
        self.setFixedSize(self._x(len(self.cols)) + 2, self.HH + len(self.rows) * self.CH + 2)
        self.update()

    def _x(self, ci):
        return self.LW + ci * self.CW + (self.GAP if ci >= self.n_in_cols else 0)

    def _origin(self):
        """The top left corner of the part in view, where the names are drawn."""
        return max(0, -self.x()), max(0, -self.y())

    def _cell(self, pos):
        ox, oy = self._origin()
        x, y = pos.x(), pos.y()
        if y < oy + self.HH or x < ox + self.LW:      # on the names
            return None
        ri = int((y - self.HH) // self.CH)
        for ci in range(len(self.cols)):
            if self._x(ci) <= x < self._x(ci) + self.CW:
                return (ci, ri) if ri < len(self.rows) else None
        return None

    def _rect(self, ci, ri):
        return QRectF(self._x(ci) + 1, self.HH + ri * self.CH + 1, self.CW - 2, self.CH - 2)

    def _send(self, ci, ri):
        """[level in dB or None, pan] of the cell."""
        kind, chans, _, _ = self.cols[ci]
        return self.win.st["sends"][kind][chans[0]][submix_of(self.win.st, self.rows[ri][0])]

    def _gain(self, ci, ri):
        return self._send(ci, ri)[0]

    def _set(self, ci, ri, db):
        kind, chans, _, _ = self.cols[ci]
        self.win.set_send_at(kind, chans, self.rows[ri][0], db)

    def _select(self, c):
        """Select cell c and scroll it into view, clear of the names."""
        self.sel = c
        x, y = self._x(c[0]), self.HH + c[1] * self.CH
        view = self.scroll.viewport()
        for bar, lo, hi in ((self.scroll.horizontalScrollBar(), x - self.LW,
                             x + self.CW - view.width()),
                            (self.scroll.verticalScrollBar(), y - self.HH,
                             y + self.CH - view.height())):
            bar.setValue(min(lo, max(bar.value(), hi)))
        self.update()

    def mousePressEvent(self, e):
        c = self._cell(e.position())
        if c is None or e.button() != Qt.LeftButton:
            return
        self.sel = c
        self._drag = (e.position().y(), None)
        if self._gain(*c) is None:
            self._set(*c, 0.0)
        self.update()

    def mouseDoubleClickEvent(self, e):
        c = self._cell(e.position())
        if c is not None and e.button() == Qt.LeftButton:
            self.sel = c
            self._set(*c, None)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def wheelEvent(self, e):
        c = self._cell(e.position())
        if c is None or not e.modifiers() & Qt.ControlModifier:
            e.ignore()            # the grid scrolls
            return
        n = self._wheel.steps(e)
        e.accept()                # with Ctrl held, a sideways movement would jump a page
        g = self._gain(*c)
        if n and g is not None:   # an empty cell stays empty
            g += n
            self._set(*c, None if g < -90 else min(FADER_MAX_DB, g))

    def mouseMoveEvent(self, e):
        if self._drag is not None and self.sel is not None:
            y0, pos0 = self._drag
            dy = y0 - e.position().y()
            if pos0 is None:
                if abs(dy) >= px(4):   # less is a click; the level then moves on from here
                    self._drag = (e.position().y(), db2pos(self._gain(*self.sel)))
                return
            scale = 0.15 if e.modifiers() & Qt.ControlModifier else 1.0
            db = pos2db(max(0.0, min(1.0, pos0 + dy / px(160) * scale)))
            self._set(*self.sel, None if db == NEG_INF else round(db, 1))
            return
        c = self._cell(e.position())
        if c != self.hover:
            self.hover = c
            self.update()

    def event(self, e):
        if e.type() == QEvent.ToolTip:     # written when it is shown, so the level is current
            c = self._cell(e.pos())
            self.setToolTip("" if c is None else
                            f"{self.cols[c[0]][2]} → {self.rows[c[1]][1]}: "
                            f"{fmt_db(self._gain(*c))} dB\n"
                            "Click an empty cell to turn it on · drag to set the level\n"
                            "Double-click or Delete to clear · Ctrl+scroll to adjust · "
                            "Enter to type a level")
        return super().event(e)

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def keyPressEvent(self, e):
        step = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1),
                Qt.Key_Down: (0, 1)}.get(e.key())
        if self.sel is None:
            super().keyPressEvent(e)
        elif step:
            self._select((max(0, min(len(self.cols) - 1, self.sel[0] + step[0])),
                          max(0, min(len(self.rows) - 1, self.sel[1] + step[1]))))
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self._set(*self.sel, None)
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._select(self.sel)    # it may have been scrolled out of view
            self._type_level()
        else:
            super().keyPressEvent(e)

    def _type_level(self):
        kind, chans, _, _ = self.cols[self.sel[0]]
        pair = self.rows[self.sel[1]][0]

        def enter(text):
            db = parse_db(text)
            if db is None:
                return False
            db = fader_db(db)
            self.win.set_send_at(kind, chans, pair, None if db == NEG_INF else db)

        LevelEdit(self, self._rect(*self.sel).toRect(), fmt_db(self._gain(*self.sel)), enter)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        if self.sel is None and self.cols and self.rows:
            self.sel = (0, 0)     # reached with Tab: the keys need a cell to act on
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ox, oy = self._origin()
        submix = self.win.st["selected"]
        dim, txt = DIM, TEXT
        # column names: they scroll sideways with the grid and stay at the top
        p.setClipRect(QRectF(ox + self.LW, oy, self.width(), self.HH))
        head_h = px(18)
        p.setFont(font(SANS, 8, QFont.DemiBold))
        if self.n_in_cols:
            p.setPen(KIND_COLOR["in"].lighter(130))
            p.drawText(QRectF(self._x(0), oy, self.n_in_cols * self.CW, head_h),
                       Qt.AlignLeft | Qt.AlignVCenter, "HARDWARE INPUTS")
        if len(self.cols) > self.n_in_cols:
            p.setPen(KIND_COLOR["play"].lighter(130))
            p.drawText(QRectF(self._x(self.n_in_cols), oy, px(400), head_h),
                       Qt.AlignLeft | Qt.AlignVCenter, "PLAYBACK")
        p.setFont(font(SANS, 11))
        for ci, (_, _, name, color) in enumerate(self.cols):
            lit = self.hover and self.hover[0] == ci
            p.setPen(txt if lit else (color or dim))
            p.drawText(QRectF(self._x(ci), oy + head_h, self.CW, self.HH - head_h - px(2)),
                       Qt.AlignCenter,
                       p.fontMetrics().elidedText(name, Qt.ElideRight, self.CW - px(2)))
        # row names: they scroll up and down with the grid and stay at the left
        p.setClipRect(QRectF(ox, oy + self.HH, self.LW, self.height()))
        for ri, (pair, name, color) in enumerate(self.rows):
            y = self.HH + ri * self.CH
            if pair == submix:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(42, 212, 255, 30))
                p.drawRoundedRect(QRectF(ox, y + 1, self.LW - px(6), self.CH - 2), px(4), px(4))
            lit = self.hover and self.hover[1] == ri
            p.setPen(ACCENT if pair == submix else (txt if lit else (color or dim)))
            p.drawText(QRectF(ox + px(4), y, self.LW - px(8), self.CH),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(name, Qt.ElideRight, self.LW - px(8)))
        # the cells, cut off where they pass under the names
        p.setClipRect(QRectF(ox + self.LW, oy + self.HH, self.width(), self.height()))
        ink = QColor("#021012")
        shown = QRectF(e.rect())
        for ri, (pair, name, color) in enumerate(self.rows):
            for ci in range(len(self.cols)):
                r = self._rect(ci, ri)
                if not r.intersects(shown):
                    continue
                g, pan = self._send(ci, ri)
                p.setPen(Qt.NoPen)
                if g is None:
                    if pair == submix:
                        p.setBrush(QColor("#06202e"))
                    else:
                        p.setBrush(QColor("#04121b") if (ci + ri) % 2 else QColor("#03101a"))
                    p.drawRoundedRect(r, px(3), px(3))
                else:
                    k = 0.45 + 0.55 * min(1.0, db2pos(g) / db2pos(0.0))
                    base = self.cols[ci][3] or KIND_COLOR[self.cols[ci][0]]
                    p.setBrush(QColor(int(base.red() * k), int(base.green() * k), int(base.blue() * k)))
                    p.drawRoundedRect(r, px(3), px(3))
                    p.setPen(ink)
                    p.drawText(r, Qt.AlignCenter, fmt_db(g))
                    if len(self.cols[ci][1]) == 1:
                        # a mono source: a tick along the bottom shows where it is panned
                        tx = r.center().x() + pan * (r.width() / 2 - px(4))
                        p.setPen(QPen(ink, px(2), Qt.SolidLine, Qt.FlatCap))
                        p.drawLine(QPointF(tx, r.bottom() - px(4)), QPointF(tx, r.bottom()))
        p.setBrush(Qt.NoBrush)
        if self.hover and self.hover != self.sel:
            p.setPen(QPen(txt, 1))
            p.drawRoundedRect(self._rect(*self.hover), px(3), px(3))
        if self.sel:
            # the selected cell, lit while the keys act on it; the dark edge keeps the ring
            # visible on a cell of its own colour
            for pen in (QPen(ink, px(4)), QPen(ACCENT if self.hasFocus() else dim, px(2))):
                p.setPen(pen)
                p.drawRoundedRect(self._rect(*self.sel), px(3), px(3))
