import unittest

from openface_mixer import control_room as cr
from openface_mixer import model as m


def gain(g, out, src):
    return g[out * m.N_SRC + src]


def mixed():
    """Default state plus input 1 hard left and input 2 hard right into Main Out (AD1 1/2)."""
    st = m.default_state()
    st["sends"]["in"][0][0] = [0.0, -1.0]
    st["sends"]["in"][1][0] = [0.0, 1.0]
    return st


def run(st):
    return cr.apply(st, m.compute_matrix(st), m.compute_out_gains(st))


class ControlRoomTest(unittest.TestCase):
    def test_defaults_change_nothing(self):
        st = mixed()
        g, og = run(st)
        self.assertEqual(list(g), list(m.compute_matrix(st)))
        self.assertEqual(list(og), list(m.compute_out_gains(st)))
        self.assertIn("control_room", st)

    def test_dim_lowers_main_master(self):
        st = mixed()
        cr.toggle(st, "dim")
        _, og = run(st)
        self.assertAlmostEqual(og[0], 0.1, places=6)
        self.assertAlmostEqual(og[1], 0.1, places=6)
        self.assertAlmostEqual(og[2], 1.0)

    def test_mono_sums_both_sides(self):
        st = mixed()
        cr.toggle(st, "mono")
        g, _ = run(st)
        for o in (0, 1):
            self.assertAlmostEqual(gain(g, o, 0), 0.5)
            self.assertAlmostEqual(gain(g, o, 1), 0.5)

    def test_speaker_b_moves_main_mix(self):
        st = mixed()
        st["out"][0]["gain"] = -6.0
        cr.toggle(st, "speaker_b")
        g, og = run(st)
        self.assertEqual(gain(g, 0, 0), 0.0)
        self.assertAlmostEqual(gain(g, 2, 0), 1.0)
        self.assertAlmostEqual(gain(g, 3, 1), 1.0)
        self.assertEqual(og[0], 0.0)
        self.assertAlmostEqual(og[2], m.db2lin(-6.0), places=6)

    def test_dim_follows_speaker_b(self):
        st = mixed()
        cr.toggle(st, "speaker_b")
        cr.toggle(st, "dim")
        _, og = run(st)
        self.assertAlmostEqual(og[2], 0.1, places=6)

    def test_ext_in_replaces_main_mix(self):
        st = mixed()
        st["control_room"] = {"ext_in": True, "ext_src": 4}
        g, _ = run(st)
        self.assertEqual(gain(g, 0, 0), 0.0)
        self.assertEqual(gain(g, 0, m.N_IN), 0.0)
        self.assertEqual(gain(g, 0, 4), 1.0)
        self.assertEqual(gain(g, 1, 5), 1.0)

    def test_talkback_to_phones(self):
        st = mixed()
        st["control_room"] = {"talkback": True, "talkback_src": 7}
        g, _ = run(st)
        ph = 2 * (m.N_PAIRS - 1)
        self.assertEqual(gain(g, ph, 7), 1.0)
        self.assertEqual(gain(g, ph + 1, 7), 1.0)
        self.assertAlmostEqual(gain(g, ph, m.N_IN + ph), 0.1, places=6)   # playback dimmed
        self.assertAlmostEqual(gain(g, 0, 0), 1.0)                         # main untouched

    def test_encoder_steps_main_then_phones(self):
        st = m.default_state()
        self.assertEqual(cr.step_volume(st, -4), (0, -2.0))
        cr.control_room(st)["encoder"] = "phones"
        self.assertEqual(cr.step_volume(st, 3), (m.N_PAIRS - 1, 1.5))
        self.assertEqual(st["out"][0]["gain"], -2.0)

    def test_encoder_clamps(self):
        st = m.default_state()
        self.assertEqual(cr.step_volume(st, 100), (0, m.FADER_MAX_DB))
        self.assertEqual(cr.step_volume(st, -1000), (0, m.NEG_INF))
        self.assertIsNone(st["out"][0]["gain"])
        self.assertEqual(cr.step_volume(st, 2), (0, m.FADER_MIN_DB + 1.0))


if __name__ == "__main__":
    unittest.main()
