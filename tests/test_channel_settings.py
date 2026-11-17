"""The channel settings popup, and the effects in the main window, on the real widgets,
offscreen. Needs PySide6 and is skipped without it."""
import os
import unittest
from unittest import mock

from tests.test_controls import WindowCase

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

    def test_48v_is_locked_while_either_side_of_a_pair_has_inst(self):
        from openface_mixer import fireface802 as ff
        s = ff.default_settings()
        s["in"]["inst"][1] = True                                   # AN 10 only
        popup = self.popup(s, "in", [8, 9])                         # AN 9/10 linked
        boxes = {b.text(): b for b in popup.findChildren(QCheckBox)}
        p48, inst = boxes["48V phantom power"], boxes["Inst (Hi-Z)"]
        self.assertTrue(inst.isChecked() and not p48.isEnabled())
        p48.click()                                                 # can't be stored for later
        self.assertEqual(s["in"]["p48"][:2], [False, False])
        inst.click()
        self.assertEqual(s["in"]["inst"][:2], [False, False])
        self.assertTrue(p48.isEnabled())

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


class WindowTest(WindowCase):
    """What the strips do reaches the 802: the words the window hands the engine."""
    device = "ff802"

    def setUp(self):
        from openface_mixer import engine
        patch = mock.patch.object(engine.Engine, "write_device", autospec=True)
        self.write_device = patch.start()
        self.addCleanup(patch.stop)
        self.f = self.w.st["hw"]["fx"]

    def words(self):
        self.settle()
        return self.write_device.call_args[0][1][1]

    def test_mute_and_solo_cut_the_fx_send(self):
        an3 = self.w.strips["in:2"]
        self.f["send"]["in"][2] = self.f["send"]["in"][3] = -12.0
        an3.mute.click()
        self.assertIn(0x0201FD76, self.words())             # AN 3 FX send off
        self.assertIn(0x0301FF88, self.words())             # AN 4 still -12.0 dB
        an3.mute.click()
        self.assertIn(0x0201FF88, self.words())
        an3.solo.click()
        self.assertIn(0x0201FF88, self.words())
        self.assertIn(0x0301FD76, self.words())             # left out by AN 3's solo
        an3.solo.click()
        self.assertIn(0x0301FF88, self.words())
        self.assertEqual(self.f["send"]["in"][2:4], [-12.0, -12.0])

    def test_a_linked_pair_shows_and_sends_its_left_channel(self):
        self.f["in"][5].update(eq=True, eq_g2=6.0)          # AN 6 only
        self.w.refresh_settings_buttons()
        self.assertTrue(self.w.strips["in:5"].eq_btn.property("on"))
        self.w.strips["in:4"].stereo.click()                # link AN 5/6
        words = self.words()
        self.assertNotIn("in:5", self.w.strips)
        self.assertFalse(self.w.strips["in:4"].eq_btn.property("on"))
        self.assertIn(0x05400000, words)                    # AN 6 EQ off in the unit, as AN 5
        self.assertIn(0x05450000, words)                    # ... band 2 gain 0 dB
        self.assertEqual((self.f["in"][5]["eq"], self.f["in"][5]["eq_g2"]), (True, 6.0))
        self.w.strips["in:4"].stereo.click()                # unlink: AN 6 has its own again
        words = self.words()
        self.assertIn(0x05400001, words)
        self.assertIn(0x0545003C, words)
        self.assertTrue(self.w.strips["in:5"].eq_btn.property("on"))

    def test_no_reply_is_connected(self):
        from openface_mixer import engine
        status = {"processing": True, "rate": 0, "sink_linked": True, "hw": "no-reply",
                  "hw_nodes": 0, "hw_levels": False, "dev_status": 0, "dev_status2": 0}
        with mock.patch.object(engine.Engine, "status", lambda self: status):
            self.w.poll_engine()
            self.assertIn("Connected, no reply from the DSP", self.w.settings.hw_label.text())
            self.assertIn("no reply", self.w.status.toolTip())
            self.assertNotIn("not connected", self.w.settings.rate.text())
            status["hw"] = "no-device"
            self.w.poll_engine()
            self.assertIn("not connected", self.w.settings.rate.text())
        self.w.poll_engine()


if __name__ == "__main__":
    unittest.main()
