"""The OSC network remote (osc_remote.py) on a UDP socket, and its settings-panel box.

RemoteLink listens on a UDP port and answers each controller at the address and port its packets
came from, the way controllers that use one socket for both directions (Q-SYS, Companion) expect.
A controller counts as connected for CLIENT_TIMEOUT_S after its last packet; they all poll with
/sendstate or similar well within that. MainWindow decides what a received message does.
"""
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QSpinBox

from . import APP_ID, APP_NAME
from . import osc_remote
from .settings_panel import CYAN, OFF, RED, light
from .theme import Section, key_label

CLIENT_TIMEOUT_S = 60
PUBLISH_MS = 150          # how often changes are sent
LEVEL_MS = 100            # how often peak levels are sent (10 per second, like TotalMix)
LEVEL_STEP_DB = 0.5       # a level is resent once it moved this much
AUTOSTART_FILE = (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
                  / "autostart" / f"{APP_ID}.desktop")


def remote_settings(st):
    """st["remote"], created with defaults: off until the user switches it on."""
    r = st.setdefault("remote", {})
    r.setdefault("enabled", False)
    r.setdefault("port", osc_remote.DEFAULT_PORT)
    r.setdefault("levels", True)
    return r


def autostart_enabled():
    return AUTOSTART_FILE.is_file()


def set_autostart(on, device_key):
    """Start Openface Mixer at login without a window (an XDG autostart entry)."""
    if on:
        AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART_FILE.write_text(
            "[Desktop Entry]\nType=Application\n"
            f"Name={APP_NAME} (background)\n"
            "Comment=Keeps the mixer and its network remote running without a window\n"
            f"Exec=openface-mixer --background --device {device_key}\n"
            "Icon=audio-card\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
    else:
        AUTOSTART_FILE.unlink(missing_ok=True)


class RemoteLink(QObject):
    """received(address, args) is emitted for each message; MainWindow answers with
    state requests through send_state() and publishes its changes with publish()."""
    received = Signal(str, list)
    status_changed = Signal(str)

    def __init__(self, snapshot, parent=None):
        """snapshot: callable returning osc_remote.snapshot() for the current state."""
        super().__init__(parent)
        self.snapshot = snapshot
        self.sock = None
        self.clients = {}                  # (address, port) -> time of last packet
        self.sent = {}                     # what the clients were last told
        self.levels_on = True
        self._levels = {}                  # level address -> highest dB since the last send
        self._levels_sent = {}
        self.status = "off"
        self._publish_timer = QTimer(self, interval=PUBLISH_MS, timeout=self.publish)
        self._level_timer = QTimer(self, interval=LEVEL_MS, timeout=self._send_levels)

    # ---- socket
    def start(self, port):
        self.stop()
        self.sock = QUdpSocket(self)
        if not self.sock.bind(QHostAddress(QHostAddress.AnyIPv4), port):
            err = self.sock.errorString()
            self.sock = None
            self._set_status(f"error: port {port}: {err}")
            return False
        self.sock.readyRead.connect(self._read)
        self.sent = {}
        self._publish_timer.start()
        self._level_timer.start()
        self._set_status(f"listening on UDP {port}")
        return True

    def stop(self):
        self._publish_timer.stop()
        self._level_timer.stop()
        if self.sock is not None:
            self.sock.close()
            self.sock.deleteLater()
            self.sock = None
        self.clients.clear()
        self._set_status("off")

    def _set_status(self, s):
        self.status = s
        self.status_changed.emit(s)

    def _read(self):
        while self.sock is not None and self.sock.hasPendingDatagrams():
            dg = self.sock.receiveDatagram()
            key = (dg.senderAddress().toString(), dg.senderPort())
            try:
                msgs = osc_remote.decode(bytes(dg.data()))
            except ValueError:
                continue
            new = key not in self.clients
            self.clients[key] = time.monotonic()
            if new:
                self._set_status(self._client_text())
            for address, args in msgs:
                self.received.emit(address, args)
                if address.startswith("/send"):
                    self.send_state((address, args), key)

    def _client_text(self):
        n = len(self.clients)
        port = self.sock.localPort() if self.sock else 0
        return f"listening on UDP {port} · {n} controller{'s' if n != 1 else ''}"

    def _live_clients(self):
        now = time.monotonic()
        gone = [k for k, t in self.clients.items() if now - t > CLIENT_TIMEOUT_S]
        for k in gone:
            del self.clients[k]
        if gone:
            self._set_status(self._client_text())
        return list(self.clients)

    def _send(self, packets, clients):
        if self.sock is None:
            return
        for host, port in clients:
            addr = QHostAddress(host)
            for p in packets:
                self.sock.writeDatagram(p, addr, port)

    # ---- state
    def send_state(self, request, client):
        """Answer a /sendall, /sendstate, /sendsubmix or /sendchan request from one client."""
        values = osc_remote.select(self.snapshot(), request)
        self._send(osc_remote.encode_bundles(osc_remote.messages(values)), [client])

    def publish(self):
        """Send what changed since the last call to every connected controller."""
        clients = self._live_clients()
        snap = self.snapshot()
        diff = osc_remote.changes(self.sent, snap)
        self.sent = snap
        if diff and clients:
            self._send(osc_remote.encode_bundles(osc_remote.messages(diff)), clients)

    def add_peaks(self, kind, channels, peaks):
        """Linear peaks read from the engine; the highest per channel is kept until sent."""
        if not self.levels_on or not self.clients:
            return
        seg = osc_remote.LEVEL_SEGS[kind]
        for c in channels:
            a = f"/level/{seg}/{c}"
            db = osc_remote.level_db(peaks[c])
            if db > self._levels.get(a, osc_remote.LEVEL_FLOOR_DB - 1):
                self._levels[a] = db

    def _send_levels(self):
        if not self.levels_on:
            return
        clients = self._live_clients()
        out = {}
        for a, db in self._levels.items():
            if abs(db - self._levels_sent.get(a, 1e9)) >= LEVEL_STEP_DB:
                out[a] = round(db, 1)
                self._levels_sent[a] = db
        self._levels.clear()
        if out and clients:
            self._send(osc_remote.encode_bundles(osc_remote.messages(out)), clients)


class RemotePanel(Section):
    """Sidebar section: switch the remote on, its port, levels, and start at login.
    Emits changed() after editing st["remote"]."""
    changed = Signal()

    def __init__(self, st, device_key, parent=None):
        super().__init__("NETWORK REMOTE", parent)
        self.st = st
        self.device_key = device_key
        r = remote_settings(st)
        g = QGridLayout(self)
        self.enabled = QCheckBox("Allow OSC control")
        self.enabled.setToolTip("Speaks TotalMix FX's Global OSC, so TotalMix remotes (Q-SYS, "
                                "Companion, Stream Deck, TouchOSC) work. Anyone on the network "
                                "can change the mix while this is on.")
        self.enabled.setChecked(r["enabled"])
        self.enabled.toggled.connect(self._edit)
        g.addWidget(self.enabled, 0, 0, 1, 2)
        g.addWidget(key_label("UDP port"), 1, 0)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(r["port"])
        self.port.editingFinished.connect(self._edit)
        g.addWidget(self.port, 1, 1)
        g.addWidget(key_label("Status"), 2, 0)
        self.status = QLabel()
        self.status.setWordWrap(True)
        g.addWidget(self.status, 2, 1)
        self.levels = QCheckBox("Send peak levels")
        self.levels.setChecked(r["levels"])
        self.levels.toggled.connect(self._edit)
        g.addWidget(self.levels, 3, 0, 1, 2)
        self.autostart = QCheckBox("Start at login, no window")
        self.autostart.setToolTip("Keeps the mixer, the ARC USB and the network remote running "
                                  "after login. Opening Openface Mixer shows the window; Ctrl+Q "
                                  "quits it.")
        self.autostart.setChecked(autostart_enabled())
        self.autostart.toggled.connect(lambda on: set_autostart(on, self.device_key))
        g.addWidget(self.autostart, 4, 0, 1, 2)
        g.setColumnStretch(1, 1)
        self.set_status("off")

    def _edit(self, *_):
        r = remote_settings(self.st)
        r["enabled"] = self.enabled.isChecked()
        r["port"] = self.port.value()
        r["levels"] = self.levels.isChecked()
        self.changed.emit()

    def set_status(self, s):
        if s.startswith("error"):
            self.status.setText(light(RED, "Error", False))
            self.status.setToolTip(s)
        elif s == "off":
            self.status.setText(light(OFF, "Off", False))
            self.status.setToolTip("")
        else:
            self.status.setText(light(CYAN, s.replace("listening on UDP ", "Listening · ", 1)))
            self.status.setToolTip(s)
