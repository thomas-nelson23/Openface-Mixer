"""The meter's logic: RMS, the peak hold time and the full-scale samples that light the clip
light. Needs PySide6 and is skipped without it."""
import os
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipUnless(QApplication, "needs PySide6")
class MeterTest(unittest.TestCase):
    def setUp(self):
        from openface_mixer import theme, widgets
        self.app = QApplication.instance() or QApplication([])
        theme.apply_theme(self.app, 1.0)
        for name in ("hold_s", "show_rms", "over_count"):     # the class holds the options
            self.addCleanup(setattr, widgets.Meter, name, getattr(widgets.Meter, name))
        self.Meter = widgets.Meter
        self.meter = widgets.Meter(2)
        self.addCleanup(self.app.sendPostedEvents, None, QEvent.DeferredDelete)
        self.addCleanup(self.meter.deleteLater)
        self.now = 100.0
        clock = mock.patch.object(widgets.time, "monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def test_rms_follows_the_device_and_falls_like_the_peak(self):
        m = self.meter
        m.push([0.5, 0.0], 0.03)
        self.assertIsNone(m.rms)                    # a device without RMS: none to draw
        m.push([0.5, 0.5], 0.03, rms=[0.25, 0.0])
        self.assertEqual(m.rms, [0.25, 0.0])
        m.push([0.0, 0.0], 1.0, rms=[0.0, 0.0])     # a second later: 26 dB lower
        self.assertAlmostEqual(m.rms[0], 0.25 * 10 ** (-26 / 20))
        m.push([0.0, 0.0], 10.0, rms=[0.0, 0.0])    # below the scale
        self.assertEqual(m.rms, [0.0, 0.0])
        m.grab()                                    # paints with and without RMS
        self.Meter.show_rms = False
        m.grab()

    def test_peak_hold_time(self):
        m = self.meter
        self.Meter.hold_s = 3.0
        m.push([0.5, 0.0], 0.03)
        self.now += 2.9
        m.push([0.0, 0.0], 2.9)
        self.assertEqual(m.holds[0][0], 0.5)        # still held
        self.now += 0.2
        m.push([0.0, 0.0], 0.2)
        self.assertLess(m.holds[0][0], 0.5)         # let go after 3 s
        self.Meter.hold_s = 0.1
        m.push([0.4, 0.0], 0.03)
        self.now += 0.15
        m.push([0.0, 0.0], 0.15)
        self.assertLess(m.holds[0][0], 0.4)

    def test_one_full_scale_sample_clips_where_the_device_counts_none(self):
        self.Meter.over_count = 5
        self.meter.push([1.0, 0.5], 0.03)
        self.assertEqual(self.meter.clip, [True, False])

    def test_clip_after_the_chosen_number_of_full_scale_samples(self):
        m = self.meter
        self.Meter.over_count = 3
        m.push([1.0, 1.0], 0.03, overs=[2.0, 3.0])
        self.assertEqual(m.clip, [False, True])
        m.push([0.5, 0.0], 0.03, overs=[3.0, 0.0])  # a count with no full-scale peak
        self.assertEqual(m.clip, [False, True])
        m.reset_peak()
        self.Meter.over_count = 1                   # the default: one sample, as before
        m.push([1.0, 0.0], 0.03, overs=[0.0, 0.0])
        self.assertEqual(m.clip, [True, False])


if __name__ == "__main__":
    unittest.main()
