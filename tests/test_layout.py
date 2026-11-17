"""Status lights, folding rows, effects panels in the row and the shortcut list, on the real
window offscreen. Needs PySide6 and is skipped without it."""
import os
import re
import unittest
from unittest import mock

from tests.test_controls import WindowCase

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
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


class FoldTest(WindowCase):
    """A click on a row's title folds the row to names and meters; the other rows grow."""

    def setUp(self):
        self.rows = (self.w.row_in, self.w.row_play, self.w.row_out)
        self.addCleanup(self.unfold)

    def unfold(self):
        for r in self.rows:
            r.set_folded(False)
        for s in self.w.strips.values():
            s.solo.setChecked(False)
            s.mute.setChecked(False)
            s.meter.reset_peak()
        self.settle()

    def click(self, widget):
        QTest.mouseClick(widget, Qt.LeftButton)
        self.settle()

    def test_the_title_folds_and_unfolds(self):
        play, out = self.w.row_play, self.w.row_out
        strip, other = play.strips[0], out.strips[0]
        before = other.fader.height()
        shown = [w for w in strip.findChildren(QWidget) if w.isVisible()]
        self.click(play.fold_btn)
        self.assertTrue(play.folded)
        self.assertEqual(self.w.st["folded"], ["play"])
        for s in play.strips:
            self.assertTrue(s.title.isVisible() and s.meter.isVisible())
            self.assertFalse(s.fader.isVisible() or s.mute.isVisible() or s.pan.isVisible()
                             or s.value.isVisible())
            self.assertGreater(s.meter.width(), s.meter.height())      # on its side
            self.assertEqual(s.height(), strip.height())
        self.assertLess(play.height(), 80)
        self.assertGreater(other.fader.height(), before + 40)          # the faders got longer
        self.assertFalse(out.folded or self.w.row_in.folded)
        self.click(play.fold_btn)
        self.assertFalse(play.folded)
        self.assertEqual(self.w.st["folded"], [])
        self.assertEqual(other.fader.height(), before)
        self.assertEqual([w for w in strip.findChildren(QWidget) if w.isVisible()], shown)
        self.assertFalse(other.solo.isVisible() or other.pan.isVisible())  # as before the fold

    def test_space_and_enter_on_the_title(self):
        btn = self.w.row_in.fold_btn
        self.assertEqual(btn.focusPolicy(), Qt.TabFocus)
        btn.setFocus()
        QTest.keyClick(btn, Qt.Key_Space)
        self.settle()
        self.assertTrue(self.w.row_in.folded)
        QTest.keyClick(btn, Qt.Key_Return)
        self.settle()
        self.assertFalse(self.w.row_in.folded)
        self.assertEqual(btn.accessibleName(), "Hardware Inputs")

    def test_a_folded_row_still_shows_solo_mute_and_clip(self):
        from openface_mixer import theme
        row = self.w.row_in
        vocal, guitar, third = row.strips[:3]
        vocal.solo.click()
        guitar.mute.click()
        self.click(row.fold_btn)
        self.assertIn(theme.SOLO.name(), vocal.styleSheet())
        self.assertIn(f"color:{theme.MUTED}", guitar.styleSheet())
        self.assertNotIn(theme.SOLO.name(), guitar.styleSheet())
        self.assertIn("SOLO", row.head.text())
        self.assertIn(vocal.name, row.head.text())
        self.assertNotIn("CLIP", row.head.text())
        self.assertTrue(self.w.st["solo"]["in"][0] and self.w.st["mute"]["in"][1])
        third.meter.push([1.0] * len(third.channels), 0.03)             # meters keep moving
        self.assertEqual(third.meter.levels[0], 1.0)
        self.assertTrue(third.meter.clip[0])
        self.assertIn(theme.RED.name(), third.styleSheet())
        self.assertIn("CLIP", row.head.text())
        self.assertIn(third.name, row.head.text())
        self.click(third.meter)                                         # resets peak and clip
        self.assertNotIn("CLIP", row.head.text())
        self.assertNotIn(f"border:1px solid {theme.RED.name()}", third.styleSheet())
        self.w.st["solo"]["in"][0] = False                              # as a remote would
        self.w.sync_strips()
        self.assertNotIn("SOLO", row.head.text())
        self.click(row.fold_btn)
        self.assertNotIn(f"color:{theme.MUTED}", guitar.styleSheet())
        self.assertNotIn("folded", row.head.text())

    def test_names_in_a_folded_row(self):
        out, play = self.w.row_out, self.w.row_play
        self.click(out.fold_btn)
        self.click(play.fold_btn)
        first = out.strips[0]
        self.click(first.title)
        self.assertEqual(self.w.st["selected"], first.pair)             # an output: its submix
        self.assertTrue(out.folded)
        self.assertNotIn("unfold", out.head.text())
        self.assertIn("click a name to unfold", play.head.text())
        self.click(play.strips[1].title)                                # a source: unfolds
        self.assertFalse(play.folded)
        self.click(play.strips[1].title)
        self.assertFalse(play.folded)

    def test_every_row_folded_and_strips_rebuilt(self):
        for r in self.rows:
            self.click(r.fold_btn)
        self.assertEqual(self.w.st["folded"], ["in", "play", "out"])
        self.assertLess(self.w.row_out.geometry().bottom(), 300)        # stacked at the top
        self.w.hide_strip(self.w.row_in.strips[0])                      # rebuilds the rows
        self.w.toast.hide()
        self.settle()
        for r in self.rows:
            self.assertTrue(r.strips and all(s.folded and not s.fader.isVisible()
                                             for s in r.strips))
        self.w.unhide_all()
        self.w.st["hidden"] = list(self.w.dev.channel_groups())         # no strips at all
        self.w.channels_shown()
        self.settle()
        self.assertEqual(self.w.row_in.strips, [])
        self.w.st["hidden"] = []
        self.w.channels_shown()
        self.click(self.w.row_in.fold_btn)
        self.assertTrue(self.w.row_in.strips[0].fader.isVisible())

    def test_folded_rows_are_remembered_with_the_device(self):
        from openface_mixer import app, config
        self.click(self.w.row_play.fold_btn)
        self.w.save()
        self.assertEqual(config.load_state(self.w.dev)[0]["folded"], ["play"])
        again = app.MainWindow(self.w.dev)
        self.addCleanup(lambda: (again.close(), again.deleteLater(), self.settle()))
        self.settle()                           # its start-up timers run while it is alive
        self.assertEqual([r.folded for r in (again.row_in, again.row_play, again.row_out)],
                         [False, True, False])
        self.assertTrue(all(s.folded for s in again.row_play.strips))
        self.assertTrue(again.row_play.fold_btn.isChecked())


if __name__ == "__main__":
    unittest.main()
