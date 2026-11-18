"""One window per device: background mode kept across an update restart, a new UI scale and a
device switch. The windows and the socket are stand-ins here; nothing is started or exec'd."""
import unittest
from unittest import mock

from openface_mixer.devices import DEVICES
from tests.test_controls import QApplication, WindowCase


class FakeWindow:
    """What run() uses of a MainWindow."""

    def __init__(self, dev, background):
        self.dev, self.background = dev, background
        self.shown, self.switch_to, self.reopen_geometry = False, None, None
        self.arc, self.remote = mock.Mock(), mock.Mock()

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
