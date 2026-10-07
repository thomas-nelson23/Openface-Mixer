import struct
import unittest

from openface_mixer import config, model as m


class MatrixFileTest(unittest.TestCase):
    def test_layout(self):
        st = m.default_state()
        st["out"][0]["gain"] = -6.0
        data = config.matrix_file_bytes(st)
        self.assertEqual(len(data), 8 + (m.N_OUT * m.N_SRC + m.N_OUT) * 4)
        magic, mode = struct.unpack_from("<2I", data)
        self.assertEqual(magic, config.MATRIX_MAGIC)
        self.assertEqual(mode, m.ENGINE_MODE["hardware"])
        out0, = struct.unpack_from("<f", data, 8 + m.N_OUT * m.N_SRC * 4)
        self.assertAlmostEqual(out0, 10 ** (-6 / 20), places=6)

    def test_software_mode(self):
        st = m.default_state()
        st["mixer_mode"] = "software"
        self.assertEqual(struct.unpack_from("<2I", config.matrix_file_bytes(st))[1],
                         m.ENGINE_MODE["software"])


if __name__ == "__main__":
    unittest.main()
