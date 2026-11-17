"""What runs while nobody is looking, and how the engine is started, on the real window offscreen
(see tests/test_controls.py WindowCase: the engine and the system are stubbed)."""
import unittest
from unittest import mock

from tests.test_controls import QApplication, WindowCase


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


class EngineStartTest(WindowCase):
    """Starting and restarting the engine never waits on the interface thread."""

    @classmethod
    def setUpClass(cls):
        if QApplication:
            from openface_mixer import app
            cls.ensure_engine = app.MainWindow.ensure_engine    # WindowCase stubs it
        super().setUpClass()

    def setUp(self):
        from openface_mixer import app
        w = self.w
        w._engine_up, w.engine_error = False, None
        self.addCleanup(lambda: (setattr(w, "_engine_due", 0.0), w.pace_timers()))
        self.start = mock.Mock(return_value=(True, "started"))
        self.stop = mock.Mock()
        for patch in (mock.patch.object(app.time, "sleep", side_effect=AssertionError("slept")),
                      mock.patch.object(w.engine, "start", self.start),
                      mock.patch.object(w.engine, "stop", self.stop)):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(w._start_timer.stop)

    def test_start_returns_at_once(self):
        from openface_mixer import config
        w = self.w
        config.matrix_file(w.dev).unlink(missing_ok=True)
        type(self).ensure_engine(w)
        self.start.assert_called_once_with()
        self.stop.assert_not_called()
        self.assertTrue(config.matrix_file(w.dev).is_file())    # the mix the engine starts with
        self.assertIsNone(w.engine_error)

    def test_a_starting_engine_is_not_called_stopped(self):
        from openface_mixer import app
        w = self.w
        status = {"processing": True, "rate": 48000, "sink_linked": True, "hw": "starting",
                  "hw_nodes": 0, "hw_levels": False, "dev_status": 0, "dev_status2": 0}
        w.poll_engine()
        self.assertIn("ENGINE STOPPED", w.status.text())
        w.status.setText("as it was")
        with mock.patch.object(app.time, "monotonic", return_value=100.0) as clock, \
                mock.patch.object(w.engine, "status", return_value=None) as engine_status, \
                mock.patch.object(w, "push_matrix") as push:
            w.start_engine()
            self.assertEqual(intervals(w)[1], 50)       # looks for it often, for a moment
            clock.return_value = 101.4
            w.poll_engine()
            self.assertEqual(w.status.text(), "as it was")
            engine_status.return_value = status         # it answers: shown, and sent the mix
            w.poll_engine()
            self.assertIn("HW DSP", w.status.text())
            push.assert_called_once_with()
            self.assertEqual(intervals(w)[1], 500)      # and the poll is back to its pace
            # one that does not come up is called stopped after a second and a half
            engine_status.return_value = None
            w.start_engine()
            clock.return_value = 103.0
            w.poll_engine()
            self.assertIn("ENGINE STOPPED", w.status.text())
            self.assertEqual(intervals(w)[1], 500)

    def test_a_start_that_fails_is_shown_in_the_status(self):
        w = self.w
        self.start.return_value = (False, "openface-mixer-engine not found")
        type(self).ensure_engine(w)
        self.assertEqual(w.engine_error, "openface-mixer-engine not found")

    def test_a_running_engine_is_left_alone(self):
        with mock.patch.object(self.w.engine, "status", return_value={"hw": "active"}):
            type(self).ensure_engine(self.w)
        self.start.assert_not_called()
        self.stop.assert_not_called()

    def test_an_older_engine_is_replaced_without_waiting(self):
        w = self.w
        with mock.patch.object(w.engine, "outdated", return_value=True):
            type(self).ensure_engine(w)
        self.stop.assert_called_once_with()
        self.start.assert_not_called()                  # once the old one has had time to go
        self.assertTrue(w._start_timer.isActive())
        self.assertEqual(w._start_timer.interval(), 300)
        w._start_timer.timeout.emit()
        self.start.assert_called_once_with()

    def test_the_restart_button(self):
        w = self.w
        w.engine_error = "an earlier failure"
        w._engine_up = True                             # it was running
        w.settings.eng_btn.click()
        self.stop.assert_called_once_with()
        self.assertIsNone(w.engine_error)
        self.assertTrue(w._start_timer.isActive())
        # the new engine is sent the mix, also when no poll saw the old one gone
        status = {"processing": True, "rate": 48000, "sink_linked": True, "hw": "starting",
                  "hw_nodes": 0, "hw_levels": False, "dev_status": 0, "dev_status2": 0}
        with mock.patch.object(w.engine, "status", return_value=status), \
                mock.patch.object(w, "push_matrix") as push:
            w.poll_engine()
            push.assert_called_once_with()

    def test_the_mix_is_sent_when_the_engine_comes_up(self):
        w = self.w
        status = {"processing": True, "rate": 48000, "sink_linked": True, "hw": "no-device",
                  "hw_nodes": 0, "hw_levels": False, "dev_status": 0, "dev_status2": 0}
        with mock.patch.object(w.engine, "status", return_value=None) as engine_status, \
                mock.patch.object(w, "push_matrix") as push:
            w.poll_engine()
            push.assert_not_called()                    # still starting
            engine_status.return_value = status
            w.poll_engine()
            w.poll_engine()
            push.assert_called_once_with()              # once, when it answered
            engine_status.return_value = None           # it stopped, and came back
            w.poll_engine()
            engine_status.return_value = status
            w.poll_engine()
            self.assertEqual(push.call_count, 2)


if __name__ == "__main__":
    unittest.main()
