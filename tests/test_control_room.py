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
        cr.control_room(st)["main_b"] = 1
        st["out"][0]["gain"] = -6.0
        cr.toggle(st, "speaker_b")
        g, og = run(st)
        self.assertEqual(gain(g, 0, 0), 0.0)
        self.assertAlmostEqual(gain(g, 2, 0), 1.0)
        self.assertAlmostEqual(gain(g, 3, 1), 1.0)
        self.assertEqual(og[0], 0.0)
        self.assertAlmostEqual(og[2], m.db2lin(-6.0), places=6)

    def test_speaker_b_pair_silent_while_off(self):
        st = mixed()
        st["sends"]["in"][4][1] = [0.0, 0.0]     # B's own mix, and playback 3/4 by default
        cr.control_room(st)["main_b"] = 1
        g, og = run(st)
        self.assertAlmostEqual(gain(g, 0, 0), 1.0)
        for o in (2, 3):
            self.assertEqual(gain(g, o, 4), 0.0)
            self.assertEqual(gain(g, o, m.N_IN + o), 0.0)
            self.assertEqual(og[o], 0.0)
        cr.toggle(st, "speaker_b")
        cr.toggle(st, "speaker_b")
        g2, og2 = run(st)
        self.assertEqual(list(g2), list(g))
        self.assertEqual(list(og2), list(og))

    def test_speaker_b_needs_a_pair(self):
        st = mixed()
        self.assertFalse(cr.toggle(st, "speaker_b"))
        g, og = run(st)
        self.assertEqual(list(g), list(m.compute_matrix(st)))
        self.assertEqual(list(og), list(m.compute_out_gains(st)))

    def test_old_state_unassigns_speaker_b_unless_on(self):
        st = mixed()
        st["control_room"] = {"main_b": 1, "speaker_b": False}
        self.assertEqual(cr.control_room(st)["main_b"], cr.NO_PAIR)
        st["control_room"] = {"main_b": 2, "speaker_b": True}
        self.assertEqual(cr.control_room(st)["main_b"], 2)
        cr.control_room(st)["main_b"] = 3          # current version: left alone
        self.assertEqual(cr.control_room(st)["main_b"], 3)

    def test_dim_follows_speaker_b(self):
        st = mixed()
        cr.control_room(st)["main_b"] = 1
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


class LoadTest(unittest.TestCase):
    """load(): a damaged or hand-edited control room can't stop the window or the mix."""

    def usable(self, st):
        c = cr.control_room(st)
        self.assertEqual(set(c), set(cr.default_control_room()))
        for key in ("dim", "mono", "ext_in", "talkback"):
            c[key] = True
        run(st)
        cr.step_volume(st, 1)
        return c

    def test_no_control_room(self):
        for value in (None, "x", [1], 7):
            with self.subTest(value=value):
                st = m.default_state()
                st["control_room"] = value
                self.assertEqual(cr.load(st, 8), cr.default_control_room(8))
        st = m.default_state()
        self.assertEqual(cr.load(st, 8), cr.default_control_room(8))

    def test_wrong_values_go_back_to_their_defaults(self):
        base = cr.default_control_room(8)
        bad = {"main": (99, -1, "x", 1.5, True, None), "phones": (18, "x"),
               "main_b": (99, -2, "x"), "talkback_src": (36, -4, "a"), "ext_src": (35, -1, "a"),
               "talkback_dests": (5, "x", [99], [0, "x"], None),
               "dim_db": (20.0, "x", None, float("nan")), "talkback_dim_db": (6, "x"),
               "encoder": (5, "aux", None), "dim": (1, "x"), "mono": (None,),
               "speaker_b": ("yes",), "ext_in": (0,), "version": ("new", None)}
        for key, values in bad.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    st = m.default_state()
                    st["control_room"] = dict(base, **{key: value})
                    self.assertEqual(cr.load(st, 8), base)
                    self.usable(st)

    def test_good_values_are_kept(self):
        st = m.default_state()
        good = dict(cr.default_control_room(8), main=3, main_b=4, phones=5, dim=True,
                    dim_db=-30, mono=True, speaker_b=True, talkback_src=35, ext_src=34,
                    talkback_dests=[5, 6], talkback_dim_db=0.0, ext_in=True, encoder="phones")
        st["control_room"] = dict(good, talkback=True)
        self.assertEqual(cr.load(st, 8), good)       # all but Talkback, which is switched off
        self.usable(st)

    def test_old_control_room_is_still_upgraded(self):
        st = m.default_state()
        st["control_room"] = {"main_b": 1, "speaker_b": False}
        c = cr.load(st, 8)
        self.assertEqual((c["main_b"], c["phones"], c["version"]), (cr.NO_PAIR, 8,
                                                                    cr.CR_VERSION))


if __name__ == "__main__":
    unittest.main()
