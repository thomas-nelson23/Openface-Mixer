"""Opens the real main window for every device, offscreen, and clicks through it.

The other tests never import Qt, so this is where a typo in the window code is caught. The
window runs in a child process (this file with --child), which gives itself a throwaway HOME
before it imports the app, so it reads and writes no real config, and stubs the engine inside
that process: no engine, no systemd, no amixer or pactl, no ARC USB. Skipped where PySide6 is
not installed.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import traceback
import unittest
from array import array
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Qt warnings that say nothing about our code
HARMLESS = ("This plugin does not support",)     # the offscreen platform: no window manager


def pump(app):
    """Run what is queued, including the deletion of closed windows."""
    from PySide6.QtCore import QEvent
    for _ in range(3):
        app.processEvents()
        app.sendPostedEvents(None, QEvent.DeferredDelete)


def exercise(app, w, check):
    """Click through one device's window. check(condition, what) records a failure."""
    from PySide6.QtWidgets import QPushButton
    from openface_mixer import engine, updater
    from openface_mixer.channel_settings import ChannelSettings
    from openface_mixer.remote_link import autostart_file

    dev = w.dev
    w.show()
    pump(app)
    check(w.row_in.strips and w.row_play.strips and w.row_out.strips, "a row has no strips")
    for view in (1, 0):                             # Matrix, back to Mixer
        w.view_btns[view].click()
        pump(app)
        check(w.tabs.currentIndex() == view, f"view {view} was not shown")
    for want_open in (False, True):                 # the settings sidebar
        w.settings_btn.click()
        pump(app)
        check(w.sidebar.is_open() == want_open, f"sidebar open is not {want_open}")
    w.settings.keys_btn.click()                     # the shortcut list at its bottom
    pump(app)
    check(w.settings.keys_list.isVisible(), "the shortcut list did not open")
    w.settings.keys_btn.click()
    pump(app)
    # a hidden window stops polling for itself and starts again when it is shown
    polls = (w._meter_timer, w._hw_timer, w._profile_timer)
    check(all(t.isActive() for t in polls), "a poll does not run while the window is shown")
    w.hide()
    pump(app)
    check(not any(t.isActive() for t in polls) and w._engine_timer.isActive(),
          "the polls of a hidden window")
    w.show()
    pump(app)
    check(all(t.isActive() for t in polls), "a poll did not start again when shown")
    # the engine is enabled for login once it reports its interface, not before
    w.poll_engine()
    check(w.engine.enabled == 0, "the engine was enabled before the interface was seen")
    w.engine.hw = "active"
    w.poll_engine()
    w.poll_engine()
    check(w.engine.enabled == 1, f"the engine was enabled {w.engine.enabled} times")
    # what the other timers do, and every engine state the Status section explains
    w.poll_hw()
    w.poll_profile()
    w.update_meters()
    status = w.engine.status()
    for packaged in (True, False):                  # a package's hints differ from a checkout's
        updater.packaged = lambda: packaged
        for state in engine.HW_STATES:
            w.settings.update_hw_mixer({**status, "hw": state})
    w.settings.update_hw_mixer(None)
    # the login entry names the command by its full path (or as found on PATH in a checkout)
    w.remote_panel.autostart.click()
    entry = autostart_file(dev.key)
    check(entry.is_file() and f'" --background --device {dev.key}\n' in entry.read_text(),
          "the login entry was not written")
    w.remote_panel.autostart.click()
    check(not entry.exists(), "the login entry was not removed")

    strips = [w.row_in.strips[0], w.row_out.strips[0]]
    if dev.fx is not None:                          # EQ, Dynamics, Reverb / Echo, Room EQ
        for strip in strips:
            strip.eq_btn.click()
            strip.dyn_btn.click()
        w.fx_btn.click()
        pump(app)
        want = {("eq", s.key) for s in strips} | {("dyn", s.key) for s in strips} | {("unit", "")}
        check(set(w.fx_panels) == want, f"effects panels open: {sorted(w.fx_panels)}")
        check(all(p.window() is w and p.isVisible() for p in w.fx_panels.values()),
              "an effects panel is not in its row")
        strips[0].eq_btn.click()                    # the button closes its panel again
        pump(app)
        check(("eq", strips[0].key) not in w.fx_panels, "EQ did not close its panel")
        strips[0].eq_btn.click()
        pump(app)
    for strip in strips:                            # the channel settings popup, where there is one
        if strip.settings_btn.isHidden():
            continue
        strip.settings_btn.click()
        pump(app)
        popups = w.findChildren(ChannelSettings)
        check(len(popups) == 1, f"{len(popups)} channel settings popups for {strip.name}")
        for popup in popups:
            room = [b for b in popup.findChildren(QPushButton) if b.text().startswith("Room EQ")]
            if room:
                room[0].click()                     # closes the popup and opens the panel
            else:
                popup.close()
        pump(app)
        check(not w.findChildren(ChannelSettings), "the channel settings popup stayed open")
    if dev.fx is not None and dev.fx.room_eq:
        check(("room", strips[1].key) in w.fx_panels, "the Room EQ panel did not open")
    for panel in list(w.fx_panels.values()):
        check(panel.isVisible(), f"{panel.windowTitle()} is not visible")
        panel.close()
    pump(app)
    check(not w.fx_panels, f"effects panels left open: {sorted(w.fx_panels)}")
    check(not w.fx_btn.isChecked(), "the FX button stayed lit")
    fold_rows(app, w, check)
    for rate in (96000, 192000):                    # the double and quad speed channel layouts
        w.engine.rate = rate
        w.poll_engine()
        pump(app)
        check(w.rate == rate and w.row_in.strips and w.row_out.strips, f"no strips at {rate} Hz")
        fold_rows(app, w, check)


def fold_rows(app, w, check):
    """Fold each row to names and meters and unfold it again, then all three at once."""
    rows = (w.row_in, w.row_play, w.row_out)
    for folding in (*((r,) for r in rows), rows):
        shown = [s.fader.isVisible() for r in rows for s in r.strips]
        for row in folding:
            row.fold_btn.click()
        pump(app)
        w.update_meters()
        for row in folding:
            check(row.folded and row.scroll.height() < row.strips[0].width_px * 2
                  and all(s.meter.isVisible() and not s.fader.isVisible() for s in row.strips),
                  f"{row.title} did not fold at {w.rate} Hz")
        for row in folding:
            row.fold_btn.click()
        pump(app)
        check(not any(r.folded for r in rows)
              and shown == [s.fader.isVisible() for r in rows for s in r.strips],
              f"{[r.title for r in folding]} did not unfold at {w.rate} Hz")
    check(w.st["folded"] == [], f"folded rows left in the state: {w.st['folded']}")


def drive(home):
    """The child process: (devices opened, problems found as text)."""
    os.environ.update(HOME=home, XDG_CONFIG_HOME=f"{home}/.config",
                      XDG_CACHE_HOME=f"{home}/.cache", QT_QPA_PLATFORM="offscreen")
    os.environ.pop("QT_SCALE_FACTOR", None)
    sys.path.insert(0, str(REPO))
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication
    from openface_mixer import app as ofm_app, arc, config, engine
    from openface_mixer.devices import DEVICES
    from openface_mixer.model import N_OUT, N_SRC
    from openface_mixer.theme import apply_theme

    problems = []
    current = ["startup"]

    def problem(text):
        problems.append(f"{current[0]}: {text}")

    def qt_message(kind, _context, message):
        if kind != QtMsgType.QtDebugMsg and kind != QtMsgType.QtInfoMsg \
                and not any(h in message for h in HARMLESS):
            problem(f"Qt: {message}")

    class StubEngine(engine.Engine):
        """An engine that runs, finds its interface when told to, and never leaves this
        process."""
        rate = 48000
        hw = "no-device"
        enabled = 0

        def attach(self):
            return False                # no shared memory: the writes do nothing

        def outdated(self):
            return False

        def status(self):
            return {"processing": True, "rate": self.rate, "sink_linked": True, "hw": self.hw,
                    "hw_nodes": 12, "hw_levels": True, "dev_status": 0, "dev_status2": 0}

        def read_peaks(self):
            src, out = array("f", [0.25] * N_SRC), array("f", [0.25] * N_OUT)
            return src, out, src, out, src, out

        def start(self):
            return True, "stub"

        def stop(self):
            pass

        def enable(self):
            self.enabled += 1

    def no_subprocess(*_args, **_kwargs):
        raise OSError("the smoke test runs no other programs")

    ofm_app.Engine = StubEngine
    subprocess.run = subprocess.Popen = no_subprocess       # amixer, pactl, systemctl, git
    arc.find_port = lambda: None                            # leave a connected ARC USB alone
    # exceptions in slots and event handlers end up here instead of stopping the test
    sys.excepthook = lambda *exc: problem("".join(traceback.format_exception(*exc)))
    qInstallMessageHandler(qt_message)

    app = QApplication(["openface-mixer"])
    for key, dev in DEVICES.items():
        current[0] = key
        try:
            apply_theme(app, 1.0)
            w = ofm_app.MainWindow(dev)
            exercise(app, w, lambda ok, what: ok or problem(what))
            w.close()
            if not config.state_file(dev).is_file():
                problem("closing did not save the mix")
            w.arc.disconnect_port()         # as main() does between two devices' windows
            w.remote.stop()
            w.deleteLater()
            pump(app)
        except Exception:                   # noqa: BLE001 - reported with the device's name
            problem(traceback.format_exc())
    return len(DEVICES), problems


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is not installed")
class GuiSmokeTest(unittest.TestCase):
    def test_every_device_opens_and_closes_cleanly(self):
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child"],
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, f"\n{r.stdout}{r.stderr}")
        self.assertIn("devices opened and closed cleanly", r.stdout)


if __name__ == "__main__":
    if "--child" not in sys.argv:
        unittest.main()
    with tempfile.TemporaryDirectory() as tmp:
        count, found = drive(tmp)
    print("\n".join(found) if found else f"{count} devices opened and closed cleanly")
    sys.exit(1 if found else 0)
