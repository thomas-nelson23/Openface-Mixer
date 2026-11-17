"""What runs while nobody is looking, on the real window offscreen (see tests/test_controls.py
WindowCase: the engine and the system are stubbed)."""
import unittest
from unittest import mock

from tests.test_controls import WindowCase


def intervals(w):
    """(meters, engine status, amixer, pactl) poll intervals in ms, 0 for one that is stopped."""
    return tuple(t.interval() if t.isActive() else 0
                 for t in (w._meter_timer, w._engine_timer, w._hw_timer, w._profile_timer))


SHOWN, HIDDEN = (33, 500, 1000, 3000), (0, 2000, 0, 0)


class PollingTest(WindowCase):
    def setUp(self):
        self.w.show()
        self.settle()

    def test_a_hidden_window_stops_polling_and_refreshes_once_when_shown(self):
        w = self.w
        self.assertEqual(intervals(w), SHOWN)
        with mock.patch.object(w, "poll_hw") as hw, mock.patch.object(w, "poll_profile") as pro, \
                mock.patch.object(w, "poll_engine") as eng, \
                mock.patch.object(w.engine, "read_peaks", return_value=(None, None)) as peaks:
            w.hide()
            self.settle()
            self.assertEqual(intervals(w), HIDDEN)
            self.assertEqual(hw.call_count + pro.call_count + eng.call_count, 0)
            w.show()
            # at once: one round of every poll, and a meter read that wakes the engine's meters
            self.assertEqual((hw.call_count, pro.call_count, eng.call_count), (1, 1, 1))
            peaks.assert_called_once_with()
            w.show()                                    # shown already: nothing more
            self.assertEqual((hw.call_count, pro.call_count, eng.call_count), (1, 1, 1))
        self.assertEqual(intervals(w), SHOWN)

    def test_meters_are_read_for_a_controller_while_hidden(self):
        w = self.w
        w.hide()
        self.settle()
        with mock.patch.object(w.remote, "wants_levels", return_value=True):
            w.remote.status_changed.emit("a controller connected")
            self.assertEqual(intervals(w), (100,) + HIDDEN[1:])
            w.show()
            self.settle()
            self.assertEqual(intervals(w), SHOWN)       # a shown window reads them anyway
            w.hide()
            self.settle()
            self.assertEqual(intervals(w), (100,) + HIDDEN[1:])
        w.remote.status_changed.emit("the controller left")
        self.assertEqual(intervals(w), HIDDEN)

    def test_levels_are_wanted_only_while_they_go_somewhere(self):
        remote = self.w.remote
        self.addCleanup(lambda: (remote.clients.clear(), setattr(remote, "sock", None),
                                 setattr(remote, "target", None)))
        remote.levels_on, remote.target = True, ("192.0.2.1", 9001)
        self.assertFalse(remote.wants_levels())         # the remote is switched off
        remote.sock = mock.Mock()
        self.assertTrue(remote.wants_levels())          # to the Send to host
        remote.target = None
        self.assertFalse(remote.wants_levels())         # nobody there
        remote.clients[("192.0.2.2", 7001)] = 0.0
        self.assertTrue(remote.wants_levels())          # a controller
        remote.levels_on = False
        self.assertFalse(remote.wants_levels())         # "Send peak levels" is off

    def test_a_window_that_was_never_shown_polls_as_a_hidden_one(self):
        from openface_mixer import app
        with mock.patch.object(app.MainWindow, "poll_hw") as hw, \
                mock.patch.object(app.MainWindow, "poll_profile") as pro:
            w = app.MainWindow(self.w.dev, background=True)
            self.addCleanup(self.settle)
            self.addCleanup(w.deleteLater)
            self.assertEqual(intervals(w), HIDDEN)
            self.assertEqual(hw.call_count + pro.call_count, 0)     # no amixer, no pactl
            w.show()
            self.settle()
            self.assertEqual(intervals(w), SHOWN)
            self.assertEqual((hw.call_count, pro.call_count), (1, 1))
            w.close()                                   # started in the background: only hides
            self.settle()
            self.assertFalse(w.isVisible())
            self.assertEqual(intervals(w), HIDDEN)
            w.quit_app()
            self.settle()


if __name__ == "__main__":
    unittest.main()
