"""The Updates section in the real window, offscreen: the version line, the channel and the
startup check's switch, and the dot on the gear button. The updater's git work is replaced by
stand-ins (tests/test_updater.py covers it), so nothing here fetches or installs anything."""
import json
import time
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import config, updater
from tests.test_controls import QApplication, WindowCase

SOURCE = (Path("/nowhere/Openface-Mixer"), "abc1234" + "0" * 33)
VERSION = "Version 0.4.0 + 2 changes (abc1234)"


@unittest.skipUnless(QApplication, "needs PySide6")
class UpdatesWindowTest(WindowCase):
    @classmethod
    def setUpClass(cls):
        from openface_mixer import app, settings_panel
        cls.SettingsPanel = settings_panel.SettingsPanel
        # stand-ins that take what the real ones take
        cls.check, cls.update = (staticmethod(mock.create_autospec(f))
                                 for f in (updater.check, updater.update))
        cls.ask, cls.warning = mock.Mock(), mock.Mock()
        cls.restarts = []
        for patch in (mock.patch.object(updater, "install_source", lambda: SOURCE),
                      mock.patch.object(updater, "version_text", lambda source: VERSION),
                      mock.patch.object(updater, "check", cls.check),
                      mock.patch.object(updater, "update", cls.update),
                      mock.patch.object(settings_panel, "ask", cls.ask),
                      mock.patch.object(settings_panel.QMessageBox, "warning", cls.warning),
                      mock.patch.object(app.MainWindow, "restart_app",
                                        lambda self: cls.restarts.append(self)),
                      # the window under test starts its check when a test says so
                      mock.patch.object(cls.SettingsPanel, "STARTUP_CHECK_MS", 3600 * 1000)):
            patch.start()
            cls.addClassCleanup(patch.stop)
        super().setUpClass()

    def setUp(self):
        self.panel = self.w.settings
        self.idle()
        for stand_in in (self.check, self.update, self.ask, self.warning):
            stand_in.reset_mock()
            stand_in.side_effect = None
        self.check.return_value = (None, [])
        del self.restarts[:]
        self.panel._show_pending(None, [], quiet=True)
        self.panel.auto_check.setChecked(True)
        self.panel.channel.setCurrentIndex(0)
        config.save_ui(update_channel="releases")
        self.plain_gear = self.gear()

    def idle(self, panel=None):
        """Wait for the panel's check or update to come back from its thread."""
        self.wait(lambda: (panel or self.panel).update_btn.isEnabled())

    def wait(self, done):
        deadline = time.monotonic() + 10
        while not done():
            self.assertLess(time.monotonic(), deadline, "timed out")
            self.app.processEvents()
            time.sleep(0.002)

    def gear(self):
        return self.w.settings_btn.icon().pixmap(self.w.settings_btn.iconSize()).toImage()

    def saved(self):
        return json.loads(config.UI_FILE.read_text())

    def new_panel(self):
        panel = self.SettingsPanel(self.w.hw, self.w.dev, self.w.st)
        self.addCleanup(self.settle)
        self.addCleanup(panel.deleteLater)
        return panel

    def choose_channel(self, index):
        self.panel.channel.setCurrentIndex(index)
        self.panel.channel.activated.emit(index)       # as a click in the list does
        self.idle()

    def test_the_version_and_the_choices_are_shown(self):
        p = self.panel
        self.assertEqual(p.version_label.text(), VERSION)
        self.assertEqual(p.update_label.text(), p.UPDATE_PROMPT)
        self.assertEqual([p.channel.itemText(i) for i in range(p.channel.count())],
                         ["Releases", "Latest changes"])
        self.assertEqual(p.channel.currentData(), "releases")
        self.assertTrue(p.auto_check.isChecked())
        for control in (p.version_label, p.update_label, p.channel, p.auto_check, p.update_btn):
            self.assertFalse(control.isHidden())
        self.assertEqual(p.update_btn.text(), "Check for updates")

    def test_the_startup_check_says_nothing_when_there_is_nothing(self):
        self.panel._startup_check()
        self.assertEqual(self.panel.update_label.text(), "Checking…")
        self.idle()
        self.check.assert_called_once_with(*SOURCE, releases=True, quiet=True)
        self.assertEqual(self.panel.update_label.text(), self.panel.UPDATE_PROMPT)
        self.assertEqual(self.panel.update_btn.text(), "Check for updates")
        self.assertEqual(self.gear(), self.plain_gear)

    def test_the_startup_check_says_nothing_when_it_fails(self):
        for failure in (updater.UpdateError("fatal: unable to access 'https://github.com/'"),
                        OSError("no git")):
            self.check.side_effect = failure
            self.panel._startup_check()
            self.idle()
            self.assertEqual(self.panel.update_label.text(), self.panel.UPDATE_PROMPT)
            self.assertEqual(self.gear(), self.plain_gear)
            self.assertTrue(self.panel.channel.isEnabled())
        self.assertEqual(self.check.call_count, 2)
        self.warning.assert_not_called()
        self.assertTrue(self.w.toast.isHidden())

    def test_a_waiting_update_shows_on_the_gear_and_is_installed_on_request(self):
        self.check.return_value = ("0.4.1", ["Fix <b>pan", "Tray icon"])
        self.panel._startup_check()
        self.idle()
        text = self.panel.update_label.text()
        self.assertIn("Version 0.4.1 is available", text)
        self.assertIn("Fix &lt;b&gt;pan", text)
        self.assertEqual(self.panel.update_btn.text(), "Update and restart…")
        self.assertNotEqual(self.gear(), self.plain_gear)
        self.assertIn("update", self.w.settings_btn.toolTip())
        self.panel._startup_check()                 # nothing to look for while one is waiting
        self.check.assert_called_once()
        self.ask.return_value = False               # Cancel
        self.panel.update_btn.click()
        self.idle()
        self.assertIn("version 0.4.1", self.ask.call_args.args[2])
        self.update.assert_not_called()
        self.ask.return_value = True
        self.panel.update_btn.click()
        self.wait(lambda: self.restarts)
        self.update.assert_called_once_with(SOURCE[0], releases=True)

    def test_the_check_button_says_what_it_found(self):
        self.panel.update_btn.click()
        self.idle()
        self.check.assert_called_once_with(*SOURCE, releases=True, quiet=False)
        self.assertEqual(self.panel.update_label.text(), "Openface Mixer is up to date.")
        self.assertEqual(self.gear(), self.plain_gear)
        self.check.return_value = (None, [f"change {n}" for n in range(7)])   # no release yet
        self.panel.update_btn.click()
        self.idle()
        self.assertIn("7 update(s) on GitHub", self.panel.update_label.text())
        self.assertIn("and 2 more", self.panel.update_label.text())
        self.assertNotEqual(self.gear(), self.plain_gear)

    def test_a_failed_check_or_update_is_shown_when_it_was_asked_for(self):
        self.check.side_effect = updater.UpdateError("fatal: no network\nmore")
        self.panel.update_btn.click()
        self.idle()
        self.assertEqual(self.panel.update_label.text(), "fatal: no network")
        self.warning.assert_not_called()
        self.check.side_effect = None
        self.check.return_value = (None, ["one"])
        self.panel.update_btn.click()
        self.idle()
        self.update.side_effect = updater.UpdateError("/src has local changes; update it by hand.")
        self.panel.update_btn.click()
        self.idle()
        self.warning.assert_called_once()
        self.assertEqual(self.restarts, [])
        self.assertEqual(self.panel.update_btn.text(), "Update and restart…")    # still waiting

    def test_the_startup_check_can_be_switched_off(self):
        self.panel.auto_check.click()
        self.assertIs(self.saved()["update_check"], False)
        self.panel._startup_check()
        self.settle()
        self.check.assert_not_called()
        self.assertEqual(self.panel.update_label.text(), self.panel.UPDATE_PROMPT)
        self.assertFalse(self.new_panel().auto_check.isChecked())      # kept for the next start
        self.panel.update_btn.click()               # the button still checks
        self.idle()
        self.check.assert_called_once()
        self.panel.auto_check.click()
        self.assertIs(self.saved()["update_check"], True)

    def test_choosing_a_channel_is_kept_and_checks_it(self):
        self.check.return_value = ("0.4.1", ["Fix pan"])
        self.panel.update_btn.click()
        self.idle()
        self.assertNotEqual(self.gear(), self.plain_gear)
        self.check.reset_mock()
        self.check.return_value = (None, [])
        self.choose_channel(1)
        self.assertEqual(self.saved()["update_channel"], "latest")
        self.check.assert_called_once_with(*SOURCE, releases=False, quiet=False)
        self.assertEqual(self.panel.update_label.text(), "Openface Mixer is up to date.")
        self.assertEqual(self.panel.update_btn.text(), "Check for updates")
        self.assertEqual(self.gear(), self.plain_gear)       # the release is no longer offered
        self.assertEqual(self.new_panel().channel.currentData(), "latest")
        self.check.return_value = (None, ["Tray icon"])
        self.panel.update_btn.click()
        self.idle()
        self.ask.return_value = True
        self.panel.update_btn.click()
        self.wait(lambda: self.restarts)
        self.update.assert_called_once_with(SOURCE[0], releases=False)
        self.choose_channel(0)
        self.assertEqual(self.saved()["update_channel"], "releases")

    def test_the_wheel_scrolls_past_the_channel_and_leaves_it(self):
        from PySide6.QtWidgets import QScrollArea
        area = self.w.sidebar.findChild(QScrollArea)
        area.ensureWidgetVisible(self.panel.channel)
        self.settle()
        at = area.verticalScrollBar().value()
        self.assertGreater(at, 0)
        self.panel.channel.setCurrentIndex(1)
        self.wheel(self.panel.channel, 0, 120)      # up: a plain box would go to Releases
        self.settle()
        self.assertEqual(self.panel.channel.currentIndex(), 1)
        self.assertLess(area.verticalScrollBar().value(), at)
        self.check.assert_not_called()
        self.assertEqual(self.saved()["update_channel"], "releases")

    def test_a_new_window_checks_by_itself_after_a_moment(self):
        self.check.return_value = (None, ["Tray icon"])
        with mock.patch.object(self.SettingsPanel, "STARTUP_CHECK_MS", 1):
            panel = self.new_panel()
        waiting = []
        panel.update_waiting.connect(waiting.append)
        self.wait(lambda: waiting)
        self.check.assert_called_once_with(*SOURCE, releases=True, quiet=True)
        self.assertEqual(waiting, [True])
        self.assertEqual(self.gear(), self.plain_gear)       # not this window's panel

    def test_a_copy_that_cannot_update_itself_has_no_controls(self):
        from PySide6.QtCore import QTimer
        with mock.patch.object(updater, "install_source", lambda: None), \
                mock.patch.object(self.SettingsPanel, "STARTUP_CHECK_MS", 1):
            panel = self.new_panel()
        self.assertEqual(panel.version_label.text(), VERSION)
        self.assertEqual(panel.update_label.text(), updater.manual_hint())
        for control in (panel.channel, panel.auto_check, panel.update_btn):
            self.assertTrue(control.isHidden())
        self.assertEqual(panel.findChildren(QTimer), [])
        self.check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
