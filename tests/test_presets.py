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


class MixFileTest(unittest.TestCase):
    def test_export_import(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            mix = m.extract_mix(m.default_state())
            export_mix(p, "Live", mix)
            self.assertEqual(import_mix(p), ("Live", mix))

    def test_rejects_foreign_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text(json.dumps({"hello": 1}))
            with self.assertRaises(ValueError):
                import_mix(p)


if __name__ == "__main__":
    unittest.main()
