"""Workspaces: everything for one interface in one file (presets.export_workspace and
import_workspace), and the Mix section's Save workspace and Load workspace. The window tests
open the real main window offscreen with a stand-in engine (test_mix_window.MixWindowTest);
skipped where PySide6 is not installed."""
import copy
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import arc, config, model as m, presets
from openface_mixer.devices import DEVICES
from openface_mixer.presets import (VERSION, WORKSPACE_FORMAT, export_workspace,
                                    import_workspace)
from tests import test_mix_window
from tests.test_pan_law import old_state, saved

UI = {"meter_rms": False, "peak_hold": 3.0, "over_samples": 4,
      "arc_keys": ["dim"] + arc.DEFAULT_KEYS[1:]}


def busy(dev):
    """A state for dev as loading leaves it, with something in every part a workspace keeps."""
    st, _problem = config.load_state(dev)
    st["sends"]["in"][0][0] = [-6.0, 0.25]
    st["mute"]["play"][1] = True
    st["names"]["in:0"] = "Vox"
    m.set_strip_color(st, "in:0", "#336699")
    m.set_strip_icon(st, "in:0", "mic")
    st["hidden_strips"] = ["in:1"]
    st["layouts"][2] = m.extract_layout(st, "Small")
    st["control_room"]["dim_db"] = -30.0
    if dev.fx is not None:
        st["hw"]["fx"]["reverb"]["predelay"] = 50
    return st


class WorkspaceFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for patch in (mock.patch.object(config, "CONFIG_DIR", self.dir / "config"),
                      mock.patch.object(config, "UI_FILE", self.dir / "config" / "ui.json")):
            patch.start()
            self.addCleanup(patch.stop)
        self.path = self.dir / "w.json"
        self.dev = DEVICES["digiface"]

    def slots(self, st):
        return [{"name": "Live", "mix": m.extract_mix(st)}] + [None] * (m.N_SLOTS - 1)

    def write(self, **changes):
        """A good workspace for self.dev, with changes made to its top level."""
        st = busy(self.dev)
        export_workspace(self.path, self.dev, st, self.slots(st), UI)
        data = json.loads(self.path.read_text())
        data.update(changes)
        self.path.write_text(json.dumps(data))
        return data

    def refused(self, sentence):
        with self.assertRaises(ValueError) as cm:
            import_workspace(self.path, self.dev)
        self.assertEqual(str(cm.exception), sentence)

    def test_everything_comes_back_on_every_device(self):
        for key, dev in DEVICES.items():
            with self.subTest(key):
                st = busy(dev)
                st["solo"]["in"][0] = True              # a solo is never kept
                st["remote"] = {"enabled": True, "port": 7001}  # nor this computer's network
                export_workspace(self.path, dev, st, self.slots(st), UI)
                self.assertNotIn("remote", json.loads(self.path.read_text())["state"])
                loaded, slots, ui = import_workspace(self.path, dev)
                expected = dict(st, solo=m.default_state()["solo"])
                del expected["remote"]
                self.assertEqual(loaded, expected)
                self.assertEqual(slots, self.slots(st))
                self.assertEqual(ui, UI)
                self.assertEqual(loaded["layouts"][2]["hidden_strips"], ["in:1"])
                if dev.fx is not None:
                    self.assertEqual(loaded["hw"]["fx"]["reverb"]["predelay"], 50)

    def test_another_interfaces_workspace_is_refused(self):
        self.write(device="ff802")
        self.refused(f"This workspace is for the {DEVICES['ff802'].name}, not the "
                     f"{self.dev.name}.")
        for key in ("nothing", None, 5):
            self.write(device=key)
            self.refused(f"This workspace is for another interface, not the {self.dev.name}.")

    def test_what_is_not_a_workspace_is_refused(self):
        for content in ("{broken", "[1]", json.dumps({"format": "openface-mixer-mix"}),
                        "[" * 100000):
            with self.subTest(content=content[:30]):
                self.path.write_text(content)
                self.refused("This is not an Openface Mixer workspace.")

    def test_a_damaged_part_is_refused(self):
        for changes, sentence in (
                ({"state": [1]}, "The mix in this workspace is damaged."),
                ({"state": {"names": {}}}, "The mix in this workspace is damaged."),
                ({"presets": 5}, "The presets in this workspace are damaged."),
                ({"presets": {"slots": [{"name": "A", "mix": {"sends": 3}}]}},
                 "The presets in this workspace are damaged.")):
            with self.subTest(changes=changes):
                self.write(**changes)
                self.refused(sentence)
        data = self.write()
        data["state"]["sends"] = "x"
        self.path.write_text(json.dumps(data))
        self.refused("The mix in this workspace is damaged.")

    def test_a_hand_made_remote_setting_is_left_out(self):
        data = self.write()
        data["state"]["remote"] = {"enabled": True}
        self.path.write_text(json.dumps(data))
        self.assertNotIn("remote", import_workspace(self.path, self.dev)[0])

    def test_a_missing_file_is_an_os_error(self):
        with self.assertRaises(OSError):
            import_workspace(self.dir / "none.json", self.dev)

    def test_damaged_options_are_their_defaults(self):
        defaults = {**config.load_meter_prefs({}), "arc_keys": arc.DEFAULT_KEYS}
        for ui in (None, 5, {"peak_hold": "x", "over_samples": 50, "arc_keys": ["boom"]}):
            with self.subTest(ui=ui):
                self.write(ui=ui)
                self.assertEqual(import_workspace(self.path, self.dev)[2], defaults)

    def test_older_contents_are_converted_once(self):
        """A state and presets from before the constant-power pan law are converted as the
        files they come from would be."""
        dev = self.dev
        old = old_state(random.Random(9), dev)
        mix = {k: old[k] for k in m.MIX_KEYS if k != "unlinked"}
        self.path.write_text(json.dumps({
            "format": WORKSPACE_FORMAT, "version": 1, "device": dev.key,
            "state": json.loads(saved(old)),
            "presets": {"version": 1, "slots": [{"name": "Old", "mix": mix}]}}))
        st, slots, _ui = import_workspace(self.path, dev)
        self.assertEqual(st["version"], m.STATE_VERSION)
        state_file = self.dir / "old-state"
        state_file.write_text(saved(old))
        with mock.patch.object(config, "state_file", lambda dev: state_file):
            self.assertEqual(st, config.load_state(dev)[0])     # as the state file would be
        bank = self.dir / "presets.json"
        bank.write_text(json.dumps({"version": 1, "slots": [{"name": "Old", "mix": mix}]}))
        self.assertEqual(slots, presets.PresetBank(bank).slots)
        # saved again, it is today's and is not converted a second time
        export_workspace(self.path, dev, st, slots, UI)
        self.assertEqual(json.loads(self.path.read_text())["presets"]["version"], VERSION)
        again = import_workspace(self.path, dev)
        self.assertEqual((again[0], again[1]), (st, slots))

    def test_reading_changes_nothing_on_disk(self):
        self.write()
        import_workspace(self.path, self.dev)
        self.assertFalse((self.dir / "config").exists())


class WorkspaceWindowTest(test_mix_window.MixWindowTest):
    """Save and Load workspace in the Mix section."""

    def test_save_and_load(self):
        w = self.window()
        w.st["names"]["in:0"] = "Vox"
        w.st["sends"]["in"][0][0] = [-6.0, 0.0]
        w.presets.store(0, "Live", m.extract_mix(w.st))
        w.set_arc_keys(["dim"] + arc.DEFAULT_KEYS[1:])
        self.remote_link.remote_settings(w.st)["port"] = 7100
        path = self.dir / "w.json"
        self.save_name.return_value = (str(path), "")
        w.settings.save_ws_btn.click()
        self.warning.assert_not_called()
        kept = copy.deepcopy(w.st)
        self.assertEqual(json.loads(path.read_text())["device"], self.dev.key)

        # change everything, then load the workspace back
        w.st["names"]["in:0"] = "Gtr"
        w.st["sends"]["in"][0][0] = [-20.0, 0.0]
        w.presets.clear(0)
        w.set_arc_keys(arc.DEFAULT_KEYS)
        self.remote_link.remote_settings(w.st)["port"] = 7200
        before = m.extract_mix(w.st)
        self.open_name.return_value = (str(path), "")
        with mock.patch.object(self.app_module.QApplication, "quit") as quit_app:
            w.settings.load_ws_btn.click()
        quit_app.assert_called_once()                               # main() opens it again
        self.warning.assert_not_called()
        self.ask.assert_called_once()
        self.assertEqual(w.switch_to, self.dev.key)                 # the window reopens
        self.open_windows.remove(w)
        w.deleteLater()
        self.assertEqual(self.previous(), before)                   # kept for Restore
        st, problem = config.load_state(self.dev)
        self.assertIsNone(problem)
        self.assertEqual(st["names"]["in:0"], "Vox")
        self.assertEqual(st["remote"]["port"], 7200)                 # this computer's, as it was
        self.assertEqual(m.extract_mix(st), m.extract_mix(kept))
        self.assertEqual(presets.PresetBank(config.device_dir(self.dev) / "presets.json")
                         .name(0), "Live")
        self.assertEqual(arc.assignment(config.load_ui().get("arc_keys"))[0], "dim")
        # and the window opened again shows it
        again = self.window()
        self.assertEqual(again.st["names"]["in:0"], "Vox")
        self.assertEqual(again.arc_keys[0], "dim")

    def test_a_refused_workspace_changes_nothing(self):
        w = self.window()
        other = DEVICES["ff800"]
        st, _problem = config.load_state(other)
        path = self.dir / "w.json"
        export_workspace(path, other, st, [None] * m.N_SLOTS, UI)
        before = copy.deepcopy(w.st)
        self.open_name.return_value = (str(path), "")
        w.settings.load_ws_btn.click()
        self.warning.assert_called_once()
        self.assertIn(f"This workspace is for the {other.name}, not the {self.dev.name}.",
                      self.warning.call_args[0][2])
        self.ask.assert_not_called()
        self.assertEqual(w.st, before)
        self.assertIsNone(w.switch_to)
        self.assertFalse(config.previous_mix_file(self.dev).exists())

    def test_cancelling_changes_nothing(self):
        w = self.window()
        path = self.dir / "w.json"
        self.save_name.return_value = (str(path), "")
        w.settings.save_ws_btn.click()
        w.st["names"]["in:0"] = "Gtr"
        before = copy.deepcopy(w.st)
        self.open_name.return_value = (str(path), "")
        self.ask.return_value = False
        w.settings.load_ws_btn.click()
        self.assertEqual(w.st, before)
        self.assertIsNone(w.switch_to)
        self.open_name.return_value = ("", "")                      # no file chosen
        w.settings.load_ws_btn.click()
        self.save_name.return_value = ("", "")
        w.settings.save_ws_btn.click()
        self.warning.assert_not_called()

    def test_a_failed_save_shows_the_dialog(self):
        w = self.window()
        self.save_name.return_value = (str(self.dir / "no" / "such" / "w.json"), "")
        with mock.patch.object(self.app_module, "export_workspace",
                               mock.Mock(side_effect=PermissionError(13, "Permission denied"))):
            w.settings.save_ws_btn.click()
        self.warning.assert_called_once()
        self.assertIn("Permission denied", self.warning.call_args[0][2])


# the tests MixWindowTest has are run once, from test_mix_window
for _name in [n for n in vars(test_mix_window.MixWindowTest) if n.startswith("test_")]:
    setattr(WorkspaceWindowTest, _name, None)


if __name__ == "__main__":
    unittest.main()
