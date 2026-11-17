import unittest

from openface_mixer.wheel import Wheel, take_steps


class Delta:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class Event:
    """What Wheel asks of a QWheelEvent."""

    def __init__(self, dx=0, dy=0, px=0, py=0):
        self.angle, self.pixel, self.accepted = Delta(dx, dy), Delta(px, py), None

    def angleDelta(self):
        return self.angle

    def pixelDelta(self):
        return self.pixel

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


class TakeStepsTest(unittest.TestCase):
    def test_a_notch_is_a_step(self):
        self.assertEqual(take_steps(0, 120), (1, 0))
        self.assertEqual(take_steps(0, -120), (-1, 0))
        self.assertEqual(take_steps(0, 360), (3, 0))

    def test_small_movements_add_up(self):
        rest, total = 0, 0
        for _ in range(16):                 # a touchpad swipe: sixteen events of 15 units
            steps, rest = take_steps(rest, 15)
            total += steps
        self.assertEqual((total, rest), (2, 0))
        self.assertEqual(take_steps(0, 119), (0, 119))
        self.assertEqual(take_steps(119, 1), (1, 0))
        self.assertEqual(take_steps(100, 150), (2, 10))

    def test_down_mirrors_up(self):
        self.assertEqual(take_steps(0, -119), (0, -119))
        self.assertEqual(take_steps(-100, -150), (-2, -10))

    def test_turning_back_drops_the_rest(self):
        self.assertEqual(take_steps(100, -30), (0, -30))
        self.assertEqual(take_steps(-100, 120), (1, 0))


class WheelTest(unittest.TestCase):
    def test_notched_wheel(self):
        w = Wheel()
        e = Event(dy=120)
        self.assertEqual(w.steps(e), 1)
        self.assertTrue(e.accepted)
        self.assertEqual(w.steps(Event(dy=-120)), -1)

    def test_sideways_is_ignored(self):
        w = Wheel()
        for e in (Event(dx=120), Event(dx=-120), Event(dx=-90, dy=30), Event(dx=40, dy=-40),
                  Event()):
            self.assertEqual(w.steps(e), 0)
            self.assertIs(e.accepted, False)
        self.assertEqual(w.rest, 0)         # and leaves nothing behind for the next event

    def test_touchpad_events_are_taken_while_they_add_up(self):
        w = Wheel()
        events = [Event(dx=2, dy=30) for _ in range(4)]
        self.assertEqual([w.steps(e) for e in events], [0, 0, 0, 1])
        self.assertTrue(all(e.accepted for e in events))   # none of them scrolls the row

    def test_pixels_only(self):
        w = Wheel()
        self.assertEqual([w.steps(Event(py=20)) for _ in range(3)], [0, 0, 1])
        e = Event(px=30, py=5)
        self.assertEqual(w.steps(e), 0)
        self.assertIs(e.accepted, False)

    def test_angle_wins_over_pixels(self):
        self.assertEqual(Wheel().steps(Event(dy=120, py=400)), 1)


if __name__ == "__main__":
    unittest.main()
