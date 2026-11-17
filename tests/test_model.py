import copy
import json
import math
import random
import unittest

from openface_mixer import model as m


def gain(g, out, src):
    return g[out * m.N_SRC + src]


def damaged_mixes():
    """(what is wrong, mix) for every way a part of a mix can be damaged."""
    def mix(edit):
        mx = m.extract_mix(m.default_state())
        edit(mx)
        return mx

    for key in m.MIX_KEYS:
        for value in ("x", 7, None, [1], {"a": 1}):
            yield f"{key} is {value!r}", mix(lambda mx: mx.update({key: value}))
    for key in ("stereo", "mute", "sends"):
        for kind in ("in", "play"):
            yield f"{key} has no {kind}", mix(lambda mx: mx[key].pop(kind))
            yield f"{key}.{kind} is a string", mix(lambda mx: mx[key].update({kind: "x"}))
    yield "a mute that is no bool", mix(lambda mx: mx["mute"]["in"].__setitem__(3, "x"))
    yield "a stereo link that is no bool", mix(lambda mx: mx["stereo"]["play"].__setitem__(0, 1))
    yield "a channel's sends are a string", mix(lambda mx: mx["sends"]["in"].__setitem__(3, "x"))
    for send in ("x", [1], [0.0, 0.0, 0.0], ["loud", "left"], [0.0, None], [float("nan"), 0.0],
                 [0.0, float("inf")], [True, 0.0], [10 ** 400, 0.0], [0.0, -10 ** 400]):
        yield f"a send is {send!r}", mix(lambda mx: mx["sends"]["play"][2].__setitem__(1, send))
    for out in (5, "x", {}, {"gain": 0.0}, {"mute": False}, {"gain": "x", "mute": False},
                {"gain": 0.0, "mute": 1}, {"gain": 10 ** 400, "mute": False}):
        yield f"an output is {out!r}", mix(lambda mx: mx["out"].__setitem__(2, out))
    for groups in ({"in:0": "x"}, {"bogus": 2}, {"in:99": 1}, {"in:0": 9}, {"in:0": 0},
                   {"out:1": 1.5}, {"in:0": True}, {7: 1}):
        yield f"groups are {groups!r}", mix(lambda mx: mx.update(groups=groups))
    kept = {"sends": [[[None, 0.0]] * m.N_PAIRS] * 2, "mute": [False, True], "group": 0}
    for unlinked in ({"in:1": kept}, {"out:0": kept}, {"in:36": kept}, {"bogus": kept}, {7: kept},
                     {"in:0": 5}, {"in:0": {}}, {"in:0": dict(kept, group=9)},
                     {"in:0": dict(kept, group="x")}, {"in:0": dict(kept, mute="x")},
                     {"in:0": dict(kept, mute=[1, 0])}, {"in:0": dict(kept, sends="x")},
                     {"in:0": dict(kept, sends=["x"])}, {"in:0": dict(kept, sends=[[["x", 0]]])},
                     {"in:0": {k: v for k, v in kept.items() if k != "sends"}}):
        yield f"unlinked is {unlinked!r:.60}", mix(lambda mx: mx.update(unlinked=unlinked))


class TaperTest(unittest.TestCase):
    def test_round_trip(self):
        for db in (6, 3, 0, -5, -10, -20, -30, -45, -60, -80):
            self.assertAlmostEqual(m.pos2db(m.db2pos(db)), db, places=9)

    def test_ends(self):
        self.assertEqual(m.pos2db(0.0), m.NEG_INF)
        self.assertEqual(m.db2pos(m.NEG_INF), 0.0)
        self.assertEqual(m.db2pos(None), 0.0)
        self.assertAlmostEqual(m.pos2db(1.0), m.FADER_MAX_DB)


class TypedLevelTest(unittest.TestCase):
    def test_numbers(self):
        for text, db in (("-12", -12.0), ("-12.5", -12.5), ("+3", 3.0), ("0", 0.0),
                         (" -6 ", -6.0), ("-12,5", -12.5), ("-12 dB", -12.0), ("3db", 3.0),
                         ("−6", -6.0)):
            self.assertEqual(m.parse_db(text), db, text)

    def test_off(self):
        for text in ("-inf", "-INF", "-∞", "off", "Off"):
            self.assertEqual(m.parse_db(text), m.NEG_INF, text)

    def test_not_a_level(self):
        # "inf" and "nan" are numbers to float(); they must never reach a fader
        for text in ("", "abc", "inf", "+inf", "nan", "infinity", "-infinity", "--3", "1 2", "dB"):
            self.assertIsNone(m.parse_db(text), text)

    def test_fader_range(self):
        self.assertEqual(m.fader_db(99.0), m.FADER_MAX_DB)
        self.assertEqual(m.fader_db(6.0), 6.0)
        self.assertEqual(m.fader_db(-12.5), -12.5)
        self.assertEqual(m.fader_db(m.FADER_MIN_DB), m.FADER_MIN_DB)
        self.assertEqual(m.fader_db(m.FADER_MIN_DB - 0.1), m.NEG_INF)
        self.assertEqual(m.fader_db(m.NEG_INF), m.NEG_INF)


class MatrixTest(unittest.TestCase):
    def test_default_is_passthrough(self):
        g = m.compute_matrix(m.default_state())
        for o in range(m.N_OUT):
            for s in range(m.N_SRC):
                self.assertEqual(gain(g, o, s), 1.0 if s == m.N_IN + o else 0.0, (o, s))

    def test_mono_input_pans_into_pair(self):
        st = m.default_state()
        st["sends"]["in"][0][16] = [0.0, -0.5]          # input 1 -> phones, half way left
        g = m.compute_matrix(st)
        self.assertAlmostEqual(gain(g, 32, 0), math.cos(math.pi / 8), places=6)
        self.assertAlmostEqual(gain(g, 33, 0), math.sin(math.pi / 8), places=6)

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


class SoloTest(unittest.TestCase):
    """TotalMix solo: solo-in-place, post fader, current submix only."""

    def setUp(self):
        self.st = m.default_state()
        self.st["selected"] = 16                       # phones
        # hard left, so the left output has the fader's level
        self.st["sends"]["in"][0][16] = [-6.0, -1.0]   # input 1 -> phones
        self.st["sends"]["in"][2][16] = [0.0, -1.0]    # input 3 -> phones
        self.st["sends"]["in"][2][0] = [0.0, -1.0]     # input 3 -> AD1 1/2 too

    def test_no_solo_changes_nothing(self):
        self.assertEqual(m.compute_matrix(self.st), m.compute_matrix(self.st, solo=False))

    def test_unsoloed_sources_drop_out_of_current_submix(self):
        self.st["solo"]["in"][0] = True
        g = m.compute_matrix(self.st)
        self.assertAlmostEqual(gain(g, 32, 0), 10 ** (-6 / 20), places=6)   # soloed, post fader
        self.assertEqual(gain(g, 32, 2), 0.0)                               # silenced
        self.assertEqual(gain(g, 32, m.N_IN + 32), 0.0)                     # playback too

    def test_other_submixes_untouched(self):
        self.st["solo"]["in"][0] = True
        g = m.compute_matrix(self.st)
        self.assertEqual(gain(g, 0, 2), 1.0)
        self.assertEqual(gain(g, 0, m.N_IN), 1.0)

    def test_several_solos_add_up(self):
        self.st["solo"]["in"][0] = self.st["solo"]["in"][2] = True
        g = m.compute_matrix(self.st)
        self.assertGreater(gain(g, 32, 0), 0.0)
        self.assertEqual(gain(g, 32, 2), 1.0)

    def test_mute_beats_solo(self):
        self.st["solo"]["in"][0] = True
        self.st["mute"]["in"][0] = True
        self.assertEqual(gain(m.compute_matrix(self.st), 32, 0), 0.0)

    def test_follows_selected_submix(self):
        self.st["solo"]["in"][0] = True
        self.st["selected"] = 0
        g = m.compute_matrix(self.st)
        self.assertEqual(gain(g, 0, 2), 0.0)       # now AD1 1/2 is the soloed submix
        self.assertEqual(gain(g, 32, 2), 1.0)      # phones back to normal


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


class StereoLinkTest(unittest.TestCase):
    """Linking a pair keeps what its two channels had; unlinking brings it back."""

    def setUp(self):
        rng = random.Random(3)
        self.st = st = m.default_state(16)
        for c in (4, 5):
            st["sends"]["in"][c] = [[rng.choice((None, -20.0, 0.0, 3.5)),
                                     rng.choice((-1.0, -0.25, 0.0, 0.7))]
                                    for _ in range(m.N_PAIRS)]
        st["sends"]["in"][4][16] = [-6.0, -0.5]
        st["sends"]["in"][5][16] = [-12.0, 0.25]
        st["mute"]["in"][5] = True
        st["groups"] = {"in:4": 1, "in:5": 2, "in:6": 2}
        st["names"] = {"in:4": "Kick", "in:5": "Snare"}
        st["colors"] = {"in:5": "#ff0000"}
        st["icons"] = {"in:5": "drum"}
        self.before = copy.deepcopy(st)

    def test_link_then_unlink_gives_the_state_from_before(self):
        m.set_stereo(self.st, "in", 4, True)
        self.assertNotEqual(self.st, self.before)
        m.set_stereo(self.st, "in", 4, False)
        self.assertEqual(self.st, self.before)

    def test_a_linked_pair_has_one_level_and_a_balance(self):
        st = self.st
        m.set_stereo(st, "in", 4, True)
        self.assertEqual(st["sends"]["in"][5], st["sends"]["in"][4])
        for (db, pan), (was, _pan) in zip(st["sends"]["in"][4], self.before["sends"]["in"][4]):
            self.assertEqual((db, pan), (was, 0.0))
        self.assertEqual(st["mute"]["in"][4:6], [False, False])
        self.assertEqual(st["groups"], {"in:4": 1, "in:6": 2})      # the right one has no strip
        self.assertEqual(st["names"], self.before["names"])
        g = m.compute_matrix(st)
        self.assertEqual((gain(g, 32, 4), gain(g, 33, 4)), (m.array("f", [m.db2lin(-6.0)])[0], 0))
        self.assertEqual((gain(g, 32, 5), gain(g, 33, 5)), (0, m.array("f", [m.db2lin(-6.0)])[0]))
        self.assertEqual(m.checked_mix(m.extract_mix(st)), m.extract_mix(st))

    def test_a_submix_changed_while_linked_unlinks_as_totalmix_does(self):
        st = self.st
        m.set_stereo(st, "in", 4, True)
        st["selected"] = 16
        m.set_strip_db(st, "in:4", -3.0)                    # the level into the phones
        st["sends"]["in"][4][2][1] = st["sends"]["in"][5][2][1] = 0.4      # a balance
        linked = m.compute_matrix(st)
        m.set_stereo(st, "in", 4, False)
        self.assertEqual(st["mute"], self.before["mute"])   # the right channel is muted again
        st["mute"]["in"][5] = False
        for c, pan in ((4, -1.0), (5, 1.0)):
            self.assertEqual(st["sends"]["in"][c][16], [-3.0, pan])
            self.assertEqual(st["sends"]["in"][c][2], [self.before["sends"]["in"][4][2][0], pan])
        # the phones hear what they heard while it was linked
        for o in (32, 33):
            for c in (4, 5):
                self.assertAlmostEqual(gain(m.compute_matrix(st), o, c), gain(linked, o, c))
        # every other submix is as it was before the link
        for p in set(range(m.N_PAIRS)) - {2, 16}:
            for c in (4, 5):
                self.assertEqual(st["sends"]["in"][c][p], self.before["sends"]["in"][c][p])
        self.assertEqual(st["groups"], self.before["groups"])
        self.assertEqual(st["unlinked"], {})

    def test_a_mute_changed_while_linked_stays_on_both(self):
        st = self.st
        m.set_stereo(st, "in", 4, True)
        st["mute"]["in"][4] = st["mute"]["in"][5] = True
        m.set_stereo(st, "in", 4, False)
        self.assertEqual(st["mute"]["in"][4:6], [True, True])
        st["mute"]["in"][5] = False
        m.set_stereo(st, "in", 4, True)
        for on in (False, True):                            # unmuted, and back: not a change
            st["mute"]["in"][4] = st["mute"]["in"][5] = on
        m.set_stereo(st, "in", 4, False)
        self.assertEqual(st["mute"]["in"][4:6], [True, False])

    def test_a_pair_with_nothing_kept_unlinks_hard_left_and_right(self):
        st = self.st                                        # playback pairs start linked
        st["sends"]["play"][0][3], st["sends"]["play"][1][3] = [-9.0, 0.5], [-9.0, 0.5]
        m.set_stereo(st, "play", 0, False)
        self.assertEqual(st["sends"]["play"][0][0], [0.0, -1.0])
        self.assertEqual(st["sends"]["play"][1][0], [0.0, 1.0])
        self.assertEqual((st["sends"]["play"][0][3], st["sends"]["play"][1][3]),
                         ([-9.0, -1.0], [-9.0, 1.0]))
        # and one from a version that kept nothing
        old = copy.deepcopy(self.before)
        old["stereo"]["in"][2] = True
        old["sends"]["in"][4][16], old["sends"]["in"][5][16] = [-6.0, 0.0], [-6.0, 0.0]
        del old["unlinked"]
        st = m.upgrade_state(old)
        self.assertEqual(st["unlinked"], {})
        m.set_stereo(st, "in", 4, False)
        self.assertEqual((st["sends"]["in"][4][16], st["sends"]["in"][5][16]),
                         ([-6.0, -1.0], [-6.0, 1.0]))

    def test_asking_for_what_it_is_already_changes_nothing(self):
        m.set_stereo(self.st, "in", 4, False)
        self.assertEqual(self.st, self.before)
        m.set_stereo(self.st, "in", 4, True)
        linked = copy.deepcopy(self.st)
        m.set_stereo(self.st, "in", 4, True)
        self.assertEqual(self.st, linked)

    def test_solo_follows_the_left_channel(self):
        self.st["solo"]["in"][4] = True
        m.set_stereo(self.st, "in", 4, True)
        self.assertEqual(self.st["solo"]["in"][4:6], [True, True])

    def test_what_is_kept_is_part_of_the_mix(self):
        """A preset or mix file made while linked unlinks to the same state anywhere."""
        m.set_stereo(self.st, "in", 4, True)
        mix = json.loads(json.dumps(m.extract_mix(self.st)))        # through a file
        other = m.apply_mix(m.default_state(16), mix)
        m.set_stereo(other, "in", 4, False)
        self.assertEqual(m.extract_mix(other), m.extract_mix(self.before))

    def test_a_mix_from_before_takes_the_place_of_what_was_kept(self):
        m.set_stereo(self.st, "in", 4, True)
        old = {k: v for k, v in m.extract_mix(self.st).items() if k != "unlinked"}
        st = m.apply_mix(copy.deepcopy(self.st), old)
        self.assertEqual(st["unlinked"], {})
        # a part of a mix that says nothing about links leaves it alone
        st = m.apply_mix(copy.deepcopy(self.st), {"out": old["out"]})
        self.assertEqual(st["unlinked"], self.st["unlinked"])

    def test_nothing_is_kept_for_channels_the_interface_lacks(self):
        st = self.st
        m.set_stereo(st, "in", 4, True)
        st["sends"]["in"][8][3] = [-6.0, 0.0]
        m.set_stereo(st, "in", 8, True)
        have = {"in": set(range(8)), "play": set(range(8)), "out": {0, 1, 32, 33}}
        m.clear_sends_outside(st, have.__getitem__)
        self.assertEqual(list(st["unlinked"]), ["in:4"])
        m.set_stereo(st, "in", 4, False)
        for c in (4, 5):
            for p, send in enumerate(st["sends"]["in"][c]):
                self.assertEqual(send, self.before["sends"]["in"][c][p] if p in (0, 16)
                                 else [None, 0.0])

    def test_damaged_keeping_is_dropped_on_repair(self):
        m.set_stereo(self.st, "in", 4, True)
        mix = m.extract_mix(self.st)
        mix["unlinked"]["in:6"] = {"group": 7}
        mix["unlinked"]["in:4"]["sends"][1][3] = "x"
        mix["unlinked"]["in:4"]["mute"] = [True]
        fixed = m.checked_mix(mix, strict=False)["unlinked"]
        self.assertEqual(list(fixed), ["in:4"])
        self.assertEqual(fixed["in:4"]["sends"][1][3], [None, 0.0])
        self.assertEqual(fixed["in:4"]["sends"][1][16], [-12.0, 0.25])
        self.assertEqual(fixed["in:4"]["mute"], [True, False])


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

    def test_upgrade_drops_mixer_mode(self):
        old = m.default_state()
        old["mixer_mode"] = "software"
        self.assertNotIn("mixer_mode", m.upgrade_state(old))

    def test_solo_resets_on_load(self):
        old = m.default_state()
        old["solo"]["in"][3] = True
        self.assertFalse(m.any_solo(m.upgrade_state(old)))
        old = m.default_state()
        del old["solo"]
        self.assertFalse(m.any_solo(m.upgrade_state(old)))

    def test_solo_is_not_part_of_a_mix(self):
        st = m.default_state()
        st["solo"]["in"][0] = True
        self.assertNotIn("solo", m.extract_mix(st))


class CheckedMixTest(unittest.TestCase):
    def test_a_good_mix_comes_back_as_an_equal_copy(self):
        st = m.default_state()
        st["sends"]["in"][3][5] = [-7.5, 0.25]
        st["out"][1] = {"gain": None, "mute": True}
        st["groups"] = {"in:3": 2, "out:17": 4}
        mix = m.extract_mix(st)
        checked = m.checked_mix(mix)
        self.assertEqual(checked, mix)
        checked["sends"]["in"][3][5][0] = 0.0
        self.assertEqual(mix["sends"]["in"][3][5][0], -7.5)

    def test_only_the_parts_given(self):
        self.assertEqual(m.checked_mix({}), {})
        self.assertEqual(m.checked_mix({"groups": {"in:0": 1}, "names": {"in:0": "x"}}),
                         {"groups": {"in:0": 1}})

    def test_levels_and_pans_are_held_to_the_controls_range(self):
        mix = m.extract_mix(m.default_state())
        mix["sends"]["in"][0][0] = [500, 9]
        mix["sends"]["in"][1][0] = [-200.0, -9.0]
        mix["out"][0]["gain"] = 40.0
        mix["sends"]["in"][2][0] = [m.NEG_INF, 0.0]        # -inf is off, as None is
        mix["out"][1]["gain"] = m.NEG_INF
        checked = m.checked_mix(mix)
        self.assertEqual(checked["sends"]["in"][2][0], [None, 0.0])
        self.assertIsNone(checked["out"][1]["gain"])
        self.assertEqual(checked["sends"]["in"][0][0], [m.FADER_MAX_DB, 1.0])
        self.assertEqual(checked["sends"]["in"][1][0], [-200.0, -1.0])
        self.assertEqual(checked["out"][0]["gain"], m.FADER_MAX_DB)

    def test_longer_lists_are_cut(self):
        mix = m.extract_mix(m.default_state())
        mix["mute"]["in"] += [True] * 4
        mix["sends"]["in"] += [[[0.0, 0.0]] * (m.N_PAIRS + 2)]
        mix["sends"]["in"][0] += [[0.0, 0.0]]
        mix["out"] += [{"gain": 0.0, "mute": False}]
        self.assertEqual(m.checked_mix(mix), m.extract_mix(m.default_state()))

    def test_damage_is_refused(self):
        with self.assertRaises(ValueError):
            m.checked_mix([1, 2])
        for what, mix in damaged_mixes():
            with self.subTest(what), self.assertRaises(ValueError):
                m.checked_mix(mix)

    def test_damage_is_repaired_when_not_strict(self):
        for what, mix in damaged_mixes():
            with self.subTest(what):
                st = dict(m.default_state(), **m.checked_mix(mix, strict=False))
                self.assertEqual(m.checked_mix(st), m.extract_mix(st))   # now a good mix
                m.compute_matrix(st)
                m.compute_out_gains(st)

    def test_repair_keeps_what_is_good(self):
        mix = m.extract_mix(m.default_state())
        mix["sends"]["in"][0][0] = [-6.0, 0.5]
        mix["sends"]["in"][0][1] = "x"
        mix["out"][3] = {"gain": -12.0, "mute": True}
        mix["out"][4] = None
        mix["groups"] = {"in:0": 2, "bogus": 1}
        mix["mute"] = "x"
        fixed = m.checked_mix(mix, strict=False)
        self.assertEqual(fixed["sends"]["in"][0][:2], [[-6.0, 0.5], [None, 0.0]])
        self.assertEqual(fixed["out"][3:5], [{"gain": -12.0, "mute": True},
                                             {"gain": 0.0, "mute": False}])
        self.assertEqual(fixed["groups"], {"in:0": 2})
        self.assertEqual(fixed["mute"], m.default_state()["mute"])

    def test_apply_leaves_the_state_alone_when_the_mix_is_damaged(self):
        for what, mix in damaged_mixes():
            with self.subTest(what):
                st = m.default_state()
                st["sends"]["in"][0][0] = [-6.0, 0.0]
                before = copy.deepcopy(st)
                with self.assertRaises(ValueError):
                    m.apply_mix(st, mix)
                self.assertEqual(st, before)

    def test_strip_keys(self):
        for key in ("in:0", "in:35", "play:35", "out:17"):
            self.assertTrue(m.valid_key(key), key)
        for key in ("in:36", "out:18", "in:-1", "in:", "in", "bus:1", "in:1:2", "in:x", "in: 1",
                    7, None, ["in:0"]):
            self.assertFalse(m.valid_key(key), key)


class DamagedStateTest(unittest.TestCase):
    """upgrade_state turns whatever a damaged state.json holds into a state the window opens."""

    def usable(self, st):
        self.assertEqual(m.checked_mix(st), m.extract_mix(st))
        m.compute_matrix(st)
        m.compute_out_gains(st)
        m.get_strip_db(st, "in:0")
        for key in st["groups"]:
            m.group_follow(st, key, 0.0, -3.0)
        return st

    def test_not_a_state(self):
        for st in ([1, 2, 3], "x", 5, None, {}, {"out": []}):
            with self.subTest(st=st):
                self.assertEqual(m.upgrade_state(copy.deepcopy(st), 3), m.default_state(3))
                with self.assertRaises(ValueError):
                    m.upgrade_state(st, 3, strict=True)

    def test_damaged_mix(self):
        for what, mix in damaged_mixes():
            with self.subTest(what):
                st = dict(m.default_state(), **mix)
                with self.assertRaises(ValueError):
                    m.upgrade_state(copy.deepcopy(st), strict=True)
                self.usable(m.upgrade_state(st))

    def test_selected_out_of_range(self):
        for value in (99, -1, m.N_PAIRS, "x", None, 1.5, True, [1]):
            with self.subTest(value=value):
                st = m.default_state()
                st["selected"] = value
                self.assertEqual(self.usable(m.upgrade_state(st, 8))["selected"], 8)
        st = m.default_state()
        st["selected"] = 5
        self.assertEqual(m.upgrade_state(st, 8, strict=True)["selected"], 5)

    def test_display_settings(self):
        for value in ("x", 7, None, [1], {"a": 1}, 1.5):
            with self.subTest(value=value):
                st = m.default_state()
                for key in ("tab", "active_slot", "names", "colors", "icons", "custom_names",
                            "hidden", "hidden_strips"):
                    st[key] = copy.deepcopy(value)
                st = self.usable(m.upgrade_state(st, strict=True))
                self.assertEqual((st["tab"], st["names"], st["colors"], st["icons"],
                                  st["custom_names"], st["hidden"], st["hidden_strips"]),
                                 (0, {}, {}, {}, True, [], []))
                self.assertEqual(st["active_slot"], 7 if value == 7 else None)

    def test_good_display_settings_are_kept(self):
        st = m.default_state()
        st.update(tab=1, active_slot=7, custom_names=False, hidden=["AD3", 5],
                  hidden_strips=["in:4", "zzz", 3], names={"in:0": "Vocal", "in:1": 5, "x": "y"},
                  colors={"out:16": "#ff0000", "out:99": "#00ff00"}, icons={"play:2": "mic"})
        st = m.upgrade_state(st, strict=True)
        self.assertEqual((st["tab"], st["active_slot"], st["custom_names"]), (1, 7, False))
        self.assertEqual((st["hidden"], st["hidden_strips"]), (["AD3"], ["in:4"]))
        self.assertEqual((st["names"], st["colors"], st["icons"]),
                         ({"in:0": "Vocal"}, {"out:16": "#ff0000"}, {"play:2": "mic"}))


class MissingChannelsTest(unittest.TestCase):
    def test_sends_outside_the_interface_are_switched_off(self):
        have = {"in": {0, 1}, "play": {0, 1, 2, 3}, "out": {0, 1, 4, 5}}
        st = m.default_state()
        for c in (0, 1, 2):
            st["sends"]["in"][c] = [[-6.0, 0.5] for _ in range(m.N_PAIRS)]
        m.clear_sends_outside(st, have.__getitem__)
        self.assertEqual(st["sends"]["in"][0][:4], [[-6.0, 0.5], [None, 0.0], [-6.0, 0.5],
                                                    [None, 0.0]])
        self.assertEqual(st["sends"]["in"][2], [[None, 0.0]] * m.N_PAIRS)
        self.assertEqual(st["sends"]["play"][0][0], [0.0, 0.0])
        self.assertEqual(st["sends"]["play"][2][1], [None, 0.0])    # pair 1 is not there
        self.assertEqual(st["sends"]["play"][8][4], [None, 0.0])    # playback 9 is not there
        g = m.compute_matrix(st)
        for o in range(m.N_OUT):
            for s in range(m.N_SRC):
                if gain(g, o, s):
                    kind, c = ("in", s) if s < m.N_IN else ("play", s - m.N_IN)
                    self.assertTrue(c in have[kind] and o in have["out"], (o, s))

    def test_a_fader_group_cant_bring_a_missing_channels_send_back(self):
        have = {"in": {0, 1}, "play": {0, 1}, "out": {0, 1, 4, 5}}
        st = m.default_state(0)
        st["groups"] = {"in:0": 1, "in:2": 1, "play:0": 2, "play:7": 2, "out:0": 3, "out:1": 3,
                        "out:2": 3}
        m.clear_sends_outside(st, have.__getitem__)
        self.assertEqual(st["groups"], {"in:0": 1, "play:0": 2, "out:0": 3, "out:2": 3})
        m.set_strip_db(st, "in:0", 0.0)
        self.assertEqual(m.group_follow(st, "in:0", m.NEG_INF, 0.0), {})
        self.assertEqual(st["sends"]["in"][2][0], [None, 0.0])


class LabelTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(m.chan_label(0, 32, False), "AD1 1")
        self.assertEqual(m.chan_label(31, 32, False), "AD4 8")
        self.assertEqual(m.chan_label(32, 32, True), "Ph L")
        self.assertEqual(m.pair_label(32, 32, True), "Phones")
        self.assertEqual(m.chan_label(4, 16, False), "AD2 1")   # 2x speed: 4 channels per port


if __name__ == "__main__":
    unittest.main()
