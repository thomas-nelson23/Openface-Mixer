"""The Control room section of the settings, on the real window offscreen: several Phones,
where Talkback goes, the Dim and Recall amounts, and the ARC's two Phones keys."""
import unittest

from openface_mixer import control_room, model as m
from tests.test_controls import QApplication, WindowCase

if QApplication:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QScrollArea


class ControlRoomTest(WindowCase):
    def setUp(self):
        self.st = self.w.st
        self.panel = self.w.control
        self.cr = control_room.control_room(self.st)
        self.cr.update(control_room.default_control_room(16), talkback_src=7)
        self.st["out"][0]["gain"] = 0.0
        self.st["groups"].clear()
        self.w.rebuild_all()
        self.w.push_matrix()

    def menu(self, key):
        """The filled menu of a row that takes several pairs, as {name: action}."""
        choice = self.panel.choices[key]
        choice.menu.aboutToShow.emit()
        return {a.text(): a for a in choice.menu.actions()}

    def talkback_pairs(self):
        g, _out = control_room.apply(self.st, m.compute_matrix(self.st),
                                     m.compute_out_gains(self.st))
        return [p for p in range(m.N_PAIRS) if g[2 * p * m.N_SRC + 7]]

    def test_talkback_goes_to_the_phones_chosen(self):
        self.panel.buttons["talkback"].click()
        self.assertEqual(self.talkback_pairs(), [16])
        self.menu("phones")["AD1 3/4"].trigger()
        self.menu("phones")["Phones"].trigger()            # no longer the Phones
        self.assertEqual(self.cr["phones"], [1])
        self.assertEqual(self.talkback_pairs(), [1])
        self.assertEqual(self.panel.choices["phones"].currentText(), "AD1 3/4")
        self.assertEqual(self.panel.choices["talkback_dests"].currentText(), "AD1 3/4")

    def test_up_to_four_phones(self):
        for name in ("AD1 3/4", "AD1 5/6", "AD1 7/8"):
            self.menu("phones")[name].trigger()
        self.assertEqual(self.cr["phones"], [16, 1, 2, 3])      # in the order they were ticked
        self.assertEqual(self.panel.choices["phones"].currentText(), "Phones +3")
        menu = self.menu("phones")
        self.assertEqual([n for n, a in menu.items() if a.isChecked()],
                         ["AD1 3/4", "AD1 5/6", "AD1 7/8", "Phones"])
        self.assertEqual([n for n, a in menu.items() if a.isEnabled()],
                         ["AD1 3/4", "AD1 5/6", "AD1 7/8", "Phones"])      # full: untick first
        for a in menu.values():
            a.trigger()
        self.assertEqual(self.cr["phones"], [])
        self.assertEqual(self.panel.choices["phones"].currentText(), "None")
        self.assertTrue(all(a.isEnabled() for a in self.menu("phones").values()))

    def test_talkback_to_further_outputs(self):
        self.cr["talkback"] = True
        menu = self.menu("talkback_dests")
        self.assertEqual([n for n, a in menu.items() if a.isChecked()], ["Phones"])
        self.assertFalse(menu["Phones"].isEnabled())         # the Phones always get it
        menu["AD2 1/2"].trigger()
        self.assertEqual(self.cr["talkback_dests"], [4])
        self.assertEqual(self.talkback_pairs(), [4, 16])
        self.assertEqual(self.panel.choices["talkback_dests"].currentText(), "Phones +1")
        self.menu("talkback_dests")["AD2 1/2"].trigger()
        self.assertEqual(self.cr["talkback_dests"], [])

    def test_the_menu_opens_without_waiting(self):
        choice = self.panel.choices["phones"]
        choice.showPopup()
        self.assertTrue(choice.menu.isVisible())
        self.assertEqual(len(choice.menu.actions()), len(self.w.out_pairs(all_pairs=True)))
        choice.menu.close()

    def test_dim_and_talkback_dim_amounts(self):
        pushed = []
        self.w.engine.write_matrix = lambda gains, out: pushed.append((list(gains), list(out)))
        self.addCleanup(lambda: vars(self.w.engine).pop("write_matrix"))
        self.panel.buttons["dim"].click()
        self.assertAlmostEqual(pushed[-1][1][0], 0.1, places=6)
        self.panel.amounts["dim_db"].setValue(-6.0)
        self.assertEqual(self.cr["dim_db"], -6.0)
        self.assertAlmostEqual(pushed[-1][1][0], m.db2lin(-6.0), places=6)
        self.panel.buttons["talkback"].click()
        self.panel.amounts["talkback_dim_db"].setValue(-12.0)
        self.assertEqual(self.cr["talkback_dim_db"], -12.0)
        self.assertAlmostEqual(pushed[-1][0][32 * m.N_SRC + m.N_IN + 32], m.db2lin(-12.0),
                               places=6)
        for key in ("dim_db", "talkback_dim_db"):
            self.assertEqual(self.panel.amounts[key].maximum(), 0.0)    # they only turn down

    def test_a_typed_amount_counts_when_enter_is_pressed(self):
        """Not digit by digit: "-25" must not pass through -2 dB while Dim is on."""
        spin = self.panel.amounts["dim_db"]
        spin.setFocus()
        spin.selectAll()
        QTest.keyClicks(spin, "-2")
        self.assertEqual(self.cr["dim_db"], -20.0)
        QTest.keyClicks(spin, "5")
        QTest.keyClick(spin, Qt.Key_Return)
        self.assertEqual(self.cr["dim_db"], -25.0)

    def test_a_comma_and_a_point_both_mean_the_decimal_mark(self):
        """Whatever the system's language: "2.5" must never become 25 dB (so +6) for Recall."""
        from PySide6.QtCore import QLocale
        from openface_mixer.control_panel import Amount
        was = QLocale()
        try:
            for name in ("en_US", "de_DE"):
                QLocale.setDefault(QLocale(name))
                spin = Amount(-65.0, 6.0, 0.5)
                for typed, db in (("-7,5", -7.5), ("-7.5", -7.5), ("2,5", 2.5), ("2.5", 2.5),
                                  ("-12", -12.0), ("3 dB", 3.0), ("-90", -65.0), ("40", 6.0)):
                    spin.setValue(-30.0)
                    spin.lineEdit().setText(typed)
                    spin.interpretText()
                    self.assertEqual(spin.value(), db, (name, typed))
                spin.lineEdit().setText("loud")             # not a level: nothing changes
                spin.interpretText()
                self.assertEqual(spin.value(), 6.0, name)
                spin.deleteLater()
        finally:
            QLocale.setDefault(was)

    def test_the_wheel_scrolls_the_settings_over_an_amount(self):
        spin = self.panel.amounts["dim_db"]
        scroll = self.w.sidebar.findChild(QScrollArea)
        scroll.ensureWidgetVisible(spin)
        at = scroll.verticalScrollBar().value()
        self.wheel(spin, 0, -120)
        self.assertEqual((spin.value(), self.cr["dim_db"]), (-20.0, -20.0))
        self.assertGreater(scroll.verticalScrollBar().value(), at)
        scroll.ensureWidgetVisible(spin)
        self.w.activateWindow()         # offscreen, an earlier test's menu may have taken it
        self.settle()
        QTest.mouseClick(spin, Qt.LeftButton)               # clicked first: now it is meant
        self.assertTrue(spin.hasFocus())
        self.wheel(spin, 0, 120)
        self.assertEqual(self.cr["dim_db"], -19.0)

    def test_recall_sets_main_out_and_its_group_follows(self):
        self.panel.amounts["recall_db"].setValue(-12.5)
        self.assertEqual(self.cr["recall_db"], -12.5)
        self.st["groups"].update({"out:0": 1, "out:1": 1})
        self.w.rebuild_all()
        recall = next(b for b in self.panel.findChildren(type(self.panel.buttons["dim"]))
                      if b.text() == "Recall")
        recall.click()
        self.assertEqual((self.st["out"][0]["gain"], self.st["out"][1]["gain"]), (-12.5, -12.5))
        self.assertAlmostEqual(self.w.strips["out:0"].fader.db(), -12.5)
        self.assertAlmostEqual(self.w.strips["out:1"].fader.db(), -12.5)
        self.cr["main"] = 2                                 # and with Main Out hidden
        self.st["groups"]["out:2"] = 1
        self.st["hidden_strips"].append("out:2")
        self.w.rebuild_all()
        recall.click()
        self.assertEqual(self.st["out"][2]["gain"], -12.5)
        self.assertEqual(self.st["out"][0]["gain"], -25.0)  # its group follows all the same
        self.assertAlmostEqual(self.w.strips["out:0"].fader.db(), -25.0)
        self.st["hidden_strips"].remove("out:2")
        for p in range(3):
            self.st["out"][p]["gain"] = 0.0

    def test_the_arcs_phones_keys_pick_the_first_and_second_phones(self):
        self.cr["phones"] = [3, 16]
        phones1, phones2 = 9, 10                            # the keys printed Phones 1 and 2
        self.w.arc_key(phones2, True)
        self.assertEqual(self.cr["encoder"], "phones2")
        self.w.arc_encoder(-4)
        self.assertEqual((self.st["out"][16]["gain"], self.st["out"][3]["gain"]), (-2.0, 0.0))
        self.w.arc_key(phones1, True)
        self.w.arc_encoder(-2)
        self.assertEqual(self.st["out"][3]["gain"], -1.0)
        self.w.arc_key(phones1, True)                       # the lit key again: Main Out
        self.assertEqual(self.cr["encoder"], "main")
        self.st["out"][16]["gain"] = self.st["out"][3]["gain"] = 0.0

    def test_with_one_phones_pair_both_arc_keys_are_its_key(self):
        """As before there could be several: either key selects the Phones and lights both,
        and either takes the encoder back to Main Out."""
        from openface_mixer import arc
        phones1, phones2 = 9, 10

        def lit():
            return [arc.key_lit(self.st, arc.DEFAULT_KEYS[k], None) for k in (phones1, phones2)]

        for first, second in ((phones1, phones2), (phones2, phones1), (phones2, phones2)):
            self.w.arc_key(first, True)
            self.assertEqual(lit(), [True, True])
            self.assertEqual(control_room.encoder_pair(self.st), 16)
            self.w.arc_key(second, True)
            self.assertEqual((self.cr["encoder"], lit()), ("main", [False, False]))

    def test_any_arc_key_does_any_action_and_its_light_follows(self):
        import json
        from unittest import mock
        from openface_mixer import arc, config
        panel, w = self.panel, self.w
        lights = []
        self.addCleanup(panel._arc_assign, None)

        def assign(key, action):
            combo = panel.arc_combos[key]
            combo.setCurrentIndex(combo.findData(action))
            combo.activated.emit(combo.currentIndex())

        panel.arc_btn.click()
        self.assertTrue(all(row.isVisible() for row in panel.arc_rows))
        self.assertEqual([c.currentData() for c in panel.arc_combos], arc.DEFAULT_KEYS)
        with mock.patch.object(w.arc, "set_leds", lights.append):
            assign(0, "dim")                                # Key 1: Dim instead of preset 1
            self.assertEqual(w.arc_keys[0], "dim")
            self.assertEqual(json.loads(config.UI_FILE.read_text())["arc_keys"][:2],
                             ["dim", "snapshot:2"])
            self.assertFalse(lights[-1][0])
            w.arc_key(0, True)
            self.assertTrue(self.cr["dim"])
            self.assertTrue(lights[-1][0] and lights[-1][14])   # and the Dim key's own light
            w.arc_key(0, True)
            self.assertFalse(self.cr["dim"] or lights[-1][0])
            # the Mono key takes the encoder back to Main Out, and stays lit while it has it
            assign(8, "main")
            w.arc_key(9, True)                              # Phones 1
            self.assertEqual(self.cr["encoder"], "phones")
            self.assertEqual(lights[-1][8:10], [False, True])
            for _ in range(2):
                w.arc_key(8, True)
                self.assertEqual((self.cr["encoder"], self.cr["main"]), ("main", 0))
                self.assertEqual(lights[-1][8:10], [True, False])
            self.assertFalse(self.cr["mono"])
            # the second Phones on another key, with two Phones pairs
            self.cr["phones"] = [3, 16]
            assign(14, "phones2")
            w.arc_key(14, True)
            self.assertEqual(control_room.encoder_pair(self.st), 16)
            self.assertTrue(lights[-1][14] and lights[-1][10])
            w.arc_key(14, True)
            self.assertEqual(self.cr["encoder"], "main")
            # back to the printed layout, for this computer
            panel.arc_rows[-1].click()
            self.assertEqual(w.arc_keys, arc.DEFAULT_KEYS)
            self.assertEqual([c.currentData() for c in panel.arc_combos], arc.DEFAULT_KEYS)
            self.assertEqual(json.loads(config.UI_FILE.read_text())["arc_keys"],
                             arc.DEFAULT_KEYS)
        panel.arc_btn.click()
        self.assertFalse(any(row.isVisible() for row in panel.arc_rows))
        self.settle()                   # the settings laid out again without the list


if __name__ == "__main__":
    unittest.main()
