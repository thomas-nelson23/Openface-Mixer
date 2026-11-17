"""Status lights, folding rows, effects panels in the row and the shortcut list, on the real
window offscreen. Needs PySide6 and is skipped without it."""
import os
import re
import unittest
from unittest import mock

from tests.test_controls import WindowCase

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QToolButton, QWidget
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


class PanelTest(WindowCase):
    """EQ and Dynamics open in the row, right of their strip; Reverb / Echo at the end of the
    outputs row."""
    device = "ff802"

    def setUp(self):
        self.addCleanup(self.tidy)

    def tidy(self):
        for panel in list(self.w.fx_panels.values()):
            panel.close()
        self.w.store_btn.setChecked(False)
        for r in (self.w.row_in, self.w.row_play, self.w.row_out):
            r.set_folded(False)
        self.w.tabs.setCurrentIndex(0)
        self.settle()

    def open(self, strip, which):
        (strip.eq_btn if which == "eq" else strip.dyn_btn).click()
        self.settle()
        return self.w.fx_panels[(which, strip.key)]

    def order(self, row):
        lay = row.scroll.widget().layout()
        return [lay.itemAt(i).widget() for i in range(lay.count()) if lay.itemAt(i).widget()]

    def test_a_panel_is_in_the_row_right_of_its_strip(self):
        row = self.w.row_in
        first, strip, after = row.strips[:3]
        before = after.x()
        self.w.activateWindow()
        self.settle()
        dyn = self.open(strip, "dyn")
        eq = self.open(strip, "eq")
        self.assertEqual(self.order(row)[:5], [first, strip, eq, dyn, after])   # EQ comes first
        self.assertIs(QApplication.activeWindow(), self.w)          # no window came and went
        self.assertEqual([t for t in QApplication.topLevelWidgets() if t.isVisible()], [self.w])
        for panel in (eq, dyn):
            self.assertFalse(panel.isWindow())
            self.assertIs(panel.window(), self.w)
            self.assertTrue(panel.isVisible())
            self.assertEqual(panel.height(), strip.height())
        self.assertEqual(eq.tag.text(), strip.name)
        self.assertGreater(eq.x(), strip.geometry().right())
        self.assertGreater(dyn.x(), eq.geometry().right())
        self.assertEqual(after.x(), before + eq.width() + dyn.width() + 2 * row.strips[1].x()
                         - 2 * first.width())                       # moved right, same gaps
        self.assertFalse(row.scroll.verticalScrollBar().isVisible())
        self.assertIn("#f5f8fa", strip.eq_btn.styleSheet())         # the white edge: it is open
        self.assertNotIn("#f5f8fa", first.eq_btn.styleSheet())
        strip.eq_btn.click()                                        # the button closes it again
        strip.dyn_btn.click()
        self.settle()
        self.assertEqual(self.w.fx_panels, {})
        self.assertEqual(row.docks, {})
        self.assertEqual(after.x(), before)
        self.assertNotIn("#f5f8fa", strip.eq_btn.styleSheet())

    def test_two_strips_have_panels_at_once(self):
        vocal, out = self.w.row_in.strips[0], self.w.row_out.strips[2]
        panels = [self.open(vocal, "eq"), self.open(out, "dyn"), self.open(out, "eq")]
        self.assertTrue(all(p.isVisible() for p in panels))
        self.assertEqual(self.order(self.w.row_out)[2:5], [out, panels[2], panels[1]])
        panels[0].findChild(QToolButton, "fxclose").click()         # its × button
        self.settle()
        self.assertEqual(set(self.w.fx_panels), {("dyn", out.key), ("eq", out.key)})
        panels[1].switches["dyn"].click()                           # the panel edits its strip
        self.assertTrue(self.w.st["hw"]["fx"]["out"][out.channels[0]]["dyn"])
        self.assertTrue(out.dyn_btn.property("on"))
        panels[1].switches["dyn"].click()

    def test_esc_and_ctrl_w_close_a_panel(self):
        strip, other = self.w.row_in.strips[0], self.w.row_in.strips[4]
        eq = self.open(strip, "eq")
        self.open(other, "dyn")
        eq.switches["lc"].setFocus()                                # the keyboard is in the EQ
        QTest.keyClick(eq.switches["lc"], Qt.Key_Escape)
        self.settle()
        self.assertEqual(set(self.w.fx_panels), {("dyn", other.key)})
        self.assertIs(self.w.focusWidget(), strip.eq_btn)           # back on the button
        eq = self.open(strip, "eq")
        self.assertIs(self.w.focusWidget(), eq)                     # a new panel has the keyboard
        QTest.keyClick(eq, Qt.Key_W, Qt.ControlModifier)
        self.settle()
        self.assertEqual(set(self.w.fx_panels), {("dyn", other.key)})
        eq = self.open(strip, "eq")
        self.w.row_play.strips[0].fader.setFocus()                  # outside: the one opened last
        self.w.store_btn.setChecked(True)
        QTest.keyClick(self.w, Qt.Key_Escape)                       # first un-arms Store
        self.settle()
        self.assertFalse(self.w.store_btn.isChecked())
        self.assertEqual(len(self.w.fx_panels), 2)
        QTest.keyClick(self.w, Qt.Key_Escape)
        self.settle()
        self.assertEqual(set(self.w.fx_panels), {("dyn", other.key)})
        QTest.keyClick(self.w, Qt.Key_W, Qt.ControlModifier)
        self.settle()
        self.assertEqual(self.w.fx_panels, {})
        QTest.keyClick(self.w, Qt.Key_Escape)                       # nothing left to close
        QTest.keyClick(self.w, Qt.Key_W, Qt.ControlModifier)
        self.assertTrue(self.w.isVisible())

    def test_reverb_and_echo_are_at_the_end_of_the_outputs_row(self):
        out = self.w.row_out
        self.w.tabs.setCurrentIndex(1)
        out.set_folded(True)
        self.w.fx_btn.click()
        self.settle()
        unit = self.w.fx_panels[("unit", "")]
        self.assertEqual([t for t in QApplication.topLevelWidgets() if t.isVisible()], [self.w])
        self.assertEqual(self.w.tabs.currentIndex(), 0)             # the mixer is shown
        self.assertFalse(out.folded)                                # and the row unfolded
        self.assertIs(self.order(out)[-1], unit)
        self.assertFalse(unit.isWindow())
        self.assertTrue(unit.isVisible())
        self.assertGreater(out.scroll.horizontalScrollBar().value(), 0)     # scrolled into view
        eq = self.open(out.strips[-1], "eq")
        self.assertEqual(self.order(out)[-3:], [out.strips[-1], eq, unit])
        unit.rv_on.click()
        self.assertTrue(self.w.st["hw"]["fx"]["reverb"]["on"])
        unit.rv_on.click()
        self.w.fx_btn.click()
        self.settle()
        self.assertNotIn(("unit", ""), self.w.fx_panels)
        self.w.fx_btn.click()
        self.settle()
        self.w.fx_panels[("unit", "")].close()
        self.settle()
        self.assertFalse(self.w.fx_btn.isChecked())

    def test_folding_a_row_closes_its_panels(self):
        row, out = self.w.row_in, self.w.row_out
        strip = row.strips[0]
        self.open(strip, "eq")
        self.open(out.strips[0], "dyn")
        self.w.fx_btn.click()
        self.settle()
        row.fold_btn.click()
        self.settle()
        self.assertEqual(set(self.w.fx_panels), {("dyn", out.strips[0].key), ("unit", "")})
        self.assertFalse(strip.eq_btn.isVisible())
        with mock.patch("openface_mixer.app.QMenu") as menu:        # no EQ in the strip's menu
            self.w.strip_menu(strip, QPoint())
        added = [c.args[0] for c in menu.return_value.addAction.call_args_list]
        self.assertIn("Rename…", added)
        self.assertNotIn("EQ…", added)
        out.fold_btn.click()
        self.settle()
        self.assertEqual(self.w.fx_panels, {})
        self.assertFalse(self.w.fx_btn.isChecked())
        row.fold_btn.click()                                        # unfolding opens nothing
        out.fold_btn.click()
        self.settle()
        self.assertEqual(self.w.fx_panels, {})
        self.assertEqual((row.docks, out.docks), ({}, {}))

    def test_panels_stay_when_the_strips_are_rebuilt(self):
        row = self.w.row_in
        keep, gone, pair = row.strips[0], row.strips[2], row.strips[4]
        dyn = self.open(keep, "dyn")
        self.open(gone, "eq")
        self.open(pair, "eq")
        self.w.hide_strip(gone)
        self.w.toast.hide()
        self.w.toggle_stereo("in", pair.channels[0], len(pair.channels) == 1)
        self.settle()
        self.addCleanup(self.w.toggle_stereo, "in", pair.channels[0], len(pair.channels) == 2)
        self.addCleanup(self.w.unhide_all)
        self.assertEqual(set(self.w.fx_panels), {("dyn", keep.key)})    # the others' strips went
        strip = self.w.strips[keep.key]
        self.assertIsNot(strip, keep)
        self.assertEqual(self.order(row)[:2], [strip, dyn])
        self.assertTrue(dyn.isVisible())
        self.assertIn("#f5f8fa", strip.dyn_btn.styleSheet())
        self.w.st["names"][keep.key] = "Kick"
        self.w.st["custom_names"] = True
        self.addCleanup(self.w.st["names"].pop, keep.key)
        self.w.rebuild_all()
        self.assertEqual(dyn.tag.text(), "Kick")
        self.w.set_color(self.w.strips[keep.key], "#56e39f")
        self.addCleanup(lambda: self.w.set_color(self.w.strips[keep.key], None))
        self.assertIn("#56e39f", dyn.tag.styleSheet())
        dyn.switches["al"].click()                                  # still edits its channel
        self.assertTrue(self.w.st["hw"]["fx"]["in"][keep.channels[0]]["al"])
        dyn.switches["al"].click()

    def test_a_panel_fits_a_low_row_and_a_tall_one(self):
        from openface_mixer import theme
        strip = self.w.row_in.strips[0]
        eq, dyn = self.open(strip, "eq"), self.open(strip, "dyn")
        self.w.fx_btn.click()
        self.settle()
        unit = self.w.fx_panels[("unit", "")]
        self.assertLess(strip.height(), 200)                        # three rows in 720 pixels
        self.assertTrue(eq.low and unit.low)
        for panel in (eq, dyn, unit):                               # no higher than the strips
            self.assertLessEqual(panel.minimumSizeHint().height(), strip.minimumSizeHint().height())
            self.assertEqual(panel.height(), strip.height())
        wide = eq.width()
        self.w.row_play.set_folded(True)                            # the open rows get its height
        self.settle()
        self.assertGreater(strip.height(), 250)
        self.assertFalse(eq.low or unit.low)
        for panel in (eq, unit):                    # the tall layout fits where it is used
            self.assertLessEqual(panel.minimumSizeHint().height(), theme.px(panel.TALL))
        self.assertLess(eq.width(), wide)                           # the curve is over the knobs
        self.assertEqual(eq.height(), strip.height())
        self.assertGreater(dyn.curve.width(), 150)
        self.w.row_play.set_folded(False)
        self.settle()
        self.assertTrue(eq.low and unit.low)
        self.assertEqual(eq.width(), wide)


class RoomEqTest(WindowCase):
    """The UCX II's Room EQ is too tall for a row: it stays a window of its own."""
    device = "ucx2"

    def test_room_eq_is_a_window_and_eq_is_not(self):
        out = self.w.row_out.strips[0]
        self.w.open_fx_panel(out, "room")
        self.w.open_fx_panel(out, "eq")
        self.settle()
        room, eq = self.w.fx_panels[("room", out.key)], self.w.fx_panels[("eq", out.key)]
        self.addCleanup(lambda: [p.close() for p in list(self.w.fx_panels.values())])
        self.assertTrue(room.isWindow() and room.isVisible())
        self.assertIn(out.name, room.windowTitle())
        self.assertFalse(eq.isWindow())
        self.assertEqual(list(self.w.row_out.docks), [eq])
        self.w.open_fx_panel(out, "room")                           # again: the same window
        self.assertIs(self.w.fx_panels[("room", out.key)], room)
        self.w.row_out.set_folded(True)                             # folding leaves the window
        self.settle()
        self.addCleanup(self.w.row_out.set_folded, False)
        self.assertEqual(set(self.w.fx_panels), {("room", out.key)})
        QTest.keyClick(room, Qt.Key_Escape)
        self.settle()
        self.assertEqual(self.w.fx_panels, {})


class NoEffectsTest(WindowCase):
    """A device without TotalMix FX has no panels and nothing that opens one."""

    def test_nothing_to_open(self):
        self.assertTrue(self.w.fx_btn.isHidden())
        self.assertTrue(all(s.eq_btn.isHidden() and s.dyn_btn.isHidden()
                            for s in self.w.strips.values()))
        self.w.store_btn.setChecked(True)
        QTest.keyClick(self.w, Qt.Key_Escape)
        self.assertFalse(self.w.store_btn.isChecked())
        QTest.keyClick(self.w, Qt.Key_Escape)
        QTest.keyClick(self.w, Qt.Key_W, Qt.ControlModifier)
        QTest.keyClick(self.w, Qt.Key_E, Qt.ControlModifier)
        self.settle()
        self.assertEqual(self.w.fx_panels, {})
        self.assertTrue(self.w.isVisible())
        self.assertEqual([r.docks for r in (self.w.row_in, self.w.row_play, self.w.row_out)],
                         [{}, {}, {}])


if __name__ == "__main__":
    unittest.main()
