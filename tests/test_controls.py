"""Wheel, typing, the matrix view and the effects panels, on the real widgets in an offscreen
window. Needs PySide6 and is skipped without it; the engine and the system are stubbed, and the
config lives in a temporary folder."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
    from PySide6.QtGui import QPalette, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QComboBox, QMenu
except ImportError:
    QApplication = None

ENGINE_STUBS = dict(status=lambda self: None, outdated=lambda self: False,
                    attach=lambda self: False, read_peaks=lambda self: (None, None),
                    write_matrix=lambda self, gains, out: None,
                    write_device=lambda self, block: None)
WINDOW_STUBS = dict(poll_hw=lambda self: None, poll_profile=lambda self: None,
                    ensure_engine=lambda self: None)


@unittest.skipUnless(QApplication, "needs PySide6")
class WindowCase(unittest.TestCase):
    """One main window per test class."""
    device = "digiface"

    @classmethod
    def setUpClass(cls):
        from openface_mixer import app, arc, config, engine, remote_link, theme
        from openface_mixer.devices import DEVICES
        cls.app = QApplication.instance() or QApplication([])
        theme.apply_theme(cls.app, 1.0)
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        home = Path(tmp.name)
        for patch in (mock.patch.object(config, "CONFIG_DIR", home / "config"),
                      mock.patch.object(config, "UI_FILE", home / "config" / "ui.json"),
                      mock.patch.object(remote_link, "AUTOSTART_DIR", home / "autostart"),
                      mock.patch.object(arc, "find_port", lambda: None),
                      mock.patch.multiple(engine.Engine, **ENGINE_STUBS),
                      mock.patch.multiple(app.MainWindow, **WINDOW_STUBS)):
            patch.start()
            cls.addClassCleanup(patch.stop)
        cls.w = app.MainWindow(DEVICES[cls.device])
        cls.addClassCleanup(cls.close_window)
        cls.w.resize(1280, 720)
        cls.w.show()
        cls.w.activateWindow()
        cls.settle()

    @classmethod
    def close_window(cls):
        cls.w.close()
        cls.w.deleteLater()
        cls.settle()

    @classmethod
    def settle(cls):
        for _ in range(4):
            cls.app.sendPostedEvents(None, QEvent.DeferredDelete)
            cls.app.processEvents()

    def wheel(self, widget, dx, dy, mods=None, pos=None):
        """A wheel event as the window system delivers it (so it travels up to a scroll area)."""
        if not hasattr(QTest, "wheelEvent"):
            self.skipTest("needs QTest.wheelEvent (Qt 6.8)")
        pos = widget.mapTo(widget.window(), widget.rect().center() if pos is None else pos)
        QTest.wheelEvent(widget.window().windowHandle(), QPointF(pos), QPoint(dx, dy), QPoint(),
                         mods or Qt.NoModifier)
        self.app.processEvents()

    def type_into(self, parent, text, key=None):
        """Put text in parent's open editor and press key (Enter if not given)."""
        from openface_mixer.widgets import LevelEdit
        editor = parent.findChild(LevelEdit)
        editor.setText(text)
        QTest.keyClick(editor, key or Qt.Key_Return)
        self.settle()


class WheelTest(WindowCase):
    def setUp(self):
        self.strip = self.w.row_in.strips[0]
        self.strip.fader.move_to(-10.0)
        self.strip.fader._wheel.rest = 0
        self.strip.pan.set_value(0.0)
        self.bar = self.w.row_in.scroll.horizontalScrollBar()
        self.bar.setValue(0)

    def test_sideways_scrolling_moves_the_row_not_the_control(self):
        self.assertGreater(self.bar.maximum(), 0)
        for control in (self.strip.fader, self.strip.pan):
            self.bar.setValue(0)
            self.wheel(control, -120, 0)
            self.assertGreater(self.bar.value(), 0)
            self.bar.setValue(0)
            self.wheel(control, -90, -20)           # a touchpad swipe is never quite level
            self.assertGreater(self.bar.value(), 0)
        self.assertAlmostEqual(self.strip.fader.db(), -10.0)
        self.assertEqual(self.strip.pan.value, 0.0)

    def test_a_notch_is_a_step(self):
        fader = self.strip.fader
        self.wheel(fader, 0, 120)
        self.assertAlmostEqual(fader.db(), -9.0)
        self.wheel(fader, 0, -120)
        self.wheel(fader, 0, -120, Qt.ControlModifier)
        self.assertAlmostEqual(fader.db(), -10.5)
        self.assertEqual(self.bar.value(), 0)
        self.wheel(self.strip.pan, 0, 120)
        self.assertEqual(self.strip.pan.value, 1 / 32)

    def test_touchpad_movement_adds_up_to_a_step(self):
        fader = self.strip.fader
        for _ in range(3):
            self.wheel(fader, 0, 30)
        self.assertAlmostEqual(fader.db(), -10.0)
        self.wheel(fader, 0, 30)
        self.assertAlmostEqual(fader.db(), -9.0)

    def test_direction_is_taken_as_delivered(self):
        # with natural scrolling the system has turned the delta round already: not again here
        fader = self.strip.fader
        pos = QPointF(fader.rect().center())
        e = QWheelEvent(pos, fader.mapToGlobal(pos), QPoint(), QPoint(0, -120), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, True)
        QApplication.sendEvent(fader, e)
        self.assertAlmostEqual(fader.db(), -11.0)


class TypeLevelTest(WindowCase):
    def setUp(self):
        self.strip = self.w.row_in.strips[0]
        self.strip.fader.move_to(-10.0)
        self.w.st["groups"].clear()
        for s in self.w.strips.values():
            s.set_group(0)

    def send(self, strip):
        return self.w.st["sends"][strip.kind][strip.channels[0]][self.w.st["selected"]][0]

    def open_editor(self, strip):
        from openface_mixer.widgets import LevelEdit
        QTest.mouseDClick(strip, Qt.LeftButton, Qt.NoModifier, strip.value.geometry().center())
        return strip.findChild(LevelEdit)

    def test_double_click_on_the_readout_types_a_level(self):
        from openface_mixer.widgets import LevelEdit
        s = self.strip
        editor = self.open_editor(s)
        self.assertTrue(s.rect().contains(editor.geometry()))
        self.assertTrue(editor.hasFocus())
        self.assertEqual(editor.selectedText(), "-10.0")
        QTest.keyClicks(editor, "-12.5")
        QTest.keyClick(editor, Qt.Key_Return)
        self.settle()
        self.assertEqual(self.send(s), -12.5)
        self.assertEqual(s.value.text(), "-12.5")
        self.assertAlmostEqual(s.fader.db(), -12.5)
        self.assertTrue(s.fader.hasFocus())
        self.assertEqual(s.findChildren(LevelEdit), [])
        self.assertTrue(self.w._save_timer.isActive())

    def test_double_click_on_the_fader_still_means_0_db(self):
        from openface_mixer.widgets import LevelEdit
        QTest.mouseDClick(self.strip.fader, Qt.LeftButton)
        self.assertEqual(self.send(self.strip), 0.0)
        self.assertEqual(self.strip.findChildren(LevelEdit), [])

    def test_what_can_be_typed(self):
        s = self.strip
        for text, want in (("+3", 3.0), ("-12", -12.0), ("-7,5", -7.5), ("99", 6.0),
                           ("-inf", None), ("-80", -80.0), ("-200", None), ("off", None)):
            s.fader.move_to(-10.0)
            QTest.keyClick(s.fader, Qt.Key_Return)      # the keyboard way in
            self.type_into(s, text)
            self.assertEqual(self.send(s), want, text)

    def test_not_a_number_keeps_the_editor_open(self):
        from openface_mixer.widgets import LevelEdit
        s = self.strip
        editor = self.open_editor(s)
        self.type_into(s, "loud")
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.selectedText(), "loud")
        self.assertEqual(self.send(s), -10.0)
        QTest.keyClick(editor, Qt.Key_Escape)
        self.settle()
        self.assertEqual(s.findChildren(LevelEdit), [])

    def test_esc_cancels_and_is_not_the_window_shortcut(self):
        from openface_mixer.widgets import LevelEdit
        s = self.strip
        self.w.store_btn.setChecked(True)
        self.addCleanup(self.w.store_btn.setChecked, False)
        self.open_editor(s)
        self.type_into(s, "-30", Qt.Key_Escape)
        self.assertEqual(self.send(s), -10.0)
        self.assertEqual(s.findChildren(LevelEdit), [])
        self.assertTrue(self.w.store_btn.isChecked())
        self.assertTrue(s.fader.hasFocus())

    def test_clicking_elsewhere_cancels(self):
        from openface_mixer.widgets import LevelEdit
        s = self.strip
        self.open_editor(s).setText("-30")
        QTest.mouseClick(self.w.row_in.strips[1].fader, Qt.LeftButton)
        self.settle()
        self.assertEqual(self.send(s), -10.0)
        self.assertEqual(s.findChildren(LevelEdit), [])

    def test_a_typed_level_moves_the_fader_group(self):
        s, other = self.strip, self.w.row_in.strips[1]
        other.fader.move_to(-20.0)
        self.w.set_group(s, 1)
        self.w.set_group(other, 1)
        self.open_editor(s)
        self.type_into(s, "-4")
        self.assertEqual(self.send(other), -14.0)
        self.assertEqual(other.value.text(), "-14.0")

    def test_a_stereo_strip_sets_both_channels(self):
        s = next(s for s in self.w.row_play.strips if len(s.channels) == 2)
        self.open_editor(s)
        self.type_into(s, "-9")
        sends = self.w.st["sends"]["play"]
        self.assertEqual([sends[c][self.w.st["selected"]][0] for c in s.channels], [-9.0, -9.0])

    def test_output_fader(self):
        s = self.w.row_out.strips[0]
        self.open_editor(s)
        self.type_into(s, "-15")
        self.assertEqual(self.w.st["out"][s.pair]["gain"], -15.0)
        self.open_editor(s)
        self.type_into(s, "off")
        self.assertIsNone(self.w.st["out"][s.pair]["gain"])


class MatrixTest(WindowCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.w.tabs.setCurrentIndex(1)
        cls.settle()

    def setUp(self):
        self.m = self.w.matrix
        for kind in ("in", "play"):
            for sends in self.w.st["sends"][kind]:
                for send in sends:
                    send[0], send[1] = None, 0.0
        self.m.sel = self.m.hover = None
        self.hbar = self.m.scroll.horizontalScrollBar()
        self.vbar = self.m.scroll.verticalScrollBar()
        self.hbar.setValue(0)
        self.vbar.setValue(0)

    def at(self, ci, ri):
        return QPoint(self.m._x(ci) + self.m.CW // 2, self.m.HH + ri * self.m.CH + self.m.CH // 2)

    def click(self, ci, ri, button=None):
        QTest.mouseClick(self.m, button or Qt.LeftButton, Qt.NoModifier, self.at(ci, ri))

    def test_click_turns_an_empty_cell_on_and_only_selects_a_set_one(self):
        m = self.m
        self.click(3, 2)
        self.assertEqual(m._gain(3, 2), 0.0)
        self.assertEqual(m.sel, (3, 2))
        self.assertTrue(m.hasFocus())
        m._set(3, 2, -18.0)
        self.click(4, 2)
        self.click(3, 2)
        self.assertEqual(m._gain(3, 2), -18.0)
        self.assertEqual(m.sel, (3, 2))
        self.click(3, 2, Qt.RightButton)
        self.assertEqual(m._gain(3, 2), -18.0)

    def test_double_click_and_delete_clear(self):
        m = self.m
        m._set(3, 2, -18.0)
        QTest.mouseDClick(m, Qt.LeftButton, Qt.NoModifier, self.at(3, 2))
        self.assertIsNone(m._gain(3, 2))
        m._set(5, 1, -6.0)
        self.click(5, 1)
        QTest.keyClick(m, Qt.Key_Delete)
        self.assertIsNone(m._gain(5, 1))

    def test_drag_sets_the_level(self):
        m = self.m
        m._set(3, 2, -18.0)
        p = self.at(3, 2)
        QTest.mousePress(m, Qt.LeftButton, Qt.NoModifier, p)
        QTest.mouseMove(m, p + QPoint(0, -2))            # a click with an unsteady hand
        self.assertEqual(m._gain(3, 2), -18.0)
        QTest.mouseMove(m, p + QPoint(0, -6))
        QTest.mouseMove(m, p + QPoint(0, -30))
        up = m._gain(3, 2)
        self.assertGreater(up, -18.0)
        self.assertEqual(up, round(up, 1))
        QTest.mouseMove(m, p + QPoint(0, -900))
        self.assertEqual(m._gain(3, 2), 6.0)
        QTest.mouseMove(m, p + QPoint(0, 10))
        self.assertLess(m._gain(3, 2), -18.0)
        QTest.mouseRelease(m, Qt.LeftButton, Qt.NoModifier, p + QPoint(0, 10))
        down = m._gain(3, 2)
        QTest.mouseMove(m, p + QPoint(0, 60))
        self.assertEqual(m._gain(3, 2), down)

    def test_wheel_scrolls_the_grid_and_changes_a_level_only_with_ctrl(self):
        m = self.m
        m._set(3, 2, -18.0)
        self.assertGreater(self.hbar.maximum(), 0)
        self.wheel(m, -120, 0, pos=self.at(3, 2))
        self.assertGreater(self.hbar.value(), 0)
        self.hbar.setValue(0)
        self.wheel(m, 0, -120, pos=self.at(3, 2))
        self.wheel(m, 0, 120, pos=self.at(3, 2))
        self.assertEqual(m._gain(3, 2), -18.0)
        self.wheel(m, 0, 120, Qt.ControlModifier, pos=self.at(3, 2))
        self.assertEqual(m._gain(3, 2), -17.0)
        for _ in range(4):
            self.wheel(m, 0, -30, Qt.ControlModifier, pos=self.at(3, 2))
        self.assertEqual(m._gain(3, 2), -18.0)
        self.assertEqual(self.hbar.value(), 0)
        self.wheel(m, -120, 0, Qt.ControlModifier, pos=self.at(3, 2))    # not a page sideways
        self.assertEqual((m._gain(3, 2), self.hbar.value()), (-18.0, 0))

    def test_wheel_up_on_an_empty_cell_makes_no_send(self):
        self.wheel(self.m, 0, 120, pos=self.at(3, 2))
        self.wheel(self.m, 0, 120, Qt.ControlModifier, pos=self.at(3, 2))
        self.assertIsNone(self.m._gain(3, 2))

    def test_keys_move_the_selection_and_type_a_level(self):
        from openface_mixer.widgets import LevelEdit
        m = self.m
        self.click(3, 2)
        for key in (Qt.Key_Right, Qt.Key_Down, Qt.Key_Down, Qt.Key_Left, Qt.Key_Left, Qt.Key_Up):
            QTest.keyClick(m, key)
        self.assertEqual(m.sel, (2, 3))
        QTest.keyClick(m, Qt.Key_Return)
        self.assertEqual(m.findChild(LevelEdit).geometry(), m._rect(2, 3).toRect())
        self.type_into(m, "-9")
        self.assertEqual(m._gain(2, 3), -9.0)
        self.assertTrue(m.hasFocus())
        QTest.keyClick(m, Qt.Key_Return)
        self.type_into(m, "off")
        self.assertIsNone(m._gain(2, 3))

    def test_typing_brings_the_selection_back_into_view(self):
        from openface_mixer.widgets import LevelEdit
        m = self.m
        self.click(1, 0)
        self.hbar.setValue(self.hbar.maximum())
        QTest.keyClick(m, Qt.Key_Return)
        editor = m.findChild(LevelEdit)
        self.assertTrue(m.visibleRegion().boundingRect().contains(editor.geometry()))
        self.assertGreaterEqual(editor.x() - m._origin()[0], m.LW)
        self.type_into(m, "-9")
        self.assertEqual(m._gain(1, 0), -9.0)

    def test_the_selection_stays_clear_of_the_names(self):
        m = self.m
        self.click(0, 0)
        for _ in range(len(m.cols)):
            QTest.keyClick(m, Qt.Key_Right)
        self.assertEqual(m.sel, (len(m.cols) - 1, 0))
        self.assertGreater(self.hbar.value(), 0)
        for _ in range(len(m.cols) - 3):
            QTest.keyClick(m, Qt.Key_Left)
        self.assertEqual(m.sel, (2, 0))
        self.assertGreaterEqual(m._rect(2, 0).left() - m._origin()[0], m.LW)

    def test_names_stay_in_view_while_the_grid_scrolls(self):
        m, view = self.m, self.m.scroll.viewport()
        m.scroll.setMaximumHeight(m.HH + 6 * m.CH)       # so the grid scrolls both ways
        self.addCleanup(m.scroll.setMaximumHeight, 16777215)
        self.settle()
        self.assertGreater(self.vbar.maximum(), 0)
        m._set(0, 0, -6.0)
        first = self.at(0, 0)

        def names():
            image = view.grab().toImage()
            return (image.copy(m.LW, 0, view.width() - m.LW, m.HH - 2),
                    image.copy(0, m.HH, m.LW - 2, view.height() - m.HH))

        cols, rows = names()
        self.vbar.setValue(3 * m.CH)
        self.settle()
        self.assertEqual(names()[0], cols)               # the column names did not move
        self.assertNotEqual(names()[1], rows)
        self.assertEqual(m._origin(), (0, 3 * m.CH))
        self.assertIsNone(m._cell(first))                # that cell is under the names now
        self.vbar.setValue(0)
        self.hbar.setValue(5 * m.CW)
        self.settle()
        self.assertEqual(names()[1], rows)               # nor the row names
        self.assertNotEqual(names()[0], cols)
        self.assertIsNone(m._cell(first))
        QTest.mouseClick(m, Qt.LeftButton, Qt.NoModifier, first)
        self.assertEqual(m._gain(0, 0), -6.0)
        self.assertEqual(m._cell(first + QPoint(5 * m.CW, 0)), (5, 0))

    def test_names_are_redrawn_on_screen_after_a_scroll(self):
        # the window's own pixels, not a fresh grab(); with a solid background Qt moves the old
        # pixels along and the view has to redraw the names itself
        m, view = self.m, self.m.scroll.viewport()
        plain = m.palette()
        solid = QPalette(plain)
        solid.setColor(QPalette.Window, Qt.black)
        m.setPalette(solid)
        m.setAutoFillBackground(True)
        self.addCleanup(m.setAutoFillBackground, False)
        self.addCleanup(m.setPalette, plain)
        self.settle()
        corner = view.mapTo(self.w, QPoint(0, 0))

        def row_names():
            image = self.w.screen().grabWindow(self.w.winId()).toImage()
            return image.copy(corner.x(), corner.y() + m.HH, m.LW - 2, 6 * m.CH)

        before = row_names()
        self.hbar.setValue(37)
        self.settle()
        self.assertEqual(row_names(), before)

    def test_a_mono_source_shows_its_pan(self):
        m = self.m
        mono = next(i for i, col in enumerate(m.cols) if len(col[1]) == 1)
        stereo = next(i for i, col in enumerate(m.cols) if len(col[1]) == 2)

        def picture(ci, pan):
            kind, chans, _, _ = m.cols[ci]
            for c in chans:
                self.w.st["sends"][kind][c][m.rows[0][0]][:] = [-6.0, pan]
            return m.grab(m._rect(ci, 0).toRect()).toImage()

        self.assertNotEqual(picture(mono, -1.0), picture(mono, 1.0))
        self.assertNotEqual(picture(mono, 0.0), picture(mono, 1.0))
        self.assertEqual(picture(stereo, -1.0), picture(stereo, 1.0))


class MenuTest(WindowCase):
    def test_right_click_menus_are_deleted_after_use(self):
        w = self.w
        before = len(w.findChildren(QMenu))
        strip = w.row_in.strips[0]
        for show in (lambda: w.strip_menu(strip, strip.mapToGlobal(QPoint(5, 5))),
                     lambda: w.workspace_menu(QPoint(50, 50)), lambda: w.slot_menu(0)):
            for _ in range(3):
                QTimer.singleShot(0, lambda: QApplication.activePopupWidget().close())
                show()
        for _ in range(3):
            w._fill_presets_menu()      # the narrow window's Presets menu and its Store submenu
        self.settle()
        self.assertLessEqual(len(w.findChildren(QMenu)), before + 1)


class EffectsTest(WindowCase):
    device = "ucx2"

    def panel(self, which, strip):
        self.w.open_fx_panel(strip, which)
        self.settle()
        panel = self.w.fx_panels[(which, strip.key)]
        self.addCleanup(self.close, panel)
        return panel

    def close(self, panel):
        if panel in self.w.fx_panels.values():
            panel.close()
            self.settle()

    def test_esc_closes_a_panel_and_is_not_the_window_shortcut(self):
        from openface_mixer.fx_panels import ValueKnob
        strip = self.w.row_in.strips[0]
        self.w.store_btn.setChecked(True)
        self.addCleanup(self.w.store_btn.setChecked, False)
        for which, target in (("eq", ValueKnob), ("dyn", ValueKnob), ("eq", QComboBox)):
            panel = self.panel(which, strip)
            panel.activateWindow()
            child = panel.findChildren(target)[0]
            child.setEnabled(True)
            child.setFocus()
            QTest.keyClick(child, Qt.Key_Escape)
            self.settle()
            self.assertNotIn((which, strip.key), self.w.fx_panels)
        self.assertTrue(self.w.store_btn.isChecked())

    def test_ctrl_w_closes_a_panel(self):
        strip = self.w.row_out.strips[0]
        for which in ("eq", "dyn", "room"):
            panel = self.panel(which, strip)
            panel.activateWindow()
            panel.setFocus()
            QTest.keyClick(panel, Qt.Key_W, Qt.ControlModifier)
            self.settle()
            self.assertNotIn((which, strip.key), self.w.fx_panels)
        self.w.fx_btn.setChecked(True)
        self.settle()
        QTest.keyClick(self.w.fx_panels[("unit", "")], Qt.Key_W, Qt.ControlModifier)
        self.settle()
        self.assertNotIn(("unit", ""), self.w.fx_panels)
        self.assertFalse(self.w.fx_btn.isChecked())

    def test_esc_in_a_typed_value_leaves_the_panel_open(self):
        from openface_mixer.fx_panels import ValueKnob
        strip = self.w.row_in.strips[0]
        panel = self.panel("eq", strip)
        panel.switches["eq"].setChecked(True)
        knob = next(k for k in panel.findChildren(ValueKnob) if k.name == "Band 2 gain")
        knob.setFocus()
        QTest.keyClick(knob, Qt.Key_Return)
        self.type_into(knob, "3", Qt.Key_Escape)
        self.assertIn(("eq", strip.key), self.w.fx_panels)
        self.assertEqual(knob.value, 0.0)
        QTest.keyClick(knob, Qt.Key_Return)
        self.type_into(knob, "4,5")
        self.assertEqual(self.w.st["hw"]["fx"]["in"][strip.channels[0]]["eq_g2"], 4.5)
        QTest.keyClick(knob, Qt.Key_Return)
        self.type_into(knob, "off")                 # only a level has an off position
        self.assertEqual(knob.value, 4.5)
        self.type_into(knob, "", Qt.Key_Escape)

    def test_knobs_and_lists_have_names_of_their_own(self):
        from openface_mixer.fx_panels import ValueKnob
        strip, out = self.w.row_in.strips[0], self.w.row_out.strips[0]
        self.w.fx_btn.setChecked(True)
        self.addCleanup(self.w.fx_btn.setChecked, False)
        panels = [self.panel("eq", strip), self.panel("dyn", strip), self.panel("room", out),
                  self.w.fx_panels[("unit", "")]]
        for panel in panels:
            names = [k.name for k in panel.findChildren(ValueKnob)] + \
                    [c.accessibleName() for c in panel.findChildren(QComboBox)]
            self.assertEqual(len(names), len(set(names)), names)
            self.assertNotIn("", names)
        eq, dyn, room, unit = ([k.name for k in p.findChildren(ValueKnob)] for p in panels)
        self.assertIn("Band 2 gain", eq)
        self.assertIn("Compressor ratio", dyn)
        self.assertIn("Expander ratio", dyn)
        self.assertIn("Band 9 Q", room)
        self.assertIn("Echo width", unit)
        knob = next(k for k in panels[0].findChildren(ValueKnob) if k.name == "Band 2 gain")
        knob.set_value(-3.0)
        self.assertEqual(knob.accessibleName(), "Band 2 gain -3.0 dB")
        rise = next(k for k in panels[1].findChildren(ValueKnob) if k.label == "Rise Time")
        self.assertTrue(rise.accessibleName().endswith(" 3.0s"), rise.accessibleName())

    def test_strip_buttons_name_their_channel(self):
        s = self.w.row_in.strips[0]
        self.assertEqual((s.eq_btn.accessibleName(), s.dyn_btn.accessibleName(),
                          s.settings_btn.accessibleName()),
                         (f"{s.name} EQ", f"{s.name} dynamics", f"{s.name} settings"))

    def test_readouts_show_what_is_sent(self):
        from openface_mixer import fx
        from openface_mixer.devices import DEVICES
        from openface_mixer.fx_panels import FxUnitPanel, ValueKnob
        room = self.panel("room", self.w.row_out.strips[0])
        vol = next(k for k in room.findChildren(ValueKnob) if k.label == "Vol. Cal.")
        for value, shown in ((0.05, "+0.05"), (-0.15, "-0.15"), (0.0, "0.00"), (3.0, "+3.00")):
            vol.set_value(value)
            self.assertEqual(vol.fmt(vol.value), shown)
        for key, value, shown in (("ucx2", 0.301, "0.301s"), ("ff802", 0.31, "0.31s")):
            model = DEVICES[key].fx
            unit = FxUnitPanel(fx.default_settings(model), model)
            self.addCleanup(unit.deleteLater)
            delay = unit.knobs[("echo", "delay")]
            delay.set_value(value, True)
            self.assertEqual(delay.fmt(delay.value), shown)
            scale = next(s for k, s, *_ in model.params(fx.ECHO) if k == "delay")
            self.assertEqual(round(float(shown[:-1]) * scale),
                             fx.raw_value(unit.fx["echo"]["delay"], scale, 0.0, 2.0))
            delay.nudge(1, True)                           # one fine step is one unit on the wire
            self.assertNotEqual(delay.fmt(delay.value), shown)

    def test_typing_on_fx_send_in_the_channel_settings_popup(self):
        from openface_mixer.channel_settings import ChannelSettings
        from openface_mixer.fx_panels import LevelKnob
        from openface_mixer.widgets import LevelEdit
        strip = self.w.row_in.strips[0]
        sends = self.w.st["hw"]["fx"]["send"]["in"]
        self.w.channel_settings(strip, strip.mapToGlobal(QPoint(10, 30)))
        self.settle()
        popup = self.w.findChild(ChannelSettings)
        self.addCleanup(popup.close)
        knob = popup.findChild(LevelKnob)
        QTest.mouseClick(popup.windowHandle(), Qt.RightButton, Qt.NoModifier,
                         knob.mapTo(popup, QPoint(knob.width() // 2, knob.height() - 8)))
        self.settle()
        editor = knob.findChild(LevelEdit)
        self.assertIsNotNone(editor)
        self.assertTrue(knob.rect().contains(editor.geometry()))
        self.assertTrue(popup.isVisible())
        self.assertIs(QApplication.activePopupWidget(), popup)      # no menu, no dialog
        QTest.keyClicks(editor, "-20")
        QTest.keyClick(editor, Qt.Key_Return)
        self.settle()
        self.assertEqual(sends[strip.channels[0]], -20.0)
        self.assertTrue(popup.isVisible())
        QTest.keyClick(knob, Qt.Key_Return)
        self.type_into(knob, "-3", Qt.Key_Escape)
        self.assertEqual(sends[strip.channels[0]], -20.0)
        self.assertTrue(popup.isVisible())
        QTest.keyClick(knob, Qt.Key_Return)
        self.type_into(knob, "off")
        self.assertIsNone(sends[strip.channels[0]])

    def test_wheel_on_an_fx_knob(self):
        from openface_mixer.fx_panels import ValueKnob
        panel = self.panel("eq", self.w.row_in.strips[0])
        panel.switches["eq"].setChecked(True)
        knob = next(k for k in panel.findChildren(ValueKnob) if k.name == "Band 2 gain")
        knob.set_value(0.0, True)
        self.wheel(knob, 120, 0)
        self.assertEqual(knob.value, 0.0)
        self.wheel(knob, 0, 120)
        up = knob.value
        self.assertGreater(up, 0.0)
        for _ in range(3):
            self.wheel(knob, 0, -30)
        self.assertEqual(knob.value, up)
        self.wheel(knob, 0, -30)
        self.assertEqual(knob.value, 0.0)


if __name__ == "__main__":
    unittest.main()
