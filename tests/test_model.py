import unittest

from openface_mixer import model as m


def gain(g, out, src):
    return g[out * m.N_SRC + src]


class TaperTest(unittest.TestCase):
    def test_round_trip(self):
        for db in (6, 3, 0, -5, -10, -20, -30, -45, -60, -80):
            self.assertAlmostEqual(m.pos2db(m.db2pos(db)), db, places=9)

    def test_ends(self):
        self.assertEqual(m.pos2db(0.0), m.NEG_INF)
        self.assertEqual(m.db2pos(m.NEG_INF), 0.0)
        self.assertEqual(m.db2pos(None), 0.0)
        self.assertAlmostEqual(m.pos2db(1.0), m.FADER_MAX_DB)


class MatrixTest(unittest.TestCase):
    def test_default_is_passthrough(self):
        g = m.compute_matrix(m.default_state())
        for o in range(m.N_OUT):
            for s in range(m.N_SRC):
                self.assertEqual(gain(g, o, s), 1.0 if s == m.N_IN + o else 0.0, (o, s))

    def test_mono_input_pans_into_pair(self):
        st = m.default_state()
        st["sends"]["in"][0][16] = [0.0, -0.5]          # input 1 -> phones, panned left
        g = m.compute_matrix(st)
        self.assertAlmostEqual(gain(g, 32, 0), 1.0)
        self.assertAlmostEqual(gain(g, 33, 0), 0.5)

    def test_stereo_pair_keeps_sides(self):
        st = m.default_state()
        st["stereo"]["in"][0] = True
        st["sends"]["in"][0][0] = [0.0, 0.0]
        st["sends"]["in"][1][0] = [0.0, 0.0]
        g = m.compute_matrix(st)
        self.assertEqual((gain(g, 0, 0), gain(g, 1, 0)), (1.0, 0.0))
        self.assertEqual((gain(g, 0, 1), gain(g, 1, 1)), (0.0, 1.0))

    def test_source_mute(self):
        st = m.default_state()
        st["mute"]["play"][0] = True
        self.assertEqual(gain(m.compute_matrix(st), 0, m.N_IN), 0.0)

    def test_master_is_separate(self):
        st = m.default_state()
        st["out"][0]["gain"] = -6.0
        st["out"][1]["mute"] = True
        self.assertEqual(gain(m.compute_matrix(st), 0, m.N_IN), 1.0)   # sends unaffected
        self.assertEqual(gain(m.compute_matrix(st), 2, m.N_IN + 2), 1.0)
        og = m.compute_out_gains(st)
        self.assertAlmostEqual(og[0], 10 ** (-6 / 20), places=6)
        self.assertAlmostEqual(og[1], 10 ** (-6 / 20), places=6)
        self.assertEqual((og[2], og[3]), (0.0, 0.0))
        self.assertEqual(og[4], 1.0)


class GroupTest(unittest.TestCase):
    def setUp(self):
        self.st = m.default_state()
        self.st["selected"] = 0
        self.st["groups"] = {"play:0": 1, "play:2": 1, "out:3": 2}
        m.set_strip_db(self.st, "play:2", -10.0)

    def test_relative_follow(self):
        changed = m.group_follow(self.st, "play:0", 0.0, -3.0)
        self.assertEqual(changed, {"play:2": -13.0})
        self.assertEqual(m.get_strip_db(self.st, "play:2"), -13.0)

    def test_other_groups_untouched(self):
        m.group_follow(self.st, "play:0", 0.0, -3.0)
        self.assertEqual(m.get_strip_db(self.st, "out:3"), 0.0)

    def test_down_to_inf_and_back(self):
        # leader 0 -> -inf: everyone in the group goes to -inf
        m.group_follow(self.st, "play:0", 0.0, m.NEG_INF)
        self.assertEqual(m.get_strip_db(self.st, "play:2"), m.NEG_INF)
        # -inf counts as the fader floor, so coming back up 60 dB lands the member at -20 dB
        m.group_follow(self.st, "play:0", m.NEG_INF, m.FADER_MIN_DB + 60)
        self.assertEqual(m.get_strip_db(self.st, "play:2"), m.FADER_MIN_DB + 60)

    def test_clamps_at_max(self):
        m.group_follow(self.st, "play:0", 0.0, 20.0)
        self.assertEqual(m.get_strip_db(self.st, "play:2"), m.FADER_MAX_DB)

    def test_stereo_strip_sets_both_channels(self):
        m.set_strip_db(self.st, "play:0", -4.0)
        self.assertEqual(self.st["sends"]["play"][0][0][0], -4.0)
        self.assertEqual(self.st["sends"]["play"][1][0][0], -4.0)


class MixTest(unittest.TestCase):
    def test_extract_apply_round_trip(self):
        a = m.default_state()
        a["sends"]["in"][3][5] = [-7.5, 0.25]
        a["groups"] = {"in:3": 2}
        b = m.apply_mix(m.default_state(), m.extract_mix(a))
        self.assertEqual(m.extract_mix(a), m.extract_mix(b))

    def test_mix_is_a_copy(self):
        a = m.default_state()
        mix = m.extract_mix(a)
        a["sends"]["in"][0][0][0] = 3.0
        self.assertIsNone(mix["sends"]["in"][0][0][0])

    def test_upgrade_old_state(self):
        old = m.default_state()
        del old["groups"], old["active_slot"]
        old["version"] = 1
        st = m.upgrade_state(old)
        self.assertEqual(st["groups"], {})
        self.assertEqual(st["version"], m.STATE_VERSION)
        self.assertEqual(m.upgrade_state(None)["version"], m.STATE_VERSION)

    def test_upgrade_adds_mixer_mode(self):
        old = m.default_state()
        del old["mixer_mode"]
        self.assertEqual(m.upgrade_state(old)["mixer_mode"], "hardware")
        old["mixer_mode"] = "bogus"
        self.assertEqual(m.upgrade_state(old)["mixer_mode"], "hardware")


class LabelTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(m.chan_label(0, 32, False), "AD1 1")
        self.assertEqual(m.chan_label(31, 32, False), "AD4 8")
        self.assertEqual(m.chan_label(32, 32, True), "Ph L")
        self.assertEqual(m.pair_label(32, 32, True), "Phones")
        self.assertEqual(m.chan_label(4, 16, False), "AD2 1")   # 2x speed: 4 channels per port


if __name__ == "__main__":
    unittest.main()
