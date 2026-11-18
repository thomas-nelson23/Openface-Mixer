"""TotalMix's submix tools in the model: copy, paste and mirror a submix, copy, paste and clear
a channel's sends, Trim Gains, Cue, mute groups and solo groups. Pure functions, on random
states of every device."""
import copy
import random
import unittest

from openface_mixer import config, control_room, model as m, osc_remote
from openface_mixer.devices import DEVICES


def busy_state(rng, dev):
    """A state on dev's channels with sends at any level and pan, mutes, and stereo links made
    with set_stereo, so that what they keep for unlinking differs from the linked sends."""
    st = m.default_state(dev.phones_pair(1))
    pairs = sorted({c // 2 for c in dev.all_channels("out")})
    for kind in ("in", "play"):
        st["stereo"][kind] = [False] * len(st["stereo"][kind])
        for c in dev.all_channels(kind):
            st["mute"][kind][c] = rng.random() < 0.1
            for p in rng.sample(pairs, min(len(pairs), 8)):
                st["sends"][kind][c][p] = [rng.choice((None, 0.0, 6.0, round(rng.uniform(-80, 6),
                                                                                 2))),
                                           rng.choice((0.0, -1.0, 1.0, round(rng.uniform(-1, 1),
                                                                             3)))]
        for c in dev.all_channels(kind):
            if c % 2 == 0 and c + 1 in dev.all_channels(kind) and rng.random() < 0.5:
                m.set_stereo(st, kind, c, True)
                p = rng.choice(pairs)
                st["sends"][kind][c][p][0] = st["sends"][kind][c + 1][p][0] = -3.0
    return st


def rows(gains, o):
    return list(gains[o * m.N_SRC:(o + 1) * m.N_SRC])


def every_device(seed):
    rng = random.Random(seed)
    for dev in DEVICES.values():
        yield dev, busy_state(rng, dev), sorted({c // 2 for c in dev.all_channels("out")}), rng


class CopyPasteMixTest(unittest.TestCase):
    def test_paste_then_copy_back_is_the_copy(self):
        for dev, st, pairs, rng in every_device(1):
            with self.subTest(dev=dev.key):
                a, b = rng.sample(pairs, 2)
                mix = m.copy_mix(st, a)
                m.paste_mix(st, b, mix)
                self.assertEqual(m.copy_mix(st, b), mix)
                self.assertEqual(m.copy_mix(st, a), mix)        # the source is untouched

    def test_the_pasted_output_plays_the_whole_submix(self):
        for dev, st, pairs, rng in every_device(2):
            with self.subTest(dev=dev.key):
                a, b = rng.sample(pairs, 2)
                others = [p for p in range(m.N_PAIRS) if p not in (a, b)]
                before = m.compute_matrix(st, solo=False)
                m.paste_mix(st, b, m.copy_mix(st, a))
                after = m.compute_matrix(st, solo=False)
                for side in (0, 1):
                    self.assertEqual(rows(after, 2 * b + side), rows(after, 2 * a + side))
                for p in others:
                    self.assertEqual(rows(after, 2 * p), rows(before, 2 * p))

    def test_unlinking_after_a_paste_gives_what_unlinking_the_source_gives(self):
        for dev, st, pairs, rng in every_device(3):
            with self.subTest(dev=dev.key):
                a, b = rng.sample(pairs, 2)
                m.paste_mix(st, b, m.copy_mix(st, a))
                for key in list(st["unlinked"]):
                    kind, c = m.parse_key(key)
                    m.set_stereo(st, kind, c, False)
                    for ch in (c, c + 1):
                        self.assertEqual(st["sends"][kind][ch][b], st["sends"][kind][ch][a])

    def test_a_pair_linked_after_the_copy_keeps_the_pasted_sends(self):
        st = m.default_state()
        st["sends"]["in"][0][0] = [-6.0, 0.0]
        st["sends"]["in"][1][0] = [-6.0, 0.0]
        mix = m.copy_mix(st, 0)
        m.set_stereo(st, "in", 0, True)
        m.paste_mix(st, 3, mix)
        m.set_stereo(st, "in", 0, False)
        self.assertEqual([st["sends"]["in"][c][3] for c in (0, 1)], [[-6.0, 0.0]] * 2)


class MirrorTest(unittest.TestCase):
    def test_a_mirror_plays_and_follows_its_source(self):
        for dev, st, pairs, rng in every_device(4):
            with self.subTest(dev=dev.key):
                src, dst = rng.sample(pairs, 2)
                st["out"][dst]["gain"] = -12.0
                self.assertTrue(m.set_mirror(st, dst, src))
                g = m.compute_matrix(st, solo=False)
                for side in (0, 1):
                    self.assertEqual(rows(g, 2 * dst + side), rows(g, 2 * src + side))
                st["selected"] = dst                # the mirror's faders edit the source
                m.set_strip_db(st, "in:0", -20.0)
                self.assertEqual(st["sends"]["in"][0][src][0], -20.0)
                self.assertEqual(m.get_strip_db(st, "in:0"), -20.0)
                self.assertAlmostEqual(m.compute_out_gains(st)[2 * dst], m.db2lin(-12.0))   # own

    def test_removing_a_mirror_keeps_what_it_played(self):
        for dev, st, pairs, rng in every_device(5):
            with self.subTest(dev=dev.key):
                src, dst = rng.sample(pairs, 2)
                m.set_mirror(st, dst, src)
                before = m.compute_matrix(st, solo=False)
                m.remove_mirror(st, dst)
                self.assertEqual(st["mirror"], {})
                self.assertEqual(m.compute_matrix(st, solo=False), before)
                self.assertEqual(m.copy_mix(st, dst), m.copy_mix(st, src))
                m.set_strip_db(dict(st, selected=src), "in:0", 3.0)     # now apart
                self.assertNotEqual(st["sends"]["in"][0][dst][0], 3.0)

    def test_no_chains_and_no_loops(self):
        st = m.default_state()
        self.assertTrue(m.set_mirror(st, 1, 0))
        self.assertTrue(m.set_mirror(st, 2, 1))         # a mirror of a mirror plays the source
        self.assertEqual(st["mirror"], {"out:1": 0, "out:2": 0})
        self.assertFalse(m.set_mirror(st, 0, 2))        # 2 plays 0 already
        self.assertFalse(m.set_mirror(st, 0, 0))
        self.assertTrue(m.set_mirror(st, 0, 5))         # those that mirrored 0 now follow 5
        self.assertEqual(st["mirror"], {"out:1": 5, "out:2": 5, "out:0": 5})
        self.assertEqual(m.checked_mix(m.extract_mix(st))["mirror"], st["mirror"])

    def test_pasting_into_a_mirror_ends_it(self):
        st = m.default_state()
        m.set_mirror(st, 1, 0)
        st["sends"]["in"][0][4] = [-9.0, 0.5]
        m.paste_mix(st, 1, m.copy_mix(st, 4))
        self.assertEqual(st["mirror"], {})
        self.assertEqual(st["sends"]["in"][0][1], [-9.0, 0.5])

    def test_solo_silences_every_output_that_plays_the_submix(self):
        st = m.default_state()
        st["sends"]["in"][0][0] = [0.0, 0.0]
        st["sends"]["in"][1][0] = [0.0, 0.0]
        m.set_mirror(st, 1, 0)
        st["selected"] = 1
        st["solo"]["in"][0] = True
        g = m.compute_matrix(st)
        for o in (0, 1, 2, 3):
            self.assertEqual(g[o * m.N_SRC + 1], 0.0)
            self.assertGreater(g[o * m.N_SRC + 0], 0.0)

    def test_the_remote_shows_and_edits_the_source(self):
        dev = DEVICES["digiface"]
        st = m.default_state()
        st["sends"]["in"][0][0] = [-10.0, 0.0]
        m.set_mirror(st, 1, 0)
        snap = osc_remote.snapshot(st, dev, 1, set(), True)
        self.assertEqual(snap["/mix/in/0/2/faderlin"], snap["/mix/in/0/0/faderlin"])
        osc_remote.apply(st, dev, 1, "/mix/in/0/2/fader", [-4.0])
        self.assertEqual(st["sends"]["in"][0][0][0], -4.0)


class ChannelSendsTest(unittest.TestCase):
    def test_paste_then_copy_back_is_the_copy(self):
        for dev, st, _pairs, rng in every_device(6):
            for kind in ("in", "play"):
                with self.subTest(dev=dev.key, kind=kind):
                    chans = sorted(dev.all_channels(kind))
                    a, b = rng.sample([c for c in chans if c % 2 == 0 and c + 1 in chans], 2)
                    for c in (a, b):                    # two pairs linked alike
                        m.set_stereo(st, kind, c, rng.random() < 0.5)
                    m.set_stereo(st, kind, b, st["stereo"][kind][a // 2])
                    sends = m.copy_sends(st, kind, a)
                    m.paste_sends(st, kind, b, sends)
                    self.assertEqual(m.copy_sends(st, kind, b), sends)

    def test_a_linked_pair_copies_both_sides_and_what_it_keeps(self):
        st = m.default_state()
        for c, db in ((0, -6.0), (1, -12.0)):
            st["sends"]["in"][c][2] = [db, 0.0]
        m.set_stereo(st, "in", 0, True)
        m.set_stereo(st, "in", 4, True)
        m.paste_sends(st, "in", 4, m.copy_sends(st, "in", 0))
        self.assertEqual(st["sends"]["in"][4:6], st["sends"]["in"][0:2])
        m.set_stereo(st, "in", 4, False)
        self.assertEqual([st["sends"]["in"][c][2] for c in (4, 5)], [[-6.0, 0.0], [-12.0, 0.0]])

    def test_mono_and_pair(self):
        st = m.default_state()
        st["sends"]["in"][0][2] = [-6.0, -0.5]
        m.set_stereo(st, "in", 4, True)
        m.paste_sends(st, "in", 4, m.copy_sends(st, "in", 0))      # both sides take it
        self.assertEqual([st["sends"]["in"][c][2] for c in (4, 5)], [[-6.0, -0.5]] * 2)
        st["sends"]["in"][5][2] = [-1.0, -0.5]
        m.paste_sends(st, "in", 9, m.copy_sends(st, "in", 4))      # a mono channel: the left
        self.assertEqual(st["sends"]["in"][9][2], [-6.0, -0.5])

    def test_clear_switches_off_every_send_and_what_is_kept(self):
        for dev, st, _pairs, _rng in every_device(7):
            with self.subTest(dev=dev.key):
                for key in list(st["unlinked"])[:3] + ["play:0"]:
                    kind, c = m.parse_key(key)
                    m.clear_sends(st, kind, c)
                    g = m.compute_matrix(st, solo=False)
                    src = c + (0 if kind == "in" else m.N_IN)
                    self.assertFalse(any(g[o * m.N_SRC + s] for o in range(m.N_OUT)
                                         for s in (src, src + 1)
                                         if s - src < len(m.strip_channels(st, kind, c))))
                    if st["stereo"][kind][c // 2]:
                        m.set_stereo(st, kind, c, False)
                        self.assertTrue(all(s[0] is None for ch in (c, c + 1)
                                            for s in st["sends"][kind][ch]))


class TrimTest(unittest.TestCase):
    def test_every_send_moves_by_the_same_amount(self):
        for dev, st, _pairs, rng in every_device(8):
            with self.subTest(dev=dev.key):
                key = "play:0"
                st["selected"] = 0
                m.set_strip_db(st, key, -20.0)
                before = copy.deepcopy(st["sends"]["play"][0])
                delta = rng.choice((-3.5, 2.25, -10.0))
                assert m.trim_sends(st, key, -20.0, -20.0 + delta) == -20.0 + delta
                for p, (old, new) in enumerate(zip(before, st["sends"]["play"][0])):
                    if p == 0:
                        continue                        # the fader's own: set by set_strip_db
                    if old[0] is None:
                        self.assertIsNone(new[0])       # off stays off
                    else:
                        want = max(m.FADER_MIN_DB, min(m.FADER_MAX_DB, old[0] + delta))
                        self.assertAlmostEqual(new[0], want, places=6)
                    self.assertEqual(new[1], old[1])    # pans stay

    def test_differences_stay_away_from_the_ends(self):
        st = m.default_state()
        levels = [-30.0, -12.5, -20.0, None, -6.0]
        for p, db in enumerate(levels):
            st["sends"]["in"][3][p] = [db, 0.0]
        st["selected"] = 0
        for new in (-27.0, -35.5, -30.0):
            old = m.get_strip_db(st, "in:3")
            m.set_strip_db(st, "in:3", new)
            m.trim_sends(st, "in:3", old, m.get_strip_db(st, "in:3"))
        got = [s[0] for s in st["sends"]["in"][3][:5]]
        self.assertEqual(got, levels)

    def test_at_the_top_a_send_stops_and_loses_the_rest(self):
        st = m.default_state()
        st["sends"]["in"][0][0] = [0.0, 0.0]
        st["sends"]["in"][0][1] = [4.0, 0.0]
        st["selected"] = 0
        m.set_strip_db(st, "in:0", 4.0)
        m.trim_sends(st, "in:0", 0.0, 4.0)
        self.assertEqual(st["sends"]["in"][0][1][0], 6.0)
        m.set_strip_db(st, "in:0", 0.0)
        m.trim_sends(st, "in:0", 4.0, 0.0)
        self.assertEqual(st["sends"]["in"][0][1][0], 2.0)

    def test_down_to_the_bottom_and_back(self):
        st = m.default_state()
        st["sends"]["in"][0][0] = [-10.0, 0.0]
        st["sends"]["in"][0][1] = [-20.0, 0.0]
        st["selected"] = 0
        m.set_strip_db(st, "in:0", m.NEG_INF)
        self.assertEqual(m.trim_sends(st, "in:0", -10.0, m.NEG_INF), m.FADER_MIN_DB)
        self.assertEqual([s[0] for s in st["sends"]["in"][0][:2]], [-80.0, -80.0])
        m.set_strip_db(st, "in:0", -10.0)
        m.trim_sends(st, "in:0", -80.0, -10.0)
        self.assertEqual([s[0] for s in st["sends"]["in"][0][:2]], [-10.0, -10.0])

    def test_a_send_that_was_off_moves_nothing_else(self):
        st = m.default_state()
        st["sends"]["in"][0][1] = [0.0, 0.0]
        st["selected"] = 0
        m.set_strip_db(st, "in:0", -10.0)
        self.assertEqual(m.trim_sends(st, "in:0", m.NEG_INF, -10.0), -10.0)
        self.assertEqual(st["sends"]["in"][0][1], [0.0, 0.0])
        self.assertEqual(m.trim_sends(st, "out:0", 0.0, -10.0), -10.0)    # outputs: no trim

    def test_a_linked_pair_trims_what_it_keeps(self):
        st = m.default_state()
        st["sends"]["in"][0][0], st["sends"]["in"][1][0] = [-6.0, -1.0], [-9.0, 1.0]
        st["sends"]["in"][0][4], st["sends"]["in"][1][4] = [-6.0, 0.0], [-6.0, 0.0]
        m.set_stereo(st, "in", 0, True)
        st["selected"] = 4
        m.set_strip_db(st, "in:0", -2.0)
        m.trim_sends(st, "in:0", -6.0, -2.0)
        m.set_stereo(st, "in", 0, False)
        self.assertEqual((st["sends"]["in"][0][0], st["sends"]["in"][1][0]),
                         ([-2.0, -1.0], [-5.0, 1.0]))
        self.assertEqual((st["sends"]["in"][0][4], st["sends"]["in"][1][4]),
                         ([-2.0, 0.0], [-2.0, 0.0]))

    def test_trim_is_saved_but_is_not_part_of_a_mix(self):
        st = m.default_state()
        st["trim"] = True
        self.assertNotIn("trim", m.extract_mix(st))
        self.assertTrue(m.upgrade_state(copy.deepcopy(st), strict=True)["trim"])


class CueTest(unittest.TestCase):
    def test_main_out_plays_the_cued_submix(self):
        for dev, st, pairs, rng in every_device(9):
            with self.subTest(dev=dev.key):
                cr = control_room.load(st, dev.phones_pair(1))
                dev.upgrade_settings(st)
                cue = rng.choice([p for p in pairs if p != cr["main"]])
                saved = copy.deepcopy(st)
                plain, plain_out = control_room.apply(st, m.compute_matrix(st),
                                                      m.compute_out_gains(st))
                g, out = control_room.apply(st, m.compute_matrix(st), m.compute_out_gains(st),
                                            cue=cue)
                main = cr["main"]
                for side in (0, 1):
                    self.assertEqual(rows(g, 2 * main + side), rows(plain, 2 * cue + side))
                    self.assertEqual(rows(g, 2 * cue + side), rows(plain, 2 * cue + side))
                self.assertEqual(out, plain_out)           # Main Out's own level stays
                self.assertEqual(st, saved)                # nothing in the state changes
                self.assertEqual(config.matrix_file_bytes(st, dev),
                                 config.matrix_file_bytes(saved, dev))

    def test_cueing_main_out_changes_nothing(self):
        st = m.default_state()
        plain = control_room.apply(st, m.compute_matrix(st), m.compute_out_gains(st))
        self.assertEqual(control_room.apply(st, m.compute_matrix(st), m.compute_out_gains(st),
                                            cue=0), plain)

    def test_dim_and_talkback_work_while_cueing(self):
        st = m.default_state()
        st["sends"]["in"][4][3] = [0.0, -1.0]
        cr = control_room.control_room(st)
        cr.update(dim=True, talkback=True, talkback_src=7, talkback_dests=[0])
        g, out = control_room.apply(st, m.compute_matrix(st), m.compute_out_gains(st), cue=3)
        self.assertAlmostEqual(out[0], m.db2lin(cr["dim_db"]))
        self.assertAlmostEqual(g[0 * m.N_SRC + 4], m.db2lin(cr["talkback_dim_db"]))  # dimmed
        self.assertEqual(g[0 * m.N_SRC + 7], 1.0)                                  # the mic

    def test_speaker_b_plays_the_cue(self):
        st = m.default_state()
        st["sends"]["in"][4][3] = [0.0, -1.0]
        control_room.control_room(st).update(main_b=1, speaker_b=True)
        g, _out = control_room.apply(st, m.compute_matrix(st), m.compute_out_gains(st), cue=3)
        self.assertEqual(g[2 * m.N_SRC + 4], 1.0)
        self.assertEqual(g[0 * m.N_SRC + 4], 0.0)


class MuteGroupTest(unittest.TestCase):
    def test_a_group_mutes_its_sends_in_their_submix_only(self):
        st = m.default_state()
        for p in (0, 1):
            st["sends"]["in"][2][p] = [0.0, -1.0]
            st["sends"]["in"][3][p] = [0.0, -1.0]
        m.set_mute_group(st, "in:2", 1, 3)
        g = m.compute_matrix(st)
        self.assertEqual(g[2 * m.N_SRC + 2], 1.0)            # off: nothing changes
        st["mute_groups"][2]["on"] = True
        g = m.compute_matrix(st)
        self.assertEqual(g[2 * m.N_SRC + 2], 0.0)            # in pair 1
        self.assertEqual(g[0 * m.N_SRC + 2], 1.0)            # not in pair 0
        self.assertEqual(g[2 * m.N_SRC + 3], 1.0)            # nor another channel
        self.assertFalse(st["mute"]["in"][2])                # the channel's own mute is apart
        st["mute_groups"][2]["on"] = False
        st["mute"]["in"][2] = True
        self.assertEqual(m.compute_matrix(st)[0 * m.N_SRC + 2], 0.0)

    def test_a_linked_pair_is_muted_on_both_sides(self):
        st = m.default_state()
        m.set_stereo(st, "play", 4, True)
        m.set_mute_group(st, "play:4", 2, 1)
        st["mute_groups"][0]["on"] = True
        g = m.compute_matrix(st)
        self.assertEqual(g[4 * m.N_SRC + m.N_IN + 4], 0.0)
        self.assertEqual(g[5 * m.N_SRC + m.N_IN + 5], 0.0)

    def test_a_right_channel_in_a_group_waits_while_its_pair_is_linked(self):
        st = m.default_state()
        st["sends"]["in"][5][2] = [0.0, 1.0]
        m.set_mute_group(st, "in:5", 2, 1)
        st["mute_groups"][0]["on"] = True
        m.set_stereo(st, "in", 4, True)             # the pair shows no group: neither side
        st["sends"]["in"][4][2] = st["sends"]["in"][5][2] = [0.0, 0.0]
        g = m.compute_matrix(st)
        self.assertEqual((g[4 * m.N_SRC + 4], g[5 * m.N_SRC + 5]), (1.0, 1.0))
        m.set_stereo(st, "in", 4, False)
        self.assertEqual(m.compute_matrix(st)[5 * m.N_SRC + 5], 0.0)

    def test_one_group_per_send_and_an_empty_group_is_off(self):
        st = m.default_state()
        m.set_mute_group(st, "in:0", 4, 1)
        st["mute_groups"][0]["on"] = True
        m.set_mute_group(st, "in:0", 4, 2)
        self.assertEqual(m.mute_group(st, "in:0", 4), 2)
        self.assertEqual(st["mute_groups"][0], {"on": False, "sends": []})
        m.set_mute_group(st, "in:0", 5, 2)
        self.assertEqual(st["mute_groups"][1]["sends"], [["in:0", 4], ["in:0", 5]])
        m.set_mute_group(st, "in:0", 4, 0)
        self.assertEqual(m.mute_group(st, "in:0", 4), 0)

    def test_groups_are_part_of_the_mix(self):
        st = m.default_state()
        m.set_mute_group(st, "in:0", 4, 1)
        st["mute_groups"][0]["on"] = True
        st["solo_groups"] = {"in:2": 3, "play:0": 1}
        mix = m.checked_mix(m.extract_mix(st))
        self.assertEqual((mix["mute_groups"], mix["solo_groups"]),
                         (st["mute_groups"], st["solo_groups"]))

    def test_a_mix_from_before_has_no_mirrors_or_groups(self):
        st = m.default_state()
        m.set_mirror(st, 2, 0)
        m.set_mute_group(st, "in:0", 4, 1)
        st["solo_groups"] = {"in:2": 3}
        old = {k: v for k, v in m.extract_mix(m.default_state()).items()
               if k not in ("mirror", "mute_groups", "solo_groups")}
        m.apply_mix(st, old)
        self.assertEqual((st["mirror"], st["solo_groups"]), ({}, {}))
        self.assertEqual(st["mute_groups"], m.default_state()["mute_groups"])

    def test_damaged_parts_are_dropped_on_repair(self):
        mix = m.extract_mix(m.default_state())
        mix["mirror"] = {"out:1": 0, "out:0": 0, "out:3": 1, "out:5": "x"}
        mix["mute_groups"][0] = {"on": True, "sends": [["in:0", 2], ["out:0", 2], "x"]}
        mix["mute_groups"][1] = "x"
        mix["solo_groups"] = {"in:1": 2, "out:0": 1}
        fixed = m.checked_mix(mix, strict=False)
        self.assertEqual(fixed["mirror"], {"out:1": 0})
        self.assertEqual(fixed["mute_groups"][:2], [{"on": True, "sends": [["in:0", 2]]},
                                                    {"on": False, "sends": []}])
        self.assertEqual(fixed["solo_groups"], {"in:1": 2})

    def test_nothing_is_kept_for_channels_the_interface_lacks(self):
        have = {"in": {0, 1}, "play": {0, 1}, "out": {0, 1, 2, 3}}
        st = m.default_state()
        st["mirror"] = {"out:1": 0, "out:5": 0, "out:0": 7}
        m.set_mute_group(st, "in:0", 1, 1)
        m.set_mute_group(st, "in:9", 1, 1)
        m.set_mute_group(st, "in:1", 6, 1)
        st["solo_groups"] = {"in:1": 1, "play:8": 2}
        m.clear_sends_outside(st, lambda kind: have[kind])
        self.assertEqual(st["mirror"], {"out:1": 0})
        self.assertEqual(st["mute_groups"][0]["sends"], [["in:0", 1]])
        self.assertEqual(st["solo_groups"], {"in:1": 1})


if __name__ == "__main__":
    unittest.main()
