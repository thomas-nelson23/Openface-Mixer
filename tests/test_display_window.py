"""The Display section on the real window: the meter options and the layout presets.
Offscreen, with the engine and the system stubbed (test_controls.WindowCase); skipped without
PySide6."""
import json
import unittest
from unittest import mock

from openface_mixer import config, model as m
from tests.test_controls import WindowCase


class MeterCase(WindowCase):
    def setUp(self):
        from openface_mixer import config, widgets
        self.config, self.Meter = config, widgets.Meter
        for name in ("hold_s", "show_rms", "over_count"):
            self.addCleanup(setattr, self.Meter, name, getattr(self.Meter, name))

    def choose(self, combo, value):
        combo.setCurrentIndex(combo.findData(value))
        combo.activated.emit(combo.currentIndex())


class MeterOptionsTest(MeterCase):
    def test_the_options_apply_at_once_and_are_kept_for_this_computer(self):
        panel = self.w.settings
        self.assertFalse(panel.rms_box.isHidden())          # the Digiface reports RMS
        self.assertIsNone(panel.over_combo)                 # but counts no overs
        self.assertEqual((self.Meter.show_rms, self.Meter.hold_s, self.Meter.over_count),
                         (True, 1.5, 1))
        self.choose(panel.hold_combo, 3.0)
        panel.rms_box.setChecked(False)
        self.assertEqual((self.Meter.show_rms, self.Meter.hold_s), (False, 3.0))
        ui = json.loads(self.config.UI_FILE.read_text())
        self.assertEqual((ui["meter_rms"], ui["peak_hold"]), (False, 3.0))
        self.assertEqual(self.config.load_meter_prefs()["peak_hold"], 3.0)
        self.assertNotIn("peak_hold", self.w.st)            # not part of the mix or the state
        panel.rms_box.setChecked(True)
        self.choose(panel.hold_combo, 1.5)


class HdspeMeterOptionsTest(MeterCase):
    device = "raydat"

    def test_clip_after_is_offered_where_the_card_counts_overs(self):
        panel = self.w.settings
        self.choose(panel.over_combo, 3)
        self.assertEqual(self.Meter.over_count, 3)
        self.assertEqual(self.config.load_meter_prefs()["over_samples"], 3)
        self.choose(panel.over_combo, 1)


class FireWireMeterOptionsTest(WindowCase):
    device = "ff800"

    def test_no_rms_option_where_the_device_reports_none(self):
        self.assertTrue(self.w.settings.rms_box.isHidden())
        self.assertIsNone(self.w.settings.over_combo)


class LayoutTest(WindowCase):
    def setUp(self):
        from openface_mixer import app
        self.name = mock.Mock(return_value=("", False))     # no dialog ever opens in a test
        patch = mock.patch.object(app.QInputDialog, "getText", self.name)
        patch.start()
        self.addCleanup(patch.stop)

    def store(self, i, name):
        self.name.return_value = (name, True)
        self.w.settings.layout_btns[i].click()
        self.name.return_value = ("", False)

    def view(self):
        return {k: self.w.st[k] for k in m.LAYOUT_KEYS}

    def test_store_and_recall(self):
        w, panel = self.w, self.w.settings
        dev = w.dev
        group = dev.channel_groups()[-1]
        solo = w.row_in.strips[1]
        solo.solo.setChecked(True)
        mix = m.extract_mix(w.st)
        self.store(0, "")                               # everything shown
        self.assertEqual(w.st["layouts"][0]["name"], "Layout 1")
        panel.show_boxes[group].setChecked(False)
        w.hide_strip(solo)
        w.row_play.set_folded(True)
        panel.names_box.setChecked(False)
        hidden = self.view()
        self.assertEqual((hidden["hidden"], hidden["hidden_strips"], hidden["folded"],
                          hidden["custom_names"]), ([group], [solo.key], ["play"], False))
        self.assertFalse(any(w.st["solo"]["in"]))       # hiding switched its solo off
        solo_key = solo.key
        self.store(1, "Small")
        self.assertTrue(panel.layout_btns[1].property("filled"))
        panel.layout_btns[0].click()                    # recall: all back
        self.assertEqual(self.view(), {"hidden": [], "hidden_strips": [], "folded": [],
                                       "custom_names": True})
        self.assertTrue(panel.show_boxes[group].isChecked() and panel.names_box.isChecked())
        self.assertFalse(w.row_play.folded)
        self.assertIn(solo_key, w.strips)
        w.strips[solo_key].solo.setChecked(True)
        panel.layout_btns[1].click()
        self.assertEqual(self.view(), hidden)
        self.assertTrue(w.row_play.folded)
        self.assertFalse(panel.show_boxes[group].isChecked() or panel.names_box.isChecked())
        self.assertNotIn(solo_key, w.strips)
        self.assertFalse(any(w.st["solo"]["in"]))       # nothing out of sight stays soloed
        self.assertEqual(m.extract_mix(w.st), mix)      # a layout never changes the sound
        # Store armed: overwrites a filled slot; Esc un-arms it
        panel.layout_store.setChecked(True)
        w.escape()
        self.assertFalse(panel.layout_store.isChecked())
        panel.layout_store.setChecked(True)
        self.store(0, "Again")
        self.assertEqual(w.st["layouts"][0]["name"], "Again")
        self.assertEqual(w.st["layouts"][0]["hidden"], [group])
        panel.layout_btns[5].click()                    # the name was cancelled: no store
        self.assertIsNone(w.st["layouts"][5])
        # kept with the device's state
        w.save()
        st, problem = config.load_state(dev)
        self.assertIsNone(problem)
        self.assertEqual(st["layouts"], w.st["layouts"])
        panel.layout_btns[1].click()
        self.assertEqual(self.view(), hidden)
        panel.layout_btns[0].click()                    # "Again" was stored from that view
        self.assertEqual(self.view(), hidden)
        # back to all shown for the next test
        w.st["layouts"][7] = {"name": "All", "hidden": [], "hidden_strips": [], "folded": [],
                              "custom_names": True}
        panel.layout_btns[7].click()
        w.st["layouts"] = [None] * m.N_SLOTS
        panel.refresh_layouts()


class HdspeLayoutTest(LayoutTest):      # other channel groups; every device: test_config
    device = "aio"


if __name__ == "__main__":
    unittest.main()
