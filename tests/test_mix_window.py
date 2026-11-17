"""The window's part in protecting the mix: damaged files, failed imports and exports, and the
mix kept before a recall. Opens the real main window offscreen with a stand-in engine and a
throwaway config folder; skipped where PySide6 is not installed."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import arc, config, engine, model as m
from openface_mixer.devices import DEVICES

try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QMenu
except ImportError:
    QApplication = None

RESTORE = "Restore previous mix"


@unittest.skipUnless(QApplication, "PySide6 is not installed")
class MixWindowTest(unittest.TestCase):
    dev = DEVICES["digiface"]

    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from openface_mixer import app, remote_link
        cls.app_module, cls.remote_link = app, remote_link
        cls.qt = QApplication.instance() or QApplication(["test"])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        app = self.app_module
        self.warning, self.ask = mock.Mock(), mock.Mock(return_value=True)
        self.open_name, self.save_name = mock.Mock(), mock.Mock()
        patches = [
            mock.patch.object(config, "CONFIG_DIR", self.dir / "config"),
            mock.patch.object(config, "UI_FILE", self.dir / "config" / "ui.json"),
            mock.patch.object(self.remote_link, "AUTOSTART_DIR", self.dir / "autostart"),
            mock.patch.object(app.ArcLink, "try_open", lambda self: None),   # no ARC USB
            mock.patch.multiple(
                engine.Engine, status=lambda self: None, outdated=lambda self: False,
                attach=lambda self: False, start=lambda self: (True, ""),
                stop=lambda self: None),
            mock.patch.multiple(
                app.MainWindow, poll_hw=lambda self: None, poll_profile=lambda self: None,
                ensure_engine=lambda self: None),
            mock.patch.object(app.QMessageBox, "warning", self.warning),
            mock.patch.object(app, "ask", self.ask),
            mock.patch.object(app.QFileDialog, "getOpenFileName", self.open_name),
            mock.patch.object(app.QFileDialog, "getSaveFileName", self.save_name),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.open_windows = []
        self.addCleanup(lambda: [self.close(w) for w in list(self.open_windows)])

    def window(self):
        w = self.app_module.MainWindow(self.dev)
        self.open_windows.append(w)
        return w

    def close(self, w):
        self.open_windows.remove(w)
        w.close()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def write_state(self, content, backup=None):
        path = config.state_file(self.dev)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if backup is not None:
            config.backup_file(path).write_text(backup)

    def mix_file(self, mix, name="Live"):
        path = self.dir / "mix.json"
        path.write_text(json.dumps({"format": "openface-mixer-mix", "version": 1, "name": name,
                                    "mix": mix}))
        self.open_name.return_value = (str(path), "")
        return path

    def previous(self):
        return json.loads(config.previous_mix_file(self.dev).read_text())["mix"]

    # ---------------------------------------------------------------- damaged files at start
    def test_opens_on_damaged_files(self):
        for name, content in (("state.json", "[1, 2]"), ("state.json", ""),
                              ("state.json", '{"sends": "x", "selected": 99}'),
                              ("presets.json", "[1]"), ("presets.json", '{"slots": 5}')):
            with self.subTest(name=name, content=content):
                path = config.device_dir(self.dev) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                w = self.window()
                w.show()
                self.qt.processEvents()
                w.tabs.setCurrentIndex(1)
                w.push_matrix()
                w.save()
                self.close(w)
                path.unlink()

    def test_opens_on_damaged_remote_settings(self):
        remote = {"port": 2 ** 40, "host": "a..b", "out_port": -1}      # no port, no host name
        self.write_state(json.dumps(dict(m.default_state(), remote=remote)))
        w = self.window()
        self.assertEqual((w.st["remote"]["port"], w.st["remote"]["out_port"]), (7001, 9001))
        self.assertIsNone(w.remote.target)

    def test_says_once_that_the_backup_was_loaded(self):
        good = m.default_state(16)
        good["sends"]["in"][0][0] = [-6.0, 0.0]
        self.write_state("{broken", backup=json.dumps(good))
        w = self.window()
        self.assertEqual(w.st["sends"]["in"][0][0], [-6.0, 0.0])
        self.assertFalse(w.toast.isVisible())
        w.show()
        self.qt.processEvents()
        self.assertTrue(w.toast.isVisible())
        self.assertIn("last good copy", w.toast.text())
        self.assertFalse(w._toast_timer.isActive())     # it stays until clicked
        w.toast.mousePressEvent(None)
        self.assertFalse(w.toast.isVisible())
        w.hide()
        w.show()
        self.qt.processEvents()
        self.assertFalse(w.toast.isVisible())
        self.warning.assert_not_called()
        w.save()
        self.close(w)
        again = self.window()                   # the next start has nothing to report
        again.show()
        self.qt.processEvents()
        self.assertFalse(again.toast.isVisible())
        self.assertEqual(again.st["sends"]["in"][0][0], [-6.0, 0.0])

    def test_says_that_a_damaged_mix_was_reset(self):
        self.write_state("{broken")
        w = self.window()
        w.show()
        self.qt.processEvents()
        self.assertIn("reset to the default", w.toast.text())

    def test_fresh_start_says_nothing(self):
        w = self.window()
        w.show()
        self.qt.processEvents()
        self.assertFalse(w.toast.isVisible())

    # ---------------------------------------------------------------- import and export
    def test_damaged_import_shows_the_dialog_and_changes_nothing(self):
        w = self.window()
        w.st["sends"]["in"][0][0] = [-6.0, 0.0]
        before = json.dumps(w.st, sort_keys=True)
        mix = m.extract_mix(m.default_state())
        files = ["[1, 2]", '"x"', "", "{broken", json.dumps({"hello": 1})]
        files += [json.dumps({"format": "openface-mixer-mix", "mix": bad}) for bad in (
            [1], dict(mix, sends="x"), dict(mix, out=[5]), dict(mix, mute={"in": []}),
            dict(mix, groups={"bogus": 1}))]
        for content in files:
            with self.subTest(content=content[:40]):
                self.warning.reset_mock()
                self.mix_file({}).write_text(content)
                w.import_mix()
                self.warning.assert_called_once()
                self.assertIn("Could not import", self.warning.call_args.args[2])
                self.assertEqual(json.dumps(w.st, sort_keys=True), before)
                self.assertFalse(config.previous_mix_file(self.dev).exists())
        self.open_name.return_value = (str(self.dir / "missing.json"), "")
        self.warning.reset_mock()
        w.import_mix()
        self.assertIn("No such file", self.warning.call_args.args[2])

    def test_import_clears_sends_on_channels_the_interface_lacks(self):
        """A mix from a bigger interface: the Digiface has 32 inputs and 17 output pairs."""
        w = self.window()
        big = m.default_state()
        big["sends"]["in"][33][0] = [0.0, 0.0]      # input 34 to AD1 1/2
        big["sends"]["in"][0][17] = [0.0, 0.0]      # input 1 to output pair 18
        big["sends"]["in"][1][0] = [-3.0, 0.0]
        self.mix_file(m.extract_mix(big))
        w.import_mix()
        self.warning.assert_not_called()
        self.assertEqual(w.st["sends"]["in"][33][0], [None, 0.0])
        self.assertEqual(w.st["sends"]["in"][0][17], [None, 0.0])
        self.assertEqual(w.st["sends"]["in"][1][0], [-3.0, 0.0])
        self.assertEqual(w.st["sends"]["play"][35][17], [None, 0.0])

    def test_export_failure_shows_the_dialog(self):
        w = self.window()
        (self.dir / "file").write_text("")
        self.save_name.return_value = (str(self.dir / "file" / "mix.json"), "")
        w.export_mix()
        self.warning.assert_called_once()
        self.assertIn("Could not export", self.warning.call_args.args[2])
        self.warning.reset_mock()
        self.save_name.return_value = (str(self.dir / "mix.json"), "")
        w.export_mix()
        self.warning.assert_not_called()
        self.assertEqual(json.loads((self.dir / "mix.json").read_text())["mix"],
                         m.extract_mix(w.st))

    # ---------------------------------------------------------------- the previous mix
    def restore_action(self, w):
        w._fill_presets_menu()
        return next(a for a in w.presets_menu.actions() if a.text() == RESTORE)

    def test_recall_import_and_reset_keep_the_mix_they_replace(self):
        w = self.window()
        self.assertFalse(self.restore_action(w).isEnabled())
        w.presets.store(0, "A", m.extract_mix(w.st))
        steps = (lambda: w.recall_slot(0), w.import_mix, w.reset_mix)
        self.mix_file(m.extract_mix(w.st))
        for n, step in enumerate(steps):
            with self.subTest(step=n):
                w.st["out"][0]["gain"] = -10.0 - n
                outgoing = m.extract_mix(w.st)
                step()
                self.assertEqual(w.st["out"][0]["gain"], 0.0)
                self.assertEqual(self.previous(), outgoing)
        self.assertEqual(sorted(p.name for p in config.device_dir(self.dev).iterdir()
                                if "previous" in p.name), ["previous-mix.json"])

    def test_restore_brings_the_previous_mix_back(self):
        w = self.window()
        w.presets.store(0, "A", m.extract_mix(w.st))
        w.st["out"][0]["gain"] = -20.0
        w.st["sends"]["in"][2][3] = [-4.0, 0.5]
        w.st["active_slot"] = 0
        mine = m.extract_mix(w.st)
        w.recall_slot(0)
        recalled = m.extract_mix(w.st)
        action = self.restore_action(w)
        self.assertTrue(action.isEnabled())
        action.trigger()
        self.assertEqual(m.extract_mix(w.st), mine)
        self.assertIsNone(w.st["active_slot"])
        self.assertAlmostEqual(w.strips["out:0"].fader.db(), -20.0)
        self.assertEqual(self.previous(), recalled)     # one file: restoring again goes back
        self.warning.assert_not_called()

    def test_restore_with_a_damaged_file_shows_the_dialog(self):
        w = self.window()
        path = config.previous_mix_file(self.dev)
        path.parent.mkdir(parents=True)
        path.write_text("{broken")
        before = m.extract_mix(w.st)
        w.restore_previous_mix()
        self.warning.assert_called_once()
        self.assertEqual(m.extract_mix(w.st), before)
        self.assertEqual(path.read_text(), "{broken")

    def test_slot_menu_has_the_restore_entry(self):
        w = self.window()
        menu = QMenu()
        w._add_restore_action(menu)
        self.assertEqual([(a.text(), a.isEnabled()) for a in menu.actions() if a.text()],
                         [(RESTORE, False)])
        w.reset_mix()
        menu = QMenu()
        w._add_restore_action(menu)
        self.assertTrue(menu.actions()[-1].isEnabled())

    def test_reset_and_clear_ask_first(self):
        w = self.window()
        w.presets.store(2, "Keep", m.extract_mix(w.st))
        w.st["out"][0]["gain"] = -10.0
        self.ask.return_value = False
        w.clear_slot(2)
        w.reset_mix()
        self.assertEqual(w.presets.name(2), "Keep")
        self.assertEqual(w.st["out"][0]["gain"], -10.0)
        self.assertFalse(config.previous_mix_file(self.dev).exists())
        self.assertIn("Keep", self.ask.call_args_list[0].args[2])
        self.ask.return_value = True
        w.clear_slot(2)
        self.assertIsNone(w.presets.slots[2])
        self.assertIsNone(self.app_module.PresetBank(config.presets_file(self.dev)).slots[2])

    def test_clearing_the_recalled_slot_puts_its_arc_key_out(self):
        w = self.window()
        key = arc.DEFAULT_KEYS.index("snapshot:3")
        w.presets.store(2, "Keep", m.extract_mix(w.st))
        with mock.patch.object(w.arc, "set_leds") as leds:
            w.recall_slot(2)
            self.assertTrue(leds.call_args.args[0][key])
            w.clear_slot(2)
            self.assertFalse(leds.call_args.args[0][key])
            self.assertIsNone(w.st["active_slot"])
            w.st["active_slot"] = 2             # a state saved before Clear did that
            w.update_arc_leds()
            self.assertFalse(leds.call_args.args[0][key])


if __name__ == "__main__":
    unittest.main()
