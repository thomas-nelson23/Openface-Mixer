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

    def test_inst_switches_48v_off_for_good(self):
        from openface_mixer import fireface802 as ff
        s = ff.default_settings()
        s["in"]["p48"][0] = True                                    # AN 9
        popup = self.popup(s, "in", [8])
        changes = []
        popup.changed.connect(lambda: changes.append(1))
        boxes = {b.text(): b for b in popup.findChildren(QCheckBox)}
        p48, inst = boxes["48V phantom power"], boxes["Inst (Hi-Z)"]
        self.assertTrue(p48.isChecked() and p48.isEnabled())
        inst.click()
        self.assertEqual((s["in"]["inst"][0], s["in"]["p48"][0]), (True, False))
        self.assertFalse(p48.isChecked() or p48.isEnabled())       # no 48V while Inst is on
        self.assertTrue(changes)
        inst.click()
        self.assertEqual((s["in"]["inst"][0], s["in"]["p48"][0]), (False, False))
        self.assertTrue(p48.isEnabled() and not p48.isChecked())    # 48V did not come back
        p48.click()
        self.assertTrue(s["in"]["p48"][0])

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
