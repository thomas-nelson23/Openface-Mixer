import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import config, model as m


class MatrixFileTest(unittest.TestCase):
    def test_layout(self):
        st = m.default_state()
        st["out"][0]["gain"] = -6.0
        data = config.matrix_file_bytes(st)
        self.assertEqual(len(data), 4 + (m.N_OUT * m.N_SRC + m.N_OUT) * 4)
        magic, = struct.unpack_from("<I", data)
        self.assertEqual(magic, config.MATRIX_MAGIC)
        out0, = struct.unpack_from("<f", data, 4 + m.N_OUT * m.N_SRC * 4)
        self.assertAlmostEqual(out0, 10 ** (-6 / 20), places=6)

    def test_solo_is_not_saved(self):
        st = m.default_state()
        st["selected"] = 0
        st["solo"]["play"][4] = True
        data = config.matrix_file_bytes(st)
        play1_to_out1, = struct.unpack_from("<f", data, 4 + m.N_IN * 4)
        self.assertEqual(play1_to_out1, 1.0)

    def test_state_file_leaves_out_solo(self):
        st = m.default_state()
        st["solo"]["in"][0] = True
        written = {}
        with mock.patch.object(config, "atomic_write", lambda path, data: written.update({path: data})):
            config.save_state(st)
        self.assertNotIn("solo", json.loads(written[config.state_file()]))
        self.assertTrue(st["solo"]["in"][0])   # the live state keeps it


class UiScaleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ui_file = Path(tmp.name) / "ui.json"
        patch = mock.patch.object(config, "UI_FILE", ui_file)
        patch.start()
        self.addCleanup(patch.stop)

    def test_default_and_roundtrip(self):
        self.assertEqual(config.load_ui_scale(), 1.0)
        config.save_ui_scale(1.5)
        self.assertEqual(config.load_ui_scale(), 1.5)
        self.assertEqual(json.loads(config.UI_FILE.read_text()), {"scale": 1.5})

    def test_bad_values_fall_back_or_clamp(self):
        config.UI_FILE.write_text("{not json")
        self.assertEqual(config.load_ui_scale(), 1.0)
        config.UI_FILE.write_text(json.dumps({"scale": "large"}))
        self.assertEqual(config.load_ui_scale(), 1.0)
        config.UI_FILE.write_text(json.dumps({"scale": 99}))
        self.assertEqual(config.load_ui_scale(), config.UI_SCALE_MAX)
        config.save_ui_scale(0.1)
        self.assertEqual(config.load_ui_scale(), config.UI_SCALE_MIN)

    def test_apply_sets_qt_scale_factor(self):
        config.save_ui_scale(1.25)
        with mock.patch.dict(os.environ, {"QT_SCALE_FACTOR": "1"}, clear=False):
            config.apply_ui_scale()
            self.assertEqual(os.environ["QT_SCALE_FACTOR"], "1.25")


if __name__ == "__main__":
    unittest.main()
