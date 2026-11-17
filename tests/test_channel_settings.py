"""The channel settings popup on the real widgets, offscreen. Needs PySide6 and is skipped
without it."""
import os
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PySide6.QtWidgets import QApplication, QCheckBox
except ImportError:
    QApplication = None


@unittest.skipUnless(QApplication, "needs PySide6")
class Fireface802Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def popup(self, s, kind, channels):
        from openface_mixer.channel_settings import ChannelSettings
        from openface_mixer.devices import DEVICES
        popup = ChannelSettings(DEVICES["ff802"], s, kind, channels, "test")
        self.addCleanup(popup.deleteLater)
        return popup

    def test_fx_send_of_a_linked_pair_is_the_left_channels(self):
        from openface_mixer import fireface802 as ff
        from openface_mixer.fx_panels import LevelKnob
        s = ff.default_settings()
        s["fx"]["send"]["in"][3] = -20.0                            # AN 4's own, kept
        knob = self.popup(s, "in", [2, 3]).findChild(LevelKnob)
        knob.set_value(-6.0, emit=True)
        self.assertEqual(s["fx"]["send"]["in"][2:4], [-6.0, -20.0])
        s["fx"]["ret"][1] = -20.0
        knob = self.popup(s, "out", [0, 1]).findChild(LevelKnob)   # an output pair shares one
        knob.set_value(-6.0, emit=True)
        self.assertEqual(s["fx"]["ret"][0:2], [-6.0, -6.0])


if __name__ == "__main__":
    unittest.main()
