"""The pan law for mono channels (constant power, as TotalMix), and the one-time conversion of
states, presets and mix files saved under the balance law before it."""
import copy
import json
import math
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import config, model as m
from openface_mixer.devices import DEVICES
from openface_mixer.presets import VERSION, PresetBank, export_mix, import_mix

HALF_POWER = math.sqrt(0.5)


def old_matrix(st):
    """The gain matrix as versions up to 5 computed it: balance law for every channel."""
    g = [0.0] * (m.N_OUT * m.N_SRC)
    for kind, base in (("in", 0), ("play", m.N_IN)):
        for c, row in enumerate(st["sends"][kind]):
            if st["mute"][kind][c]:
                continue
            linked = st["stereo"][kind][c // 2]
            for p, (db, pan) in enumerate(row):
                lin = m.db2lin(db)
                if not linked or c % 2 == 0:
                    g[2 * p * m.N_SRC + base + c] += lin * min(1.0, 1.0 - pan)
                if not linked or c % 2:
                    g[(2 * p + 1) * m.N_SRC + base + c] += lin * min(1.0, 1.0 + pan)
    return g


def old_state(rng, dev, hot=False):
    """A busy version 5 state on dev's channels: links, mutes and sends at any level and pan.
    hot: with levels up to the fader's top, which a centred send can't keep (see held())."""
    st = m.default_state(dev.phones_pair(1))
    st["version"] = 5
    pairs = sorted({c // 2 for c in dev.all_channels("out")})
    for kind in ("in", "play"):
        st["stereo"][kind] = [rng.random() < 0.4 for _ in st["stereo"][kind]]
        for c in dev.all_channels(kind):
            st["mute"][kind][c] = rng.random() < 0.1
            for p in rng.sample(pairs, min(len(pairs), 6)):
                pan = rng.choice((0.0, 0.0, -1.0, 1.0, round(rng.uniform(-1, 1), 3),
                                  rng.uniform(-1, 1)))
                top = 6.0 if hot else 6.0 - 10 * math.log10(1 + (1 - abs(pan)) ** 2)
                db = rng.choice((None, 0.0, top, round(rng.uniform(-80, top), 2),
                                 rng.uniform(-80, top)))
                st["sends"][kind][c][p] = [db, pan]
            if st["stereo"][kind][c // 2] and c % 2:      # a linked pair shares level and pan
                st["sends"][kind][c] = copy.deepcopy(st["sends"][kind][c - 1])
    return st


def saved(st):
    """st as version 5 wrote it: without what today's links keep."""
    return json.dumps({k: v for k, v in st.items() if k != "unlinked"})


def held(old, new):
    """Whether a converted send [dB, pan] is one whose level the fader can't give: it sits at
    the fader's top, lower than the old send's power by what did not fit."""
    return new[0] == m.FADER_MAX_DB and old[0] is not None and (
        old[0] + 10 * math.log10(1 + (1 - abs(old[1])) ** 2) > m.FADER_MAX_DB + 1e-9)


class LawTest(unittest.TestCase):
    def test_ends_and_centre(self):
        self.assertEqual(m.pan_gains(-1.0), (1.0, 0.0))
        self.assertEqual(m.pan_gains(1.0), (0.0, 1.0))
        left, right = m.pan_gains(0.0)
        self.assertAlmostEqual(left, HALF_POWER)
        self.assertAlmostEqual(right, HALF_POWER)
        self.assertAlmostEqual(m.lin2db(left), -3.0103, places=4)

    def test_the_power_is_the_same_at_every_position(self):
        for i in range(-64, 65):
            left, right = m.pan_gains(i / 64)
            self.assertAlmostEqual(left * left + right * right, 1.0)
            self.assertGreaterEqual(min(left, right), 0.0)

    def test_a_mono_channel_is_3_db_down_at_the_centre(self):
        st = m.default_state()
        st["sends"]["in"][0][16] = [0.0, 0.0]
        st["sends"]["in"][2][16] = [-6.0, -1.0]
        st["sends"]["in"][3][16] = [-6.0, 1.0]
        g = m.compute_matrix(st)
        self.assertAlmostEqual(g[32 * m.N_SRC], HALF_POWER, places=6)
        self.assertAlmostEqual(g[33 * m.N_SRC], HALF_POWER, places=6)
        self.assertAlmostEqual(g[32 * m.N_SRC + 2], m.db2lin(-6.0), places=6)
        self.assertEqual(g[33 * m.N_SRC + 2], 0.0)      # hard left leaves no route on the right
        self.assertEqual(g[32 * m.N_SRC + 3], 0.0)
        self.assertAlmostEqual(g[33 * m.N_SRC + 3], m.db2lin(-6.0), places=6)

    def test_a_linked_pair_keeps_its_balance(self):
        st = m.default_state()
        st["stereo"]["in"][0] = True
        for c, o, pan, want in ((0, 0, 0.0, 1.0), (1, 1, 0.0, 1.0), (0, 0, 0.5, 0.5),
                                (1, 1, 0.5, 1.0), (0, 0, -0.5, 1.0), (1, 1, -0.5, 0.5)):
            st["sends"]["in"][0][0] = st["sends"]["in"][1][0] = [0.0, pan]
            self.assertAlmostEqual(m.compute_matrix(st)[o * m.N_SRC + c], want)


class ConversionTest(unittest.TestCase):
    """What was saved under the old law sounds the same under the new one."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patch = mock.patch.object(config, "CONFIG_DIR", self.dir)
        patch.start()
        self.addCleanup(patch.stop)

    def same_matrix(self, old, new, exact=True):
        """The old state's matrix under the old law against the new state's under today's.
        Returns how many sends were held at the fader's top."""
        before, after = old_matrix(old), m.compute_matrix(new)
        n_held = 0
        for kind, base in (("in", 0), ("play", m.N_IN)):
            for c, row in enumerate(new["sends"][kind]):
                for p, send in enumerate(row):
                    was = old["sends"][kind][c][p]
                    at = [o * m.N_SRC + base + c for o in (2 * p, 2 * p + 1)]
                    if not new["stereo"][kind][c // 2] and held(was, send):
                        self.assertFalse(exact, (kind, c, p, was, send))
                        n_held += 1
                        # lower on both sides by the same amount, 3 dB at most
                        lost = was[0] + 10 * math.log10(1 + (1 - abs(was[1])) ** 2) - 6.0
                        self.assertLess(lost, 3.0104)
                        for i in at:
                            if not new["mute"][kind][c]:
                                self.assertAlmostEqual(after[i], before[i] * m.db2lin(-lost),
                                                       places=5)
                        continue
                    for i in at:
                        self.assertTrue(math.isclose(after[i], before[i], rel_tol=1e-6,
                                                     abs_tol=1e-9), (kind, c, p, was, send))
        return n_held

    def test_a_saved_state_sounds_the_same_on_every_device(self):
        rng = random.Random(6)
        for key, dev in DEVICES.items():
            for _ in range(3):
                with self.subTest(key):
                    old = old_state(rng, dev)
                    path = config.state_file(dev)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(saved(old))
                    m.clear_sends_outside(old, dev.all_channels)    # as loading does
                    new, problem = config.load_state(dev)
                    self.assertIsNone(problem)
                    self.assertEqual(new["version"], m.STATE_VERSION)
                    self.assertEqual(self.same_matrix(old, new), 0)
                    # saved and loaded again, it is not converted a second time
                    config.save_state(new, dev)
                    self.assertEqual(config.load_state(dev)[0], new)

    def test_presets_and_mix_files_sound_the_same(self):
        rng = random.Random(7)
        for key, dev in DEVICES.items():
            with self.subTest(key):
                olds = [old_state(rng, dev) for _ in range(2)]
                mixes = [{k: st[k] for k in m.MIX_KEYS if k != "unlinked"} for st in olds]
                path = self.dir / "presets.json"
                path.write_text(json.dumps({"version": 1, "slots": [
                    {"name": "A", "mix": mixes[0]}, None, {"name": "B", "mix": mixes[1]}]}))
                bank = PresetBank(path)
                for old, slot in zip(olds, (0, 2)):
                    new = m.apply_mix(m.default_state(), bank.slots[slot]["mix"])
                    self.assertEqual(self.same_matrix(old, new), 0)
                # once saved, the bank is today's and is read as it is
                bank.rename(0, "A2")
                self.assertEqual(json.loads(path.read_text())["version"], VERSION)
                self.assertEqual(PresetBank(path).slots, bank.slots)

                # an exported mix, and previous-mix.json, which is one
                for name in ("mix.json", "previous-mix.json"):
                    path = self.dir / name
                    path.write_text(json.dumps({"format": "openface-mixer-mix", "version": 1,
                                                "name": "Old", "mix": mixes[0]}))
                    _name, mix = import_mix(path)
                    self.assertEqual(self.same_matrix(olds[0], m.apply_mix(m.default_state(),
                                                                           mix)), 0)
                    export_mix(path, "New", mix)
                    self.assertEqual(import_mix(path), ("New", mix))

    def test_a_level_the_fader_cant_give_is_held_at_its_top(self):
        rng = random.Random(8)
        n_held = 0
        for dev in DEVICES.values():
            old = old_state(rng, dev, hot=True)
            new = m.upgrade_state(copy.deepcopy(old))
            n_held += self.same_matrix(old, new, exact=False)
            for kind in ("in", "play"):
                for row in new["sends"][kind]:
                    self.assertTrue(all(db is None or db <= m.FADER_MAX_DB for db, _pan in row))
        self.assertGreater(n_held, 100)
        # the plainest case: +6 dB at the centre was +6 dB on each side and is now +3 dB
        st = m.default_state()
        st["version"] = 5
        st["sends"]["in"][0][0] = [6.0, 0.0]
        st["sends"]["in"][1][0] = [2.9, 0.0]            # this one still fits
        st = m.upgrade_state(st)
        self.assertEqual(st["sends"]["in"][0][0], [6.0, 0.0])
        self.assertAlmostEqual(st["sends"]["in"][1][0][0], 2.9 + 3.0103, places=4)

    def test_known_values(self):
        st = m.default_state()
        st["version"] = 5
        st["sends"]["in"][0][0] = [-10.0, 0.0]          # centre: 3 dB up, still the centre
        st["sends"]["in"][1][0] = [-10.0, -1.0]         # the ends stay as they are
        st["sends"]["in"][2][0] = [-10.0, 1.0]
        st["sends"]["in"][3][0] = [None, 0.5]           # off: the position is converted too
        st["sends"]["play"][0][0] = [-10.0, 0.5]        # a linked pair: left alone
        st = m.upgrade_state(st)
        sends = st["sends"]["in"]
        self.assertAlmostEqual(sends[0][0][0], -10.0 + 3.0103, places=4)
        self.assertEqual(sends[0][0][1], 0.0)
        self.assertEqual((sends[1][0], sends[2][0]), ([-10.0, -1.0], [-10.0, 1.0]))
        self.assertIsNone(sends[3][0][0])
        self.assertAlmostEqual(sends[3][0][1], math.atan2(1.0, 0.5) * 4 / math.pi - 1)
        self.assertEqual(st["sends"]["play"][0][0], [-10.0, 0.5])

    def test_only_files_from_before_are_converted(self):
        st = m.default_state()
        st["sends"]["in"][0][0] = [-10.0, 0.0]
        mix = m.extract_mix(st)
        for version, converted in ((None, True), (0, True), (1, True), (5, True), (6, False),
                                   (7, False), ("x", False), (5.5, False), (True, False),
                                   ([5], False), (-1, False)):
            with self.subTest(version=version):
                saved = copy.deepcopy(st)
                if version is None:
                    del saved["version"]
                else:
                    saved["version"] = version
                level = m.upgrade_state(saved)["sends"]["in"][0][0][0]
                self.assertEqual(level != -10.0, converted)
                self.assertEqual(saved["version"], m.STATE_VERSION)
        for version, converted in ((None, True), (1, True), (2, False), (3, False),
                                   ("x", False), (1.5, False), (True, False), (-1, False)):
            with self.subTest(file_version=version):
                data = {"format": "openface-mixer-mix", "name": "x", "mix": mix,
                        "slots": [{"name": "x", "mix": mix}]}
                if version is not None:
                    data["version"] = version
                path = self.dir / "file.json"
                path.write_text(json.dumps(data))
                for got in (import_mix(path)[1], PresetBank(path).slots[0]["mix"]):
                    self.assertEqual(got["sends"]["in"][0][0][0] != -10.0, converted)


if __name__ == "__main__":
    unittest.main()
