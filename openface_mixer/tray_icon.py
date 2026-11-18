"""The system tray icon (tray.py says what it shows): a sign that a mixer started without a
window is running, the way back to its window, and Dim, the main out mute, the presets and Quit.

QSystemTrayIcon is a StatusNotifierItem on KDE Plasma and most other desktops, which draw the
icon, the tooltip and the menu. Where there is no tray (GNOME without an extension) no icon is
made and the app behaves as it does without one.
"""
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QActionGroup, QIcon, QPainter
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import tray
from .config import load_ui
from .theme import CYAN, RED

APP_ICON = Path(__file__).parent / "openface-mixer.svg"
SIZES = (16, 22, 24, 32, 48, 64)          # panel icon sizes at 1x and 2x
BADGE = {tray.ON: CYAN, tray.FAULT: RED}
RETRY_MS, RETRIES = 3000, 40              # at login the tray may come up after the app: look
                                          # again for two minutes, then run without an icon


def state_icon(state):
    """The app icon for a tray.icon_state(): greyed unless the engine runs, with a dot in the
    corner for ON and FAULT."""
    app_icon = QIcon(str(APP_ICON))
    mode = QIcon.Normal if state in (tray.NORMAL, tray.ON) else QIcon.Disabled
    icon = QIcon()
    for size in SIZES:
        pm = app_icon.pixmap(QSize(size, size), mode)
        if state in BADGE:
            d = max(6.0, round(size * 0.36))        # 8 px on a 22 px icon
            ring = max(1.0, size / 22)
            box = QRectF(size - d, size - d, d, d)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setCompositionMode(QPainter.CompositionMode_Clear)    # a gap around the dot
            p.drawEllipse(box.adjusted(-ring, -ring, ring, ring))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setBrush(BADGE[state])
            p.drawEllipse(box)
            p.end()
        icon.addPixmap(pm)
    return icon


class Tray(QObject):
    """The window's tray icon, there or not as the Tray icon setting says. The window calls
    apply() when the setting changes and refresh() when anything the icon shows may have."""

    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.icon = self.menu = None
        self._shown = None                  # (menu, state, tooltip) on the icon now
        self._icons = {}
        self._tries = 0
        self._retry = QTimer(self, interval=RETRY_MS, timeout=self._look_again)
        self.apply()

    def visible(self):
        return self.icon is not None

    def apply(self):
        """Show or remove the icon as the setting says."""
        self._tries = 0
        self._retry.stop()
        if not tray.wanted(load_ui().get("tray"), self.w.background):
            self.remove()
        elif self.icon is None:
            self._look_again()
        self._quit_on_close()

    def _look_again(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._retry.stop()
            self._create()
            self._quit_on_close()
        elif self._tries < RETRIES:
            self._tries += 1
            self._retry.start()
        else:
            self._retry.stop()

    def _quit_on_close(self):
        # with the icon there, hiding the window (or closing the tray menu) must not end the app
        QApplication.setQuitOnLastWindowClosed(not self.w.hides_on_close())

    def _create(self):
        self.menu = QMenu(self.w)
        self.menu.aboutToShow.connect(self.refresh)
        self.icon = QSystemTrayIcon(self)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self._shown = None
        self.refresh()
        self.icon.show()

    def remove(self):
        if self.icon is not None:
            self.icon.hide()
            self.icon.deleteLater()
            self.menu.deleteLater()
            self.icon = self.menu = None

    def _activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:       # left click
            self.w.command("hide" if self.w.window_shown() else "show")

    def refresh(self):
        """Bring the menu, the icon and the tooltip up to date; cheap when nothing changed."""
        if self.icon is None:
            return
        items, state, tip = shown = self.w.tray_view()
        old = self._shown or (None, None, None)
        if items != old[0]:
            for sub in self.menu.findChildren(QMenu):
                sub.deleteLater()
            self.menu.clear()
            self._fill(self.menu, items)
        if state != old[1]:
            if state not in self._icons:
                self._icons[state] = state_icon(state)
            self.icon.setIcon(self._icons[state])
        if tip != old[2]:
            self.icon.setToolTip(tip)
        self._shown = shown

    def _fill(self, menu, items):
        group = None
        for it in items:
            if it.kind == "separator":
                menu.addSeparator()
            elif it.kind == "menu":
                sub = menu.addMenu(it.text)
                sub.setEnabled(it.enabled)
                self._fill(sub, it.children)
            else:
                a = menu.addAction(it.text)
                a.setEnabled(it.enabled)
                a.setCheckable(it.kind != "action")
                a.setChecked(it.checked)
                if it.kind == "radio":
                    group = group or QActionGroup(menu)
                    group.addAction(a)
                a.triggered.connect(lambda _on=False, cmd=it.command: self.w.command(*cmd))
