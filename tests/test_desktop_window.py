"""The strip menus' desktop outputs and inputs, on the real window offscreen
(tests/test_controls.py WindowCase: the engine and the system are stubbed, the config is in a
temporary folder)."""
import unittest
from unittest import mock

from tests.test_controls import QApplication, WindowCase

if QApplication:
    from PySide6.QtWidgets import QMenu


def menu_running(run):
    """A QMenu whose exec() calls run(menu) instead of showing it."""
    class Menu(QMenu):
        def exec(self, *_args):
            run(self)
    return Menu


class DesktopMenuTest(WindowCase):
    def setUp(self):
        from openface_mixer import config, sinks
        self.sinks, self.config = sinks, config
        self.w.st["sinks"] = sinks.default_list(self.w.dev)
        self.w.save()
        self.reload = mock.patch.object(self.w.engine, "reload_sinks").start()
        self.addCleanup(mock.patch.stopall)

    def strip(self, row, ch):
        return next(s for s in row.strips if ch in s.channels)

    def menu(self, strip, pick=None, answer=("Music", True)):
        """The texts of the strip menu's actions; triggers the one starting with pick."""
        from openface_mixer import app
        texts = []

        def run(menu):
            actions = [a for a in menu.actions() if not a.isSeparator() and not a.menu()]
            texts.extend(a.text() for a in actions)
            if pick:
                next(a for a in actions if a.text().startswith(pick)).trigger()

        with mock.patch.object(app, "QMenu", menu_running(run)), \
                mock.patch.object(app.QInputDialog, "getText", return_value=answer) as ask:
            self.w.strip_menu(strip, None)
        self.settle()
        self.asked = ask
        return [t for t in texts if "desktop" in t]

    def conf(self):
        return self.config.sinks_file(self.w.dev).read_text().splitlines()[1:]

    def test_the_playback_sink_and_another(self):
        w = self.w
        first = self.strip(w.row_play, 1)
        self.assertEqual(self.menu(first), ["Rename desktop output “Openface Mixer Playback”…",
                                            "Remove desktop output “Openface Mixer Playback”"])
        third = self.strip(w.row_play, 3)        # mono or stereo, the sink takes the pair
        self.assertEqual(self.menu(third), ["Add desktop output…"])
        self.menu(third, "Add desktop output")
        self.assertEqual(self.asked.call_args.kwargs["text"], "Playback AD1 3/4")
        self.assertEqual(w.st["sinks"][-1], {"kind": "sink", "ch": 2, "stereo": True,
                                             "name": "Music"})
        self.assertEqual(self.conf()[-1], "sink openface_mixer_play_3_4 2 3 2 3 2 3 Openface Music")
        self.reload.assert_called_once_with()
        self.menu(third, "Rename desktop output", ('  Mus"ic 2 ', True))
        self.assertEqual(self.asked.call_args.kwargs["text"], "Music")
        self.assertEqual(self.conf()[-1], "sink openface_mixer_play_3_4 2 3 2 3 2 3 Openface Music 2")
        self.menu(first, "Rename desktop output")     # the prefix is not offered for typing
        self.assertEqual(self.asked.call_args.kwargs["text"], "Mixer Playback")
        self.assertEqual(self.conf()[0].split(" ", 2)[:2], ["sink", "openface_mixer_playback"])
        self.menu(first, "Remove desktop output")
        self.menu(third, "Remove desktop output")
        self.assertEqual((w.st["sinks"], self.conf()), ([], []))
        self.assertEqual(self.reload.call_count, 5)
        self.assertEqual(self.menu(first), ["Add desktop output…"])

    def test_cancel_or_an_empty_name_changes_nothing(self):
        third = self.strip(self.w.row_play, 3)
        for answer in (("Music", False), (' "" \t', True)):
            self.menu(third, "Add desktop output", answer)
        self.assertEqual(self.w.st["sinks"], self.sinks.default_list(self.w.dev))
        self.reload.assert_not_called()

    def test_inputs(self):
        w = self.w
        strip = self.strip(w.row_in, 5)
        self.assertEqual(self.menu(strip), ["Add desktop input…"])
        self.menu(strip, "Add desktop input", ("Chat", True))
        entry = w.st["sinks"][-1]
        self.assertEqual((entry["kind"], entry["ch"], entry["stereo"]),
                         ("source", strip.channels[0], len(strip.channels) == 2))
        self.assertIn(" Openface Chat", self.conf()[-1])
        self.assertTrue(self.conf()[-1].startswith("source openface_mixer_in_"))
        self.assertEqual(self.menu(strip), ["Rename desktop input “Openface Chat”…",
                                            "Remove desktop input “Openface Chat”"])

    def test_outputs_and_a_full_list_have_no_add(self):
        self.assertEqual(self.menu(self.w.row_out.strips[0]), [])
        self.w.st["sinks"] = [{"kind": "source", "ch": c, "stereo": False, "name": f"{c}"}
                              for c in range(self.sinks.MAX)]
        from openface_mixer import app
        added = []
        with mock.patch.object(app, "QMenu", menu_running(lambda m: added.extend(
                a.isEnabled() for a in m.actions() if a.text() == "Add desktop output…"))):
            self.w.strip_menu(self.strip(self.w.row_play, 3), None)
        self.settle()
        self.assertEqual(added, [False])


if __name__ == "__main__":
    unittest.main()
