import unittest

from openface_mixer import model as m
from openface_mixer.history import History


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.st = m.default_state()
        self.h = History(self.st)

    def test_nothing_to_undo(self):
        self.assertFalse(self.h.can_undo())
        self.assertIsNone(self.h.undo())
        self.assertIsNone(self.h.redo())

    def test_edits_between_commits_are_one_step(self):
        for db in (-10.0, -8.0, -6.0):          # one fader drag
            self.st["out"][0]["gain"] = db
        self.assertTrue(self.h.commit())
        self.assertFalse(self.h.commit())       # nothing new
        self.assertEqual(self.h.undo(), "Output level")
        self.assertEqual(self.st["out"][0]["gain"], 0.0)
        self.assertEqual(self.h.redo(), "Output level")
        self.assertEqual(self.st["out"][0]["gain"], -6.0)

    def test_undo_commits_pending_edits_first(self):
        self.st["mute"]["in"][3] = True
        self.assertTrue(self.h.can_undo())
        self.assertEqual(self.h.undo(), "Mute")
        self.assertFalse(self.st["mute"]["in"][3])

    def test_new_edit_clears_redo(self):
        self.st["names"]["in:0"] = "Vocal"
        self.h.commit()
        self.h.undo()
        self.assertTrue(self.h.can_redo())
        self.st["colors"]["in:0"] = "#ff2a4f"
        self.h.commit()
        self.assertFalse(self.h.can_redo())
        self.assertEqual(self.st["names"], {})

    def test_explicit_label_and_untracked_keys(self):
        self.st["selected"] = 2                 # navigation, not an edit
        self.st["solo"]["in"][0] = True
        self.assertFalse(self.h.commit())
        self.st["sends"]["in"][0][0] = [0.0, 0.0]
        self.h.commit("Reset mix")
        self.assertEqual(self.h.undo(), "Reset mix")

    def test_restored_state_is_a_copy(self):
        self.st["groups"]["in:0"] = 1
        self.h.commit()
        self.h.undo()
        self.st["groups"]["in:2"] = 2
        self.h.commit()
        self.h.undo()
        self.assertEqual(self.st["groups"], {})

    def test_limit(self):
        h = History(self.st, limit=3)
        for i in range(5):
            self.st["out"][0]["gain"] = -float(i + 1)
            h.commit()
        self.assertEqual(len(h.undo_stack), 3)


if __name__ == "__main__":
    unittest.main()
