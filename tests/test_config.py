import json
import struct
import unittest
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


if __name__ == "__main__":
    unittest.main()
