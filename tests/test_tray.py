"""The tray icon: when it shows, its menu and its state (tray.py, no Qt), and the real window
with a stand-in tray (the offscreen platform has none)."""
import json
import unittest
from unittest import mock

from openface_mixer import config, tray
from tests.test_controls import QApplication, WindowCase

RUNNING = {"processing": True, "rate": 48000, "hw": "active"}
NAMES = ["Tracking", "Mix", None, "Late night", None, None, None, None]


def by_text(items):
    return {it.text: it for it in items if it.kind != "separator"}


class ModeTest(unittest.TestCase):
    def test_when_it_shows(self):
        self.assertTrue(tray.wanted("background", background=True))
        self.assertFalse(tray.wanted("background", background=False))
        self.assertTrue(tray.wanted("always", background=False))
        self.assertFalse(tray.wanted("never", background=True))

    def test_a_damaged_setting_is_the_default(self):
        for bad in (None, 7, "sometimes", ["always"], {"a": 1}):
            self.assertEqual(tray.mode(bad), "background")
        self.assertTrue(tray.wanted(["always"], background=True))
        self.assertFalse(tray.wanted(["always"], background=False))


class MenuTest(unittest.TestCase):
    def menu(self, **kw):
        args = dict(window_shown=False, dim=False, main_muted=False, slot_names=NAMES,
                    active_slot=None)
        args.update(kw)
        return tray.menu(**args)

    def test_entries(self):
        self.assertEqual([(it.kind, it.text) for it in self.menu()], [
            ("action", "Show mixer"), ("separator", ""), ("check", "Dim"),
            ("check", "Mute main out"), ("menu", "Presets"), ("separator", ""),
            ("action", "Quit Openface Mixer")])
        items = by_text(self.menu())
        self.assertEqual(items["Show mixer"].command, ("show", None))
        self.assertEqual(items["Dim"].command, ("dim", None))
        self.assertEqual(items["Mute main out"].command, ("mute-main", None))
        self.assertEqual(items["Quit Openface Mixer"].command, ("quit", None))
        self.assertEqual(self.menu(window_shown=True)[0][1:3], (("hide", None), "Hide mixer"))

    def test_switches_show_the_state(self):
        self.assertTrue(by_text(self.menu(dim=True))["Dim"].checked)
        self.assertFalse(by_text(self.menu(dim=True))["Mute main out"].checked)
        self.assertTrue(by_text(self.menu(main_muted=True))["Mute main out"].checked)

    def test_only_filled_presets(self):
        presets = by_text(self.menu(active_slot=1))["Presets"]
        self.assertTrue(presets.enabled)
        self.assertEqual([(p.kind, p.text, p.command, p.checked) for p in presets.children], [
            ("radio", "1  Tracking", ("recall", 0), False),
            ("radio", "2  Mix", ("recall", 1), True),
            ("radio", "4  Late night", ("recall", 3), False)])
        # a cleared slot that was lit is not listed, and nothing is ticked
        presets = by_text(self.menu(active_slot=2))["Presets"]
        self.assertFalse(any(p.checked for p in presets.children))
        presets = by_text(self.menu(slot_names=[None] * 8))["Presets"]
        self.assertEqual((presets.enabled, presets.children), (False, ()))

    def test_same_state_same_menu(self):
        self.assertEqual(self.menu(dim=True, active_slot=0), self.menu(dim=True, active_slot=0))
        self.assertNotEqual(self.menu(dim=True), self.menu())


class IconStateTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(tray.icon_state(RUNNING, False, False), tray.NORMAL)
        self.assertEqual(tray.icon_state(RUNNING, True, False), tray.ON)
        self.assertEqual(tray.icon_state(RUNNING, False, True), tray.ON)
        self.assertEqual(tray.icon_state({**RUNNING, "hw": "starting"}, True, True), tray.OFF)
        for status in (None, {**RUNNING, "processing": False}, {**RUNNING, "hw": "no-device"},
                       {**RUNNING, "hw": "no-nodes"}, {**RUNNING, "hw": "no-reply"}):
            self.assertEqual(tray.icon_state(status, True, True), tray.FAULT, status)

    def test_tooltip(self):
        self.assertEqual(tray.tooltip("RME Digiface USB", RUNNING, "Active", False, False),
                         "Openface Mixer — Digiface USB\n48 kHz · Hardware mixer: Active")
        self.assertEqual(tray.tooltip("RME Fireface 802", {**RUNNING, "rate": 44100}, "Active",
                                      True, True),
                         "Openface Mixer — Fireface 802\n44.1 kHz · Hardware mixer: Active\n"
                         "Main out muted, Dim on")
        self.assertEqual(tray.tooltip("RME Digiface USB", None, "", True, False),
                         "Openface Mixer — Digiface USB\nEngine stopped\nDim on")
        self.assertEqual(tray.tooltip("RME Digiface USB", {**RUNNING, "hw": "no-device"},
                                      "Not found", False, False),
                         "Openface Mixer — Digiface USB\nHardware mixer: Not found")
        self.assertIn("Engine not responding",
                      tray.tooltip("RME Digiface USB", {**RUNNING, "processing": False}, "Active",
                                   False, False))


if QApplication:
    from PySide6.QtWidgets import QSystemTrayIcon

    class StubTray(QSystemTrayIcon):
        """A tray icon that is never shown: offscreen there is no tray to show it in."""
        available = True
        shown = False

        @staticmethod
        def isSystemTrayAvailable():
            return StubTray.available

        def show(self):
            self.shown = True

        def hide(self):
            self.shown = False


class TrayWindowTest(WindowCase):
    @classmethod
    def setUpClass(cls):
        if QApplication:
            from openface_mixer import tray_icon
            patch = mock.patch.object(tray_icon, "QSystemTrayIcon", StubTray)
            patch.start()
            cls.addClassCleanup(patch.stop)
        super().setUpClass()

    def setUp(self):
        StubTray.available = True
        self.addCleanup(self.set_mode, "background")
        self.addCleanup(self.w.show)
        self.addCleanup(setattr, self.w, "background", False)

    def set_mode(self, mode):
        config.save_ui(tray=mode)
        self.w.tray.apply()
        self.settle()

    def actions(self, menu=None):
        return {a.text(): a for a in (menu or self.w.tray.menu).actions() if a.text()}

    def test_background_mode_by_default(self):
        w = self.w
        self.assertFalse(w.tray.visible())          # a window opened by hand: no icon
        self.assertFalse(w.hides_on_close())
        self.assertTrue(QApplication.quitOnLastWindowClosed())
        w.background = True                         # started at login
        w.tray.apply()
        self.assertTrue(w.tray.visible() and w.tray.icon.shown)
        self.assertFalse(QApplication.quitOnLastWindowClosed())

    def test_always_close_hides_and_click_toggles(self):
        w = self.w
        self.set_mode("always")
        self.assertTrue(w.tray.visible())
        self.assertEqual(json.loads(config.UI_FILE.read_text())["tray"], "always")
        w.close()
        self.settle()
        self.assertFalse(w.isVisible())             # hidden, the app runs on
        self.assertEqual(list(self.actions())[0], "Show mixer")
        w.tray.icon.activated.emit(QSystemTrayIcon.Trigger)     # left click
        self.settle()
        self.assertTrue(w.isVisible())
        w.tray.menu.aboutToShow.emit()
        self.assertEqual(list(self.actions())[0], "Hide mixer")
        w.tray.icon.activated.emit(QSystemTrayIcon.Trigger)
        self.settle()
        self.assertFalse(w.isVisible())
        w.tray.icon.activated.emit(QSystemTrayIcon.MiddleClick)  # does nothing
        self.settle()
        self.assertFalse(w.isVisible())

    def test_menu_acts_and_the_icon_follows(self):
        from openface_mixer import control_room
        from openface_mixer.model import extract_mix
        w = self.w
        self.set_mode("always")
        cr = control_room.control_room(w.st)
        main = cr["main"]
        w.engine_status = RUNNING
        w.refresh_tray()
        self.assertEqual(w.tray._shown[1], tray.NORMAL)
        self.actions()["Dim"].trigger()
        self.assertTrue(cr["dim"])
        self.assertTrue(self.actions()["Dim"].isChecked())
        self.assertEqual(w.tray._shown[1], tray.ON)
        self.assertIn("Dim on", w.tray.icon.toolTip())
        self.actions()["Dim"].trigger()
        self.assertFalse(cr["dim"])
        self.actions()["Mute main out"].trigger()
        self.assertTrue(w.st["out"][main]["mute"])
        self.actions()["Mute main out"].trigger()
        self.assertFalse(w.st["out"][main]["mute"])
        # presets: only filled slots, and choosing one recalls it
        self.assertFalse(self.actions()["Presets"].isEnabled())
        w.presets.store(2, "Late night", extract_mix(w.st))
        self.addCleanup(w.presets.clear, 2)
        w.refresh_slots()
        presets = self.actions()["Presets"].menu()
        self.assertEqual(list(self.actions(presets)), ["3  Late night"])
        with mock.patch.object(w, "replace_mix") as replace:
            self.actions(presets)["3  Late night"].trigger()
        replace.assert_called_once()
        # a stopped engine greys the icon and gives it a red dot
        w.engine_status = None
        w.refresh_tray()
        self.assertEqual(w.tray._shown[1], tray.FAULT)
        self.assertIn("Engine stopped", w.tray.icon.toolTip())

    def test_quit(self):
        w = self.w
        self.set_mode("always")
        with mock.patch.object(QApplication, "quit") as quit_:
            self.actions()["Quit Openface Mixer"].trigger()
        self.addCleanup(setattr, w, "_quitting", False)
        quit_.assert_called_once_with()
        self.assertFalse(w.isVisible())             # closed for real, not hidden

    def test_never_and_no_tray(self):
        w = self.w
        w.background = True
        self.set_mode("never")
        self.assertFalse(w.tray.visible())
        self.assertTrue(w.hides_on_close())         # started at login: hides, as before
        w.background = False
        self.set_mode("always")
        StubTray.available = False                  # the desktop has no tray
        self.set_mode("never")
        self.set_mode("always")
        self.addCleanup(w.tray._retry.stop)
        self.assertFalse(w.tray.visible())
        self.assertFalse(w.hides_on_close())        # closing quits, as without a tray
        self.assertTrue(QApplication.quitOnLastWindowClosed())
        self.assertTrue(w.tray._retry.isActive())   # it looks again for a tray that comes later
        StubTray.available = True
        w.tray._retry.timeout.emit()
        self.assertTrue(w.tray.visible())
        self.assertFalse(w.tray._retry.isActive())

    def test_a_damaged_ui_file(self):
        w = self.w
        w.background = True
        config.UI_FILE.parent.mkdir(parents=True, exist_ok=True)
        for text in ("[1, 2]", '{"tray": ["always"]}', "\x00not json"):
            config.UI_FILE.write_text(text)
            w.tray.apply()
            self.assertTrue(w.tray.visible(), text)     # the default: in background mode
        config.UI_FILE.write_text('{"tray": "never"}')
        w.tray.apply()
        self.assertFalse(w.tray.visible())

    def test_setting_in_the_sidebar(self):
        w = self.w
        box = w.remote_panel.tray_mode
        self.assertEqual([box.itemText(i) for i in range(box.count())],
                         ["In background mode", "Always", "Never"])
        self.assertEqual(box.currentData(), "background")
        box.setCurrentIndex(1)
        box.activated.emit(1)
        self.assertEqual(json.loads(config.UI_FILE.read_text())["tray"], "always")
        self.assertTrue(w.tray.visible())


if __name__ == "__main__":
    unittest.main()
