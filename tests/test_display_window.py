"""The Display section on the real window: the meter options. Offscreen, with the engine and
the system stubbed (test_controls.WindowCase); skipped without PySide6."""
import json
import unittest

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


if __name__ == "__main__":
    unittest.main()
