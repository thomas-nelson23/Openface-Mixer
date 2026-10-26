import unittest

from openface_mixer import devices, fireface400 as ff, model as m


class ConfigTest(unittest.TestCase):
    def test_default(self):
        # internal clock, Lo Gain in, Hi Gain out and phones; MIDI to offset 0, all rates
        q = ff.config_words(ff.default_settings())
        self.assertEqual(q, [0x00040410, 0x00000010, 0x8400001F])

    def test_inputs(self):
        s = ff.default_settings()
        s.update(p48=[False, True], inst=[True, False], pad=[False, True], in_level="con",
                 phones_level="con")
        q = ff.config_words(s)
        self.assertEqual(q[0] & 0x3BF, 0x080 | 0x200 | 0x002 | 0x020)
        self.assertEqual(q[0] & 0x60000, 0x20000)
        self.assertEqual(q[1] & 0x3, 0x3)

    def test_options(self):
        s = ff.default_settings()
        s.update(clock="spdif", spdif_in="optical", opt_out="spdif", spdif_pro=True,
                 word_single=True)
        self.assertEqual(ff.config_words(s)[2] & 0x3F21, 0x0C00 | 0x200 | 0x100 | 0x20 | 0x2000)

    def test_amp_commands(self):
        s = ff.default_settings()
        s.update(mic_gain=[40, 99], line_gain=[12, -1])
        self.assertEqual(ff.amp_commands(s), [0x80000028, 0x80010041, 0x8002000C, 0x80030000])
        self.assertEqual(ff.device_words(s), (0, []))
        s["known"] = True
        cfg, words = ff.device_words(s)
        self.assertEqual((cfg, len(words)), (1, 7))

    def test_status(self):
        s = ff.default_settings()
        ff.settings_from_status(s, 0x00000C00 | 0x200)
        self.assertEqual((s["clock"], s["spdif_in"], s["known"]), ("spdif", "optical", True))
        st = ff.decode_status(0x00001000 | 0x00000400 | 0x06000000, 0x00000000)
        self.assertEqual(st["source"], "ADAT")
        self.assertEqual(st["inputs"][0], ("ADAT", "Sync", 48000))


class LayoutTest(unittest.TestCase):
    def test_channels(self):
        self.assertEqual(ff.channels("in", 1), list(range(18)))
        self.assertEqual(ff.channels("out", 2), list(range(14)))
        self.assertEqual(ff.channels("play", 4), list(range(10)))
        self.assertLessEqual(len(ff.channels("in", 1)), m.N_IN)

    def test_labels(self):
        self.assertEqual(ff.chan_label("in", 6), "AN 7")
        self.assertEqual(ff.chan_label("out", 6), "PH 7")
        self.assertEqual(ff.pair_label("out", 8), "SPDIF")
        self.assertEqual(ff.pair_label("in", 10), "A 1/2")

    def test_channel_settings(self):
        s = ff.default_settings()
        self.assertTrue(ff.channel_has_settings("in", 3))
        self.assertFalse(ff.channel_has_settings("in", 4))
        s["pad"][1] = True
        self.assertTrue(ff.channel_settings_active(s, "in", 3))
        self.assertFalse(ff.channel_settings_active(s, "in", 0))


class DeviceTest(unittest.TestCase):
    def test_device(self):
        dev = devices.DEVICES["ff400"]
        self.assertEqual((dev.family, dev.unit_version, dev.phones_pair(1)), ("former", 2, 3))
        st = m.default_state(4)
        dev.upgrade_settings(st)
        self.assertEqual(st["hw"]["mic_gain"], [0, 0])


if __name__ == "__main__":
    unittest.main()
