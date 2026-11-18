"""One window per device: commands for desktop shortcuts over its socket, and background mode
kept across an update restart, a new UI scale and a device switch. Sockets live in a temporary
folder; nothing is exec'd and no real window is reached."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import commands
from openface_mixer.devices import DEVICES
from tests.test_controls import QApplication, WindowCase

REPO = Path(__file__).resolve().parent.parent


class CommandsTest(unittest.TestCase):
    def test_from_args(self):
        self.assertEqual(commands.from_args(["--device", "ff802", "--dim"]), ["dim"])
        self.assertEqual(commands.from_args(["--mute-main", "--recall", "3", "--show"]),
                         ["mute-main", "recall 3", "show"])
        self.assertEqual(commands.from_args(["--background", "--device", "aio"]), [])
        for bad in (["--recall"], ["--recall", "9"], ["--recall", "0"], ["--recall", "x"]):
            with self.assertRaises(ValueError):
                commands.from_args(bad)

    def test_parse(self):
        self.assertEqual(commands.parse(b"show"), ("show", None))
        self.assertEqual(commands.parse(b" dim \r"), ("dim", None))
        self.assertEqual(commands.parse(b"mute-main"), ("mute-main", None))
        self.assertEqual(commands.parse(b"recall 8"), ("recall", 7))
        for junk in (b"", b"recall", b"recall 9", b"recall 1 2", b"quit", b"dim dim",
                     b"\xff\xfe", b"SHOW", b"recall \xd9\xa3"):
            self.assertIsNone(commands.parse(junk), junk)

    @unittest.skipUnless(QApplication, "needs PySide6")
    def test_no_mixer_running(self):
        """A command with nothing running says so and exits instead of opening a window."""
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, XDG_CONFIG_HOME=f"{tmp}/config", TMPDIR=tmp,
                       XDG_CACHE_HOME=f"{tmp}/cache", QT_QPA_PLATFORM="offscreen")
            for args, message in ((["--dim"], "the RME Fireface 400 mixer is not running"),
                                  (["--recall", "9"], "--recall: a preset number from 1 to 8")):
                p = subprocess.run([sys.executable, "-m", "openface_mixer", "--device", "ff400"]
                                   + args, cwd=REPO, env=env, capture_output=True, text=True,
                                   timeout=60)
                self.assertEqual(p.returncode, 1, p.stderr)
                self.assertIn(f"openface-mixer: {message}", p.stderr)


class FakeWindow:
    """What run() uses of a MainWindow."""

    def __init__(self, dev, background):
        self.dev, self.background = dev, background
        self.shown, self.switch_to, self.reopen_geometry = False, None, None
        self.arc, self.remote, self.tray = mock.Mock(), mock.Mock(), mock.Mock()

    def show(self):
        self.shown = True

    def setGeometry(self, geometry):
        pass

    def deleteLater(self):
        pass


@unittest.skipUnless(QApplication, "needs PySide6")
class RunTest(unittest.TestCase):
    def run_windows(self, background, show, switches, running=()):
        """Run app.run() with each app.exec() ending in the next of switches (a device key the
        window asked for, or None to quit). running: devices that have a window elsewhere.
        Returns (exit code, [(device, background, shown)] of the windows made)."""
        from openface_mixer import app
        windows = []
        script = iter(switches)

        def make(dev, bg):
            windows.append(FakeWindow(dev, bg))
            return windows[-1]

        def exec_():
            windows[-1].switch_to = next(script)
            return 0

        fake_app = mock.Mock(exec=exec_)
        with mock.patch.object(app, "MainWindow", make), \
                mock.patch.object(app, "tell_running",
                                  lambda dev, line="show": dev.key in running), \
                mock.patch.object(app, "listen", lambda a, dev: mock.Mock()), \
                mock.patch.object(app, "apply_theme"), \
                mock.patch.object(app, "save_device_choice"):
            code = app.run(fake_app, DEVICES["digiface"], background, show)
        return code, [(w.dev.key, w.background, w.shown) for w in windows]

    def test_a_background_instance_stays_one(self):
        # started at login, opened, a new UI scale (the same device reopens), then another device
        code, made = self.run_windows(True, False, ["digiface", "ff802", None])
        self.assertEqual(code, 0)
        self.assertEqual(made, [("digiface", True, False), ("digiface", True, True),
                                ("ff802", True, True)])

    def test_a_normal_window_stays_normal(self):
        _code, made = self.run_windows(False, True, ["digiface", None])
        self.assertEqual(made, [("digiface", False, True), ("digiface", False, True)])

    def test_started_with_show(self):
        _code, made = self.run_windows(True, True, [None])
        self.assertEqual(made, [("digiface", True, True)])

    def test_a_device_with_a_window_already_is_shown_instead(self):
        code, made = self.run_windows(False, True, ["ff802"], running=("ff802",))
        self.assertEqual((code, made), (0, [("digiface", False, True)]))
        code, made = self.run_windows(False, True, [], running=("digiface",))
        self.assertEqual((code, made), (0, []))


@unittest.skipUnless(QApplication, "needs PySide6")
class SocketTest(unittest.TestCase):
    """The protocol end to end, over real sockets in a temporary folder."""

    def setUp(self):
        from openface_mixer import app
        self.app = QApplication.instance() or QApplication([])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(app, "socket_name", lambda dev: f"{tmp.name}/{dev.key}")
        patch.start()
        self.addCleanup(patch.stop)
        self.got = {}

    def start(self, key):
        """A running window for this device, that notes what it is told."""
        from openface_mixer import app
        server = app.listen(self.app, DEVICES[key])
        self.addCleanup(server.deleteLater)
        self.addCleanup(server.close)
        got = self.got.setdefault(key, [])
        app.serve(server, lambda cmd, slot: got.append((cmd, slot)))

    def wait(self, key, n):
        end = time.monotonic() + 5
        while len(self.got[key]) < n and time.monotonic() < end:
            self.app.processEvents()
        self.app.processEvents()
        return self.got[key]

    def send_raw(self, key, data):
        from PySide6.QtNetwork import QLocalSocket
        from openface_mixer import app
        sock = QLocalSocket()
        sock.connectToServer(app.socket_name(DEVICES[key]))
        self.assertTrue(sock.waitForConnected(1000))
        sock.write(data)
        sock.waitForBytesWritten(1000)
        sock.disconnectFromServer()

    def test_each_command(self):
        from openface_mixer import app
        self.start("digiface")
        self.assertTrue(app.tell_running(DEVICES["digiface"]))      # a plain second start
        for lines in (["dim"], ["mute-main"], ["recall 3"], ["recall 1", "dim"]):
            self.assertTrue(app.tell_running(DEVICES["digiface"], lines))
        self.assertEqual(self.wait("digiface", 6), [
            ("show", None), ("dim", None), ("mute-main", None), ("recall", 2), ("recall", 0),
            ("dim", None)])

    def test_junk_is_dropped(self):
        self.start("digiface")
        self.send_raw("digiface", b"hello\n\xff\x00\nrecall 9\n" + b"x" * 5000 + b"\ndim\n")
        self.send_raw("digiface", b"")                  # connects and says nothing
        self.send_raw("digiface", b"show")              # no end of line: not a command
        self.send_raw("digiface", b"mute-main\n")
        self.assertEqual(sorted(self.wait("digiface", 2)), [("dim", None), ("mute-main", None)])
        end = time.monotonic() + 0.3
        while time.monotonic() < end:
            self.app.processEvents()
        self.assertEqual(len(self.got["digiface"]), 2)

    def test_nothing_running(self):
        from openface_mixer import app
        self.assertFalse(app.tell_running(DEVICES["digiface"], ["dim"]))

    def test_two_devices(self):
        from openface_mixer import app
        self.start("digiface")
        self.start("ff802")
        self.assertTrue(app.tell_running(DEVICES["ff802"], ["dim"]))
        self.assertTrue(app.tell_running(DEVICES["digiface"], ["recall 2"]))
        self.assertEqual(self.wait("ff802", 1), [("dim", None)])
        self.assertEqual(self.wait("digiface", 1), [("recall", 1)])
        self.assertFalse(app.tell_running(DEVICES["ucx2"]))


class CommandWindowTest(WindowCase):
    """What each command does in the window: the same as its button."""

    def test_dim_and_main_mute_toggle(self):
        from openface_mixer import control_room
        w = self.w
        cr = control_room.control_room(w.st)
        main = cr["main"]
        dim, muted = cr["dim"], w.st["out"][main]["mute"]
        w.command("dim")
        self.assertEqual(cr["dim"], not dim)
        self.assertEqual(w.control.buttons["dim"].isChecked(), not dim)
        w.command("dim")
        self.assertEqual(cr["dim"], dim)
        w.command("mute-main")
        self.assertEqual(w.st["out"][main]["mute"], not muted)
        strip = next(s for s in w.row_out.strips if s.pair == main)
        self.assertEqual(strip.mute.isChecked(), not muted)
        w.command("mute-main")
        self.assertEqual(w.st["out"][main]["mute"], muted)

    def test_recall_only_a_filled_slot(self):
        from openface_mixer.model import extract_mix
        w = self.w
        w.presets.store(4, "Late night", extract_mix(w.st))
        self.addCleanup(w.presets.clear, 4)
        self.addCleanup(w.presets.clear, 5)
        with mock.patch.object(w, "replace_mix") as replace:
            w.command("recall", 5)
            replace.assert_not_called()
            w.command("recall", 4)
            replace.assert_called_once()

    def test_show(self):
        w = self.w
        w.hide()
        self.settle()
        w.command("show")
        self.settle()
        self.assertTrue(w.isVisible())


class RestartTest(WindowCase):
    def restart_args(self, background):
        from openface_mixer import app
        w = self.w
        self.addCleanup(setattr, w, "background", w.background)
        w.background = background
        with mock.patch.object(app.QProcess, "startDetached", return_value=(True, 1)) as start, \
                mock.patch.object(QApplication.instance(), "quit"):
            w.restart_app()
        self.addCleanup(w.apply_remote_settings)
        (program, args, _cwd), _kw = start.call_args
        return args

    def test_restart_keeps_background_mode(self):
        self.assertEqual(self.restart_args(True),
                         ["-m", "openface_mixer", "--device", "digiface", "--background",
                          "--show"])
        self.assertEqual(self.restart_args(False), ["-m", "openface_mixer", "--device",
                                                    "digiface"])

    def test_switching_from_background_to_a_running_device_keeps_this_one(self):
        from openface_mixer import app
        w = self.w
        self.addCleanup(setattr, w, "background", w.background)
        w.background = True
        with mock.patch.object(app, "tell_running", return_value=True) as told, \
                mock.patch.object(app.QApplication, "quit") as quit_:
            w.settings.device.setCurrentIndex(w.settings.device.findData("ff802"))
            w.switch_device("ff802")
        told.assert_called_once_with(DEVICES["ff802"])
        quit_.assert_not_called()
        self.assertIsNone(w.switch_to)
        self.assertFalse(w.isVisible())             # hidden, still running
        self.assertEqual(w.settings.device.currentData(), "digiface")
        w.show()
        self.settle()


if __name__ == "__main__":
    unittest.main()
