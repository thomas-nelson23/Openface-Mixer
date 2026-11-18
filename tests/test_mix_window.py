"""The window's part in protecting the mix: damaged files, failed imports and exports, the mix
kept before a recall, what a stereo link keeps, and solo on strips that go out of sight. Opens
the real main window offscreen with a stand-in engine and a throwaway config folder; skipped
where PySide6 is not installed."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import arc, config, engine, model as m, presets
from openface_mixer.model import NEG_INF
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
        path.write_text(json.dumps({"format": "openface-mixer-mix", "version": presets.VERSION,
                                    "name": name, "mix": mix}))
        self.open_name.return_value = (str(path), "")
        return path

    def previous(self):
        return json.loads(config.previous_mix_file(self.dev).read_text())["mix"]

    # ---------------------------------------------------------------- damaged files at start
    def test_opens_on_damaged_files(self):
        kept = dict(m.default_state(), unlinked={"in:0": 5, "play:0": {"group": 0}}, control_room={
            "phones": "x", "recall_db": "loud", "talkback_dests": 7, "dim_db": [1],
            "talkback_dim_db": None, "encoder": "phones9"})
        for name, content in (("state.json", "[1, 2]"), ("state.json", ""),
                              ("state.json", '{"sends": "x", "selected": 99}'),
                              ("state.json", json.dumps(kept)),
                              ("state.json", json.dumps(dict(kept, unlinked="x", version="x"))),
                              ("state.json", json.dumps(dict(
                                  kept, mirror={"out:1": 1, "out:2": "x"}, trim="yes",
                                  mute_groups=[{"on": 1}, "x", {"on": True, "sends": [[1, 2]]}],
                                  solo_groups={"out:0": 1, "in:0": 9}))),
                              ("state.json", json.dumps(dict(kept, mirror=[3], mute_groups={},
                                                             solo_groups=None, trim=None))),
                              ("presets.json", "[1]"), ("presets.json", '{"slots": 5}')):
            with self.subTest(name=name, content=content):
                path = config.device_dir(self.dev) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                w = self.window()
                w.show()
                self.qt.processEvents()
                w.tabs.setCurrentIndex(1)
                for pair in ("play", 0), ("in", 0):         # unlink and link
                    w.toggle_stereo(*pair, not w.st["stereo"][pair[0]][0])
                self.qt.processEvents()
                w.control.recall.emit()
                w.arc_encoder(1)
                for g in (1, 2):
                    w.toggle_group("mute", g)
                    w.toggle_group("solo", g)
                w.move_fader(w.strips["in:0"], -3.0)
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

    # ---------------------------------------------------------------- stereo link
    def two_mono_channels(self, w):
        """Inputs 1 and 2 with levels, pans, a mute, a group, a name, a colour and an icon of
        their own. Returns the state to get back to."""
        st = w.st
        st["sends"]["in"][0][16], st["sends"]["in"][1][16] = [-6.0, -0.5], [-12.0, 0.25]
        st["sends"]["in"][0][0], st["sends"]["in"][1][0] = [None, 0.3], [0.0, 1.0]
        st["mute"]["in"][1] = True
        st["groups"].update({"in:0": 1, "in:1": 2})
        st["names"].update({"in:0": "Kick", "in:1": "Snare"})
        st["colors"]["in:1"] = "#ff0000"
        st["icons"]["in:1"] = "drum"
        w.rebuild_all()
        return copy.deepcopy(st)

    def link(self, w, on):
        strip = w.strips["in:0"]
        self.assertNotEqual(strip.stereo.isChecked(), on)
        strip.stereo.click()
        self.qt.processEvents()                 # the row is rebuilt after the click
        self.assertEqual(w.strips["in:0"].channels, [0, 1] if on else [0])
        self.assertEqual("in:1" in w.strips, not on)

    def test_st_button_brings_both_channels_back_as_they_were(self):
        w = self.window()
        before = self.two_mono_channels(w)
        self.link(w, True)
        self.assertEqual(w.st["sends"]["in"][1][16], [-6.0, 0.0])
        self.assertEqual(w.strips["in:0"].name, "Kick")
        self.assertFalse(w.strips["in:0"].mute.isChecked())
        self.link(w, False)
        self.assertEqual(w.st, before)
        right = w.strips["in:1"]
        self.assertEqual((right.name, right.group, right.mute.isChecked()), ("Snare", 2, True))
        self.assertAlmostEqual(right.fader.db(), -12.0)
        self.assertEqual(right.pan.value, 0.25)

    def test_a_level_set_while_linked_stays_on_both_channels(self):
        w = self.window()
        before = self.two_mono_channels(w)
        self.link(w, True)
        w.move_fader(w.strips["in:0"], -3.0)        # in the phones submix
        self.link(w, False)
        self.assertEqual((w.st["sends"]["in"][0][16], w.st["sends"]["in"][1][16]),
                         ([-3.0, -1.0], [-3.0, 1.0]))
        self.assertEqual((w.st["sends"]["in"][0][0], w.st["sends"]["in"][1][0]),
                         (before["sends"]["in"][0][0], before["sends"]["in"][1][0]))
        self.assertEqual(w.st["groups"], before["groups"])

    def test_link_survives_the_remote_presets_files_and_a_restart(self):
        w = self.window()
        before = m.extract_mix(self.two_mono_channels(w))

        def remote_link(w, on):                                 # the OSC remote
            w.remote_message(f"/input/{int(on)}/stereo", [float(on)])
            self.qt.processEvents()             # the row is rebuilt after the message
            self.assertEqual("in:1" in w.strips, not on)

        def unlinks_to_before(w):
            self.assertTrue(w.st["stereo"]["in"][0])
            remote_link(w, False)
            self.assertEqual(m.extract_mix(w.st), before)
            self.assertEqual(w.strips["in:1"].name, "Snare")
            remote_link(w, True)

        remote_link(w, True)
        unlinks_to_before(w)
        linked = m.extract_mix(w.st)
        w.presets.store(0, "Linked", linked)
        self.save_name.return_value = (str(self.dir / "linked.json"), "")
        w.export_mix()
        self.open_name.return_value = self.save_name.return_value
        for step in (lambda: w.recall_slot(0), w.import_mix, w.restore_previous_mix):
            w.reset_mix()                                       # nothing linked, nothing kept
            self.assertEqual(w.st["unlinked"], {})
            step()
            unlinks_to_before(w)
        w.save()
        self.close(w)
        unlinks_to_before(self.window())
        self.warning.assert_not_called()

    # ---------------------------------------------------------------- solo out of sight
    def solo(self, w, key):
        w.strips[key].solo.click()
        self.assertTrue(m.any_solo(w.st))

    def test_hiding_a_strip_clears_its_solo(self):
        w = self.window()
        pushed = []
        w.engine.write_matrix = lambda gains, out: pushed.append(list(gains))
        self.solo(w, "in:2")
        self.solo(w, "play:0")                      # a linked pair: both channels
        self.assertEqual(w.st["solo"]["play"][:2], [True, True])
        w.hide_strip(w.strips["play:0"])
        self.assertEqual(w.st["solo"]["play"][:2], [False, False])
        self.assertTrue(w.st["solo"]["in"][2])      # the one still in view stays
        w.hide_strip(w.strips["in:2"])
        self.assertFalse(m.any_solo(w.st))
        self.assertEqual(pushed[-1], list(m.compute_matrix(w.st)))      # and it is heard
        w.unhide_all()
        self.assertFalse(w.strips["in:2"].solo.isChecked())

    def test_hiding_a_group_clears_its_solos(self):
        w = self.window()
        self.solo(w, "in:8")                        # AD2 1
        self.solo(w, "in:0")
        w.settings._show_toggled("AD2", False)
        self.assertEqual([c for c, on in enumerate(w.st["solo"]["in"]) if on], [0])
        w.settings._show_toggled("AD1", False)
        self.assertFalse(m.any_solo(w.st))

    def test_nothing_out_of_sight_is_soloed_after_a_recall_a_rate_change_or_a_remote(self):
        w = self.window()
        w.presets.store(0, "A", m.extract_mix(w.st))
        w.st["hidden_strips"] = ["in:3"]
        w.st["solo"]["in"][3] = w.st["solo"]["in"][20] = True
        w.recall_slot(0)                            # the strips are rebuilt
        self.assertEqual([c for c, on in enumerate(w.st["solo"]["in"]) if on], [20])
        w.engine.status = lambda: {      # found, but not yet active: nothing to enable
            "processing": True, "rate": 96000, "sink_linked": True, "hw": "no-nodes",
            "hw_nodes": 0, "hw_levels": True, "dev_status": 0, "dev_status2": 0}
        w.poll_engine()                             # AD3 5 is not there at 96 kHz
        self.assertFalse(m.any_solo(w.st))
        w.remote_message("/mix/in/3/0/solo", [1.0])
        self.assertFalse(m.any_solo(w.st))
        w.remote_message("/mix/in/2/0/solo", [1.0])
        self.assertTrue(w.strips["in:2"].solo.isChecked())

    # ---------------------------------------------------------------- submix tools
    def menu_entries(self, w, strip):
        with mock.patch.object(self.app_module, "QMenu") as menu:
            w.strip_menu(strip, None)
        menu = menu.return_value
        return ({c.args[-2] if callable(c.args[-1]) else c.args[0]: c
                 for c in menu.addAction.call_args_list},
                [c.args[0] for c in menu.addMenu.call_args_list])

    def test_the_menus_offer_the_tools(self):
        w = self.window()
        out, src = w.strips["out:0"], w.strips["in:0"]
        actions, menus = self.menu_entries(w, out)
        self.assertIn("Copy mix", actions)
        self.assertNotIn("Copy sends", actions)
        self.assertNotIn("Solo group", menus)
        w.copy_mix(out)
        actions, _ = self.menu_entries(w, w.strips["out:1"])
        self.assertIn(f"Paste mix from {out.name}", actions)
        self.assertIn(f"Mirror mix of {out.name}", actions)
        actions, menus = self.menu_entries(w, src)
        self.assertEqual([k for k in actions if "sends" in k],
                         ["Copy sends", "Paste sends", "Clear sends…"])
        self.assertIn("Solo group", menus)
        self.assertIn("Mute group in Phones", menus)

    def test_paste_and_mirror_keep_the_mix_they_replace(self):
        w = self.window()
        w.st["sends"]["in"][2][0] = [-4.0, 0.5]
        a, b = w.strips["out:0"], w.strips["out:1"]
        w.copy_mix(a)
        outgoing = m.extract_mix(w.st)
        w.paste_mix(b)
        self.assertEqual(self.previous(), outgoing)
        self.assertEqual(w.st["sends"]["in"][2][1], [-4.0, 0.5])
        w.restore_previous_mix()
        self.assertEqual(m.extract_mix(w.st), outgoing)
        w.mirror_mix(w.strips["out:3"])
        self.assertEqual(w.st["mirror"], {"out:3": 0})
        self.assertEqual(self.previous(), outgoing)
        w.select_out(3, by_pair=True)
        self.assertIn("mirrors", w.row_in._subtitle)
        w.move_fader(w.strips["in:2"], -9.0)              # edits the mirrored submix
        self.assertEqual(w.st["sends"]["in"][2][0][0], -9.0)
        actions, _ = self.menu_entries(w, w.strips["out:3"])
        actions[f"Remove mirror of {a.name}"].args[-1]()
        self.assertEqual(w.st["mirror"], {})
        self.assertEqual(w.st["sends"]["in"][2][3][0], -9.0)
        self.warning.assert_not_called()

    def test_clear_sends_asks_first(self):
        w = self.window()
        strip = w.strips["play:0"]
        self.ask.return_value = False
        w.clear_sends(strip)
        self.assertEqual(w.st["sends"]["play"][0][0], [0.0, 0.0])
        self.assertFalse(config.previous_mix_file(self.dev).exists())
        self.assertIn(strip.name, self.ask.call_args.args[2])
        self.ask.return_value = True
        outgoing = m.extract_mix(w.st)
        w.clear_sends(strip)
        self.assertTrue(all(s[0] is None for c in (0, 1) for s in w.st["sends"]["play"][c]))
        self.assertEqual(self.previous(), outgoing)
        w.copy_sends(w.strips["play:2"])
        w.change_mix(lambda st: m.paste_sends(st, "play", 0, w.sends_clipboard[1]), "x")
        self.assertEqual(w.st["sends"]["play"][0], w.st["sends"]["play"][2])

    def test_cue_plays_an_outputs_submix_on_main_out(self):
        pushed = []
        with mock.patch.object(engine.Engine, "write_matrix",
                               lambda _self, g, _o: pushed.append(list(g))):
            w = self.window()
            w.st["sends"]["in"][4][2] = [0.0, -1.0]
            cues = {s.pair: s.cue for s in w.row_out.strips}
            self.assertFalse(cues[0].isEnabled())       # Main Out
            saved = copy.deepcopy(w.st)
            cues[2].click()
            self.assertEqual(w.cue, 2)
            self.assertEqual(pushed[-1][4], 1.0)        # input 5 on Main Out left
            cues[3].click()                             # one at a time
            self.assertEqual((w.cue, cues[2].isChecked()), (3, False))
            self.assertEqual(pushed[-1][4], 0.0)
            cues[3].click()
            self.assertIsNone(w.cue)
            cues[2].click()
            self.assertEqual(w.st, saved)               # never part of the state
            w.save()
            self.assertNotIn("cue", json.loads(config.state_file(self.dev).read_text()))
            w.hide_strip(w.strips["out:2"])             # out of sight: Cue ends
            self.assertIsNone(w.cue)

    def test_trim_moves_every_submix(self):
        w = self.window()
        w.st["sends"]["in"][0][0] = [-10.0, 0.0]
        w.st["sends"]["in"][0][16] = [-20.0, 0.0]
        w.select_out(16, by_pair=True)
        w.trim_btn.click()
        self.assertTrue(w.st["trim"])
        self.assertIn("Trim", w.row_in._subtitle)
        w.move_fader(w.strips["in:0"], -14.0)
        self.assertEqual(w.st["sends"]["in"][0][0][0], -4.0)
        w.move_fader(w.strips["in:0"], NEG_INF)         # held at the bottom, as shown
        self.assertEqual(w.st["sends"]["in"][0][16][0], m.FADER_MIN_DB)
        self.assertAlmostEqual(w.strips["in:0"].fader.db(), m.FADER_MIN_DB)
        w.trim_btn.click()
        w.move_fader(w.strips["in:0"], -20.0)
        self.assertEqual(w.st["sends"]["in"][0][0][0], -70.0)
        w.save()
        self.close(w)
        self.assertFalse(self.window().st["trim"])

    def test_group_buttons(self):
        w = self.window()
        mute1, solo2 = w.group_btns["mute", 1], w.group_btns["solo", 2]
        self.assertFalse(mute1.isEnabled())
        w.choose_mute_group(w.strips["in:0"], 1)
        w.choose_solo_group(w.strips["in:2"], 2)
        w.choose_solo_group(w.strips["play:0"], 2)
        self.assertTrue(mute1.isEnabled() and solo2.isEnabled())
        self.assertIn(w.strips["in:0"].name, mute1.toolTip())
        mute1.click()
        self.assertTrue(w.st["mute_groups"][0]["on"] and mute1.isChecked())
        self.assertEqual(w.strips["in:0"]._group_muted, 1)
        w.select_out(0, by_pair=True)                   # muted in Phones only
        self.assertEqual(w.strips["in:0"]._group_muted, 0)
        solo2.click()
        self.assertEqual((w.st["solo"]["in"][2], w.st["solo"]["play"][:2]), (True, [True, True]))
        self.assertTrue(w.strips["in:2"].solo.isChecked() and solo2.isChecked())
        solo2.click()
        self.assertFalse(m.any_solo(w.st))
        w.choose_mute_group(w.strips["in:0"], 0)        # back in Phones? no: in AD1 1/2
        w.select_out(16, by_pair=True)
        w.choose_mute_group(w.strips["in:0"], 0)
        self.assertFalse(mute1.isEnabled() or w.st["mute_groups"][0]["on"])
        self.assertEqual(w.strips["in:0"]._group_muted, 0)


if __name__ == "__main__":
    unittest.main()
