import json
import tempfile
import unittest
from pathlib import Path

from openface_mixer import model as m
from openface_mixer.presets import PresetBank, export_mix, import_mix


class PresetBankTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "presets.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_empty_bank(self):
        bank = PresetBank(self.path)
        self.assertEqual(bank.slots, [None] * m.N_SLOTS)

    def test_store_persists(self):
        mix = m.extract_mix(m.default_state())
        PresetBank(self.path).store(2, "Tracking", mix)
        bank = PresetBank(self.path)
        self.assertEqual(bank.name(2), "Tracking")
        self.assertEqual(bank.slots[2]["mix"], mix)

    def test_rename_and_clear(self):
        bank = PresetBank(self.path)
        bank.store(0, "A", {})
        bank.rename(0, "B")
        self.assertEqual(PresetBank(self.path).name(0), "B")
        bank.clear(0)
        self.assertIsNone(PresetBank(self.path).slots[0])

    def test_corrupt_file_is_ignored(self):
        self.path.write_text("{not json")
        self.assertEqual(PresetBank(self.path).slots, [None] * m.N_SLOTS)

    def test_damaged_file_gives_empty_slots(self):
        slot = {"name": "A", "mix": m.extract_mix(m.default_state())}
        whole = json.dumps({"version": 1, "slots": [slot]})
        for content in ("", whole[:500], "[1, 2]", '"x"', "5", "null", "{}",
                        '{"slots": {"a": 1}}', '{"slots": 4}', '{"slots": "abc"}',
                        '{"slots": null}'):
            with self.subTest(content=content[:20]):
                self.path.write_text(content)
                bank = PresetBank(self.path)
                self.assertEqual(bank.slots, [None] * m.N_SLOTS)
                bank.store(1, "B", slot["mix"])            # and the bank still works
                self.assertEqual(PresetBank(self.path).name(1), "B")

    def test_damaged_slot_is_left_empty(self):
        mix = m.extract_mix(m.default_state())
        bad = ["x", 5, [1], {}, {"name": "no mix"}, {"name": "A", "mix": [1]},
               {"name": "A", "mix": "x"}, {"name": "A", "mix": dict(mix, sends="x")},
               {"name": "A", "mix": dict(mix, out=[5])}]
        for slot in bad:
            with self.subTest(slot=str(slot)[:40]):
                good = {"name": "Good", "mix": mix}
                self.path.write_text(json.dumps({"slots": [slot, good, None]}))
                bank = PresetBank(self.path)
                self.assertEqual(bank.slots, [None, good] + [None] * (m.N_SLOTS - 2))

    def test_slot_without_a_usable_name_gets_one(self):
        mix = m.extract_mix(m.default_state())
        self.path.write_text(json.dumps({"slots": [None, {"mix": mix}, {"name": 5, "mix": mix},
                                                   {"name": "", "mix": mix}]}))
        bank = PresetBank(self.path)
        self.assertEqual([bank.name(i) for i in range(4)], [None, "Mix 2", "Mix 3", "Mix 4"])

    def test_old_and_long_files(self):
        mix = m.extract_mix(m.default_state())
        old = dict(mix, out=mix["out"][:17], mute={"in": [True] * 32, "play": [False] * 34})
        self.path.write_text(json.dumps({"slots": [{"name": "Old", "mix": old}] * 12}))
        bank = PresetBank(self.path)
        self.assertEqual(len(bank.slots), m.N_SLOTS)
        st = m.apply_mix(m.default_state(), bank.slots[7]["mix"])
        self.assertEqual((len(st["out"]), len(st["mute"]["in"])), (m.N_PAIRS, m.N_IN))
        self.assertEqual(st["mute"]["in"][31:33], [True, False])


class MixFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "x.json"

    def file(self, **changes):
        return dict({"format": "openface-mixer-mix", "version": 1, "name": "Live",
                     "mix": m.extract_mix(m.default_state())}, **changes)

    def test_export_import(self):
        mix = m.extract_mix(m.default_state())
        export_mix(self.path, "Live", mix)
        self.assertEqual(import_mix(self.path), ("Live", mix))
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["x.json"])

    def test_rejects_foreign_json(self):
        self.path.write_text(json.dumps({"hello": 1}))
        with self.assertRaises(ValueError):
            import_mix(self.path)

    def test_rejects_what_is_not_a_mix_file(self):
        whole = json.dumps(self.file())
        for content in ("", whole[:800], "{not json", "[1, 2]", '"x"', "5", "null", "{}",
                        json.dumps(self.file(format="other")), json.dumps(self.file(mix=[1])),
                        json.dumps(self.file(mix="x")), json.dumps(self.file(mix=None)),
                        json.dumps(self.file(mix={})), json.dumps(self.file(mix={"names": {}})),
                        "[" * 100000):
            with self.subTest(content=content[:30]):
                self.path.write_text(content)
                with self.assertRaises(ValueError):
                    import_mix(self.path)
        self.path.write_bytes(bytes([0xff, 0xfe, 0x00, 0x80]))
        with self.assertRaises(ValueError):
            import_mix(self.path)

    def test_rejects_a_damaged_mix(self):
        mix = m.extract_mix(m.default_state())
        for key in m.MIX_KEYS:
            for value in ("x", 7, None, [1]):
                with self.subTest(key=key, value=value):
                    self.path.write_text(json.dumps(self.file(mix=dict(mix, **{key: value}))))
                    with self.assertRaises(ValueError):
                        import_mix(self.path)
        broken = json.loads(json.dumps(mix))
        del broken["sends"]["play"]
        self.path.write_text(json.dumps(self.file(mix=broken)))
        with self.assertRaises(ValueError):
            import_mix(self.path)

    def test_missing_file_is_an_os_error(self):
        with self.assertRaises(OSError):
            import_mix(self.path)

    def test_name_falls_back_to_the_file_name(self):
        for name in (None, "", [1], 5):
            self.path.write_text(json.dumps(self.file(name=name)))
            self.assertEqual(import_mix(self.path)[0], "x")

    def test_a_mix_saved_with_fewer_channels_is_padded(self):
        mix = m.extract_mix(m.default_state())
        mix["out"] = mix["out"][:17]
        mix["sends"]["in"] = [ch[:17] for ch in mix["sends"]["in"][:32]]
        mix["sends"]["in"][31][16] = [-3.0, 0.0]
        self.path.write_text(json.dumps(self.file(mix=mix)))
        _name, loaded = import_mix(self.path)
        self.assertEqual(len(loaded["out"]), m.N_PAIRS)
        self.assertEqual(loaded["sends"]["in"][31][16:], [[-3.0, 0.0], [None, 0.0]])
        self.assertEqual(loaded["sends"]["in"][35], [[None, 0.0]] * m.N_PAIRS)

    def test_export_to_a_place_that_cannot_be_written_is_an_os_error(self):
        mix = m.extract_mix(m.default_state())
        blocker = self.path.parent / "file"
        blocker.write_text("")
        folder = self.path.parent / "folder"
        folder.mkdir()
        (folder / "kept").write_text("")
        with self.assertRaises(OSError):
            export_mix(blocker / "sub" / "mix.json", "Live", mix)     # under a file
        with self.assertRaises(OSError):
            export_mix(folder, "Live", mix)                           # a folder with files
        # nothing is left behind
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["file", "folder"])
        self.assertEqual(blocker.read_text(), "")


if __name__ == "__main__":
    unittest.main()
