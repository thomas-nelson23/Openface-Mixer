"""The strip menus' desktop outputs and inputs and the Clock section's PipeWire rate and buffer, on
the real window offscreen (tests/test_controls.py WindowCase: the engine and the system are
stubbed, the config is in a temporary folder). pw-metadata is stubbed: the command lines are
checked, nothing is run."""
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_controls import QApplication, WindowCase

if QApplication:
    from PySide6.QtWidgets import QMenu


HINT = "PipeWire's rate and buffer apply to every program and device."


def menu_running(run):
    """A QMenu whose exec() calls run(menu) instead of showing it."""
    class Menu(QMenu):
        def exec(self, *_args):
            run(self)
    return Menu


def done(code=0, stderr=""):
    return subprocess.CompletedProcess([], code, "", stderr)


class DesktopMenuTest(WindowCase):
    def setUp(self):
        from openface_mixer import config, sinks
        self.sinks, self.config = sinks, config
        self.assertNotEqual(config.CONFIG_DIR, Path.home() / ".config" / "openface-mixer")
        self.w.st["sinks"] = sinks.default_list(self.w.dev)
        self.w.save()
        patch = mock.patch.object(self.w.engine, "reload_sinks")
        self.reload = patch.start()
        self.addCleanup(patch.stop)     # never patch.stopall: WindowCase's patches must stay

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
        self.assertEqual(self.asked.call_args.args[2], "Name for playback AD1 3/4 in the "
                         "desktop's sound settings (shown after “Openface”):")
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


class PipeWireClockTest(WindowCase):
    SETTINGS = {"clock.force-rate": 0, "clock.force-quantum": 0, "clock.min-quantum": 32,
                "clock.max-quantum": 2048}

    def setUp(self):
        from openface_mixer import hardware, settings_panel
        self.hardware, self.sp, self.p = hardware, settings_panel, self.w.settings
        self.notices = []
        self.p.notice.connect(self.notices.append)
        self.addCleanup(self.p.notice.disconnect, self.notices.append)
        self.reading = mock.patch.object(self.p, "read_pw_clock")
        self.reads = self.reading.start()
        self.addCleanup(self.reading.stop)

    def items(self, combo):
        return [combo.itemText(i) for i in range(combo.count())]

    def choose(self, combo, data, answer=None):
        with mock.patch.object(self.hardware.subprocess, "run",
                               return_value=answer or done()) as run:
            i = combo.findData(data)
            combo.setCurrentIndex(i)
            combo.activated.emit(i)
        return [c.args[0] for c in run.call_args_list]

    def test_the_rows(self):
        p = self.p
        grid = p.clock_grid
        keys = [grid.itemAtPosition(r, 0).widget().text() for r in range(grid.rowCount())
                if grid.itemAtPosition(r, 0)]
        self.assertEqual(keys[-3:], ["PipeWire rate", "Buffer", HINT])
        self.assertIn("every program and every device", p.pw_rate.toolTip())
        p._show_pw_clock(None)                      # PipeWire is not running
        self.assertFalse(p.pw_rate.isEnabled() or p.pw_quantum.isEnabled())
        p._show_pw_clock(dict(self.SETTINGS))
        self.assertTrue(p.pw_rate.isEnabled() and p.pw_quantum.isEnabled())
        self.assertEqual(self.items(p.pw_rate), ["Auto", "44.1 kHz", "48 kHz", "88.2 kHz",
                                                 "96 kHz", "176.4 kHz", "192 kHz"])
        self.assertEqual(self.items(p.pw_quantum),
                         ["Auto", "32", "64", "128", "256", "512", "1024", "2048"])
        self.assertEqual((p.pw_rate.currentText(), p.pw_quantum.currentText()), ("Auto", "Auto"))
        p._show_pw_clock({**self.SETTINGS, "clock.min-quantum": 64,
                          "clock.max-quantum": 1024, "clock.force-quantum": 48})
        self.assertEqual(self.items(p.pw_quantum),
                         ["Auto", "48", "64", "128", "256", "512", "1024"])
        self.assertEqual(p.pw_quantum.currentText(), "48")

    def test_setting_and_auto(self):
        p = self.p
        p._show_pw_clock(dict(self.SETTINGS))
        self.assertEqual(self.choose(p.pw_rate, 0), [])     # Auto while Auto: nothing written
        self.reads.assert_not_called()
        self.assertEqual(self.choose(p.pw_rate, 96000),
                         [["pw-metadata", "-n", "settings", "0", "clock.force-rate", "96000"]])
        self.assertEqual(self.choose(p.pw_quantum, 128),
                         [["pw-metadata", "-n", "settings", "0", "clock.force-quantum", "128"]])
        self.assertEqual(self.reads.call_count, 2)          # then what PipeWire has is shown
        p._show_pw_clock({**self.SETTINGS, "clock.force-rate": 96000})
        self.assertEqual(p.pw_rate.currentText(), "96 kHz")
        self.assertEqual(self.choose(p.pw_rate, 0),         # back to Auto
                         [["pw-metadata", "-n", "settings", "0", "clock.force-rate", "0"]])
        self.assertEqual(self.notices, [])

    def test_a_failure_is_shown(self):
        p = self.p
        p._show_pw_clock(dict(self.SETTINGS))
        self.choose(p.pw_rate, 48000, done(255, "can't connect: Host is down"))
        self.assertEqual(self.notices, ["Not changed: PipeWire is not running"])

    def test_read_on_a_thread(self):
        self.reading.stop()
        with mock.patch.object(self.sp, "pw_clock",
                               return_value={**self.SETTINGS, "clock.force-quantum": 512}):
            self.p.read_pw_clock()
            deadline = time.monotonic() + 5
            while self.p.pw_quantum.currentText() != "512" and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertEqual(self.p.pw_quantum.currentText(), "512")


class RayDATClockTest(WindowCase):
    device = "raydat"

    def test_the_rows_follow_the_cards_own(self):
        p = self.w.settings
        grid = p.clock_grid
        keys = [grid.itemAtPosition(r, 0).widget().text() for r in range(grid.rowCount())
                if grid.itemAtPosition(r, 0)]
        self.assertEqual(keys, ["Clock mode", "Sample rate", "Prefer", "Internal rate",
                                "PipeWire rate", "Buffer", HINT])
        self.assertIn("Internal rate", p.pw_rate.toolTip())


if __name__ == "__main__":
    unittest.main()
