"""The OSC network remote (osc_remote.py) on a UDP socket, and its settings-panel box.

RemoteLink listens on a UDP port and answers each controller at the address and port its packets
came from, the way controllers that use one socket for both directions (Q-SYS, Companion) expect.
A controller counts as connected for CLIENT_TIMEOUT_S after its last packet; they all poll with
/sendstate or similar well within that. Controllers that listen on a port of their own (TouchOSC,
TotalMix's "Port outgoing") get everything at the Send to host and port as well. MainWindow
decides what a received message does.
"""
import os
import socket
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QLineEdit, QSpinBox

from . import APP_ID, APP_NAME
from . import osc_remote, tray
from .config import launcher, load_ui, save_ui
from .osc_remote import remote_settings  # noqa: F401  (MainWindow imports it from here)
from .settings_panel import CYAN, OFF, RED, WideList, light
from .theme import Section, key_label

CLIENT_TIMEOUT_S = 60
PUBLISH_MS = 150          # how often changes are sent
LEVEL_MS = 100            # how often peak levels are sent (10 per second, like TotalMix)
LEVEL_STEP_DB = 0.5       # a level is resent once it moved this much
AUTOSTART_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart"


def autostart_file(device_key):
    """The XDG autostart entry for one device: each device has its own window, so each can be
    started at login on its own."""
    return AUTOSTART_DIR / f"{APP_ID}-{device_key}.desktop"


def autostart_enabled(device_key):
    return autostart_file(device_key).is_file()


def set_autostart(on, device_key, device_name):
    """Start device_key's Openface Mixer at login without a window."""
    f = autostart_file(device_key)
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "[Desktop Entry]\nType=Application\n"
            f"Name={APP_NAME} ({device_name}, background)\n"
            "Comment=Keeps the mixer and its network remote running without a window\n"
            f'Exec="{launcher()}" --background --device {device_key}\n'
            "Icon=audio-card\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
    else:
        f.unlink(missing_ok=True)


def resolve_target(host, port, lookup=True):
    """(address text, port) for the Send to host, or None if it is empty or unknown. Without
    lookup only an address is taken: a name can take as long as the name server does."""
    host = (host or "").strip()
    if not host or not 0 < port < 65536:
        return None
    try:
        return socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM,
                                  flags=0 if lookup else socket.AI_NUMERICHOST)[0][4][0], port
    except (OSError, IndexError, UnicodeError):     # UnicodeError: no host name ("a..b")
        return None


class RemoteLink(QObject):
    """received(address, args) is emitted for each message; MainWindow answers with
    state requests through send_state() and publishes its changes with publish()."""
    received = Signal(str, list)
    status_changed = Signal(str)
    _looked_up = Signal(str, int, object)  # a host name's lookup has finished on its thread

    def __init__(self, snapshot, parent=None):
        """snapshot: callable returning osc_remote.snapshot() for the current state."""
        super().__init__(parent)
        self.snapshot = snapshot
        self.sock = None
        self.clients = {}                  # (address, port) -> time of last packet
        self.target = None                 # (address, port) everything is also sent to
        self.target_error = None           # the Send to host, if it could not be resolved
        self._wanted = ("", 0)             # the Send to host and port as last set
        self._looked_up.connect(self._lookup_done)
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

    def set_target(self, host, port):
        """Also send state and levels to host:port (empty host: only to the controllers). An
        address is used at once. A name is looked up on a thread of its own, so a slow name
        server can't freeze the window; until the answer is in, a new name gets nothing and
        an unchanged one keeps its address."""
        host = (host or "").strip()
        changed, self._wanted = (host, port) != self._wanted, (host, port)
        target = resolve_target(host, port, lookup=False)
        if target is None and host:
            threading.Thread(target=self._lookup, args=(host, port), name="ofm-lookup",
                             daemon=True).start()
            if changed:
                self._use_target(None, None)
        else:
            self._use_target(target, None)

    def _lookup(self, host, port):
        target = resolve_target(host, port)
        try:
            self._looked_up.emit(host, port, target)
        except RuntimeError:
            pass                           # the window has gone meanwhile

    def _lookup_done(self, host, port, target):
        if (host, port) == self._wanted:   # not the answer to an older question
            self._use_target(target, None if target else host)

    def _use_target(self, target, error):
        self.target_error = error
        if target != self.target:
            self.target = target
            self.sent = {}                 # the new target gets the whole state next
        if self.sock is not None and self.status != "off":
            self._set_status(self._client_text())

    def _targets(self):
        """Everyone to send to: the live controllers and the Send to host."""
        targets = self._live_clients()
        if self.target is not None and self.target not in targets:
            targets.append(self.target)
        return targets

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
        """Send what changed since the last call to every connected controller. With nobody to
        tell, nothing is computed; the first controller then gets what changed meanwhile."""
        targets = self._targets()
        if not targets:
            return
        snap = self.snapshot()
        diff = osc_remote.changes(self.sent, snap)
        self.sent = snap
        if diff:
            self._send(osc_remote.encode_bundles(osc_remote.messages(diff)), targets)

    def wants_levels(self):
        """True while peak levels go out to someone: the window keeps reading the meters for
        that, shown or not."""
        return self.sock is not None and self.levels_on and bool(self.clients or self.target)

    def add_peaks(self, kind, channels, peaks):
        """Linear peaks read from the engine; the highest per channel is kept until sent."""
        if not self.wants_levels():
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
        targets = self._targets()
        out = {}
        for a, db in self._levels.items():
            if abs(db - self._levels_sent.get(a, 1e9)) >= LEVEL_STEP_DB:
                out[a] = round(db, 1)
                self._levels_sent[a] = db
        self._levels.clear()
        if out and targets:
            self._send(osc_remote.encode_bundles(osc_remote.messages(out)), targets)


class RemotePanel(Section):
    """Sidebar section: switch the remote on, its port, levels, start at login and the tray
    icon. Emits changed() after editing st["remote"], tray_changed() after saving the Tray icon
    setting."""
    changed = Signal()
    tray_changed = Signal()

    def __init__(self, st, dev, parent=None):
        super().__init__("NETWORK REMOTE", parent)
        self.st = st
        self.dev = dev
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
        self.port.setRange(osc_remote.PORTS["port"][0], osc_remote.PORTS["port"][-1])
        self.port.setValue(r["port"])
        self.port.editingFinished.connect(self._edit)
        g.addWidget(self.port, 1, 1)
        g.addWidget(key_label("Send to"), 2, 0)
        self.host = QLineEdit(r["host"])
        self.host.setPlaceholderText("controllers only")
        self.host.setToolTip("Replies always go back to where a controller sends from. A "
                             "controller that listens on a port of its own (TouchOSC, TotalMix's "
                             "Port outgoing) also gets everything at this host and port.")
        self.host.editingFinished.connect(self._edit)
        g.addWidget(self.host, 2, 1)
        g.addWidget(key_label("Port out"), 3, 0)
        self.out_port = QSpinBox()
        self.out_port.setRange(osc_remote.PORTS["out_port"][0], osc_remote.PORTS["out_port"][-1])
        self.out_port.setValue(r["out_port"])
        self.out_port.editingFinished.connect(self._edit)
        g.addWidget(self.out_port, 3, 1)
        g.addWidget(key_label("Status"), 4, 0)
        self.status = QLabel()
        self.status.setWordWrap(True)
        g.addWidget(self.status, 4, 1)
        self.levels = QCheckBox("Send peak levels")
        self.levels.setChecked(r["levels"])
        self.levels.toggled.connect(self._edit)
        g.addWidget(self.levels, 5, 0, 1, 2)
        self.autostart = QCheckBox("Start at login, no window")
        self.autostart.setToolTip(f"Starts the {dev.name.replace('RME ', '')} mixer at login and "
                                  "keeps it, the ARC USB and the network remote running without "
                                  "a window. The tray icon or opening Openface Mixer shows "
                                  "the window; Quit in the tray menu or Ctrl+Q quits it.")
        self.autostart.setChecked(autostart_enabled(dev.key))
        if self.autostart.isChecked():      # keep the entry's command path current
            set_autostart(True, dev.key, dev.name)
        self.autostart.toggled.connect(lambda on: set_autostart(on, self.dev.key, self.dev.name))
        g.addWidget(self.autostart, 6, 0, 1, 2)
        g.addWidget(key_label("Tray icon"), 7, 0)
        self.tray_mode = WideList()
        for value, text in tray.MODES:
            self.tray_mode.addItem(text, value)
        self.tray_mode.setCurrentIndex(self.tray_mode.findData(tray.mode(load_ui().get("tray"))))
        self.tray_mode.setToolTip("When Openface Mixer has an icon in the system tray. While it "
                                  "is there, closing the window only hides it. Needs a desktop "
                                  "with a system tray.")
        self.tray_mode.activated.connect(self._tray_mode_chosen)
        g.addWidget(self.tray_mode, 7, 1)
        g.setColumnStretch(1, 1)
        self.set_status("off")

    def _edit(self, *_):
        r = remote_settings(self.st)
        r["enabled"] = self.enabled.isChecked()
        r["port"] = self.port.value()
        r["host"] = self.host.text().strip()
        r["out_port"] = self.out_port.value()
        r["levels"] = self.levels.isChecked()
        self.changed.emit()

    def _tray_mode_chosen(self, i):
        save_ui(tray=self.tray_mode.itemData(i))
        self.tray_changed.emit()

    def set_status(self, s, target_error=None):
        if target_error:
            self.status.setText(light(RED, "Unknown host"))
            self.status.setToolTip(f"{s}; Send to host “{target_error}” could not be resolved")
        elif s.startswith("error"):
            self.status.setText(light(RED, "Error"))
            self.status.setToolTip(s)
        elif s == "off":
            self.status.setText(light(OFF, "Off"))
            self.status.setToolTip("")
        else:
            self.status.setText(light(CYAN, s.replace("listening on UDP ", "Listening · ", 1)))
            self.status.setToolTip(s)
