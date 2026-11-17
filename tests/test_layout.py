"""Status lights, folding rows, effects panels in the row and the shortcut list, on the real
window offscreen. Needs PySide6 and is skipped without it."""
import os
import re
import unittest
from unittest import mock

from tests.test_controls import WindowCase

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

STATUS = {"processing": True, "rate": 48000, "sink_linked": True, "hw": "active",
          "hw_nodes": 12, "hw_levels": True, "dev_status": 0, "dev_status2": 0}


def dot(html):
    """The colour of the light a status text starts with."""
    found = re.match(r"<span style='color:(#[0-9a-f]{6})'>●</span>", html)
    return found and found.group(1)


class LightsTest(WindowCase):
    """Every light is a dot: cyan when on, red for a fault, grey when off or not known."""
    device = "raydat"

    def poll(self, **status):
        from openface_mixer import engine
        with mock.patch.object(engine.Engine, "status",       # nothing given: no engine
                               lambda _self: {**STATUS, **status} if status else None):
            self.w.poll_engine()
        return self.w.settings

    def test_hardware_states(self):
        from openface_mixer import engine, theme
        cyan, red, grey = theme.CYAN.name(), theme.RED.name(), theme.MUTED
        want = dict.fromkeys(engine.HW_STATES, red)
        want.update(active=cyan, starting=grey)
        for state, color in want.items():
            panel = self.poll(hw=state)
            self.assertEqual(dot(panel.hw_label.text()), color, state)
            self.assertEqual(dot(self.w.status.text()), color, state)   # the top-bar pill
        self.assertIn("Check driver", self.poll(hw="wrong-driver").hw_label.text())
        self.assertIn("Connecting", self.poll(hw="starting").hw_label.text())

    def test_engine_and_sink(self):
        from openface_mixer import theme
        cyan, red = theme.CYAN.name(), theme.RED.name()
        panel = self.poll(hw="active")
        self.assertEqual(dot(panel.eng_label.text()), cyan)
        self.assertIsNone(dot(panel.sink_label.text()))             # linked: just the words
        panel = self.poll(processing=False, sink_linked=False)
        self.assertEqual(dot(panel.eng_label.text()), red)
        self.assertEqual(dot(panel.sink_label.text()), red)
        panel = self.poll()                                         # no engine
        self.assertEqual(dot(panel.eng_label.text()), red)
        self.assertEqual(dot(self.w.status.text()), red)
        self.assertIn("Engine stopped", self.w.status.text())

    def test_no_ring_and_no_amber_anywhere(self):
        from PySide6.QtWidgets import QLabel
        from openface_mixer import engine, theme
        from openface_mixer.settings_panel import Led, SyncLight
        seen = []
        polls = [{"hw": state, "processing": ok, "sink_linked": ok}
                 for state in engine.HW_STATES for ok in (True, False)]
        for status in (*polls, {}):                                 # {}: no engine
            self.poll(**status)
            seen += [lbl.text() for lbl in self.w.findChildren(QLabel)]
        for status in ("connected", "absent", "no-access", "busy"):
            self.w.control.set_arc_status(status)
            seen.append(self.w.control.arc_label.text())
        for status, error in (("off", None), ("listening on UDP 7001", None), ("x", "host"),
                              ("error: port in use", None)):
            self.w.remote_panel.set_status(status, error)
            seen.append(self.w.remote_panel.status.text())
        led = Led()
        self.addCleanup(led.deleteLater)
        for state in ("Sync", "Lock", "No Lock", None):
            led.set_state(state)
            seen.append(led.text())
            self.assertEqual(dot(led.text()), theme.CYAN.name() if state in ("Sync", "Lock")
                             else theme.MUTED, state)
        for light in self.w.findChildren(SyncLight):
            light.set_current(None)
            self.assertEqual(dot(light.text()), theme.MUTED)
            seen.append(light.text())
        text = "\n".join(seen)
        self.assertIn("●", text)
        self.assertNotIn("○", text)
        self.assertNotIn(theme.WARN.name(), text)

    def test_digiface_profile_lights(self):
        from openface_mixer import theme
        from openface_mixer.devices import DEVICES
        from openface_mixer.hardware import Hardware
        from openface_mixer.model import default_state
        from openface_mixer.settings_panel import SettingsPanel
        dev = DEVICES["digiface"]
        with mock.patch("openface_mixer.updater.install_source", return_value=None):
            panel = SettingsPanel(Hardware(dev.alsa_card), dev, default_state())
        self.addCleanup(panel.deleteLater)
        panel.update_profile(None, None, False)
        self.assertEqual(dot(panel.profile.text()), theme.MUTED)
        panel.update_profile("card", "pro-audio", False)
        self.assertEqual(dot(panel.recording.text()), theme.MUTED)
        self.assertIn("monitoring still works", panel.recording.text())
        panel.update_profile("card", "pro-audio", True)
        self.assertEqual(dot(panel.recording.text()), theme.CYAN.name())


if __name__ == "__main__":
    unittest.main()
