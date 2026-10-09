import struct
import unittest

from openface_mixer import config, devices, fireface800 as ff, model as m


class ConfigTest(unittest.TestCase):
    def test_default(self):
        # internal clock, Lo Gain in, Hi Gain out, front jacks; all rates, continue at errors
        q = ff.config_words(ff.default_settings())
        self.assertEqual(q, [0x00000408, 0x000008B0, 0x8000001F])

    def test_levels(self):
        s = ff.default_settings()
        s.update(in_level="pro", out_level="con")
        q = ff.config_words(s)
        self.assertEqual((q[0] & 0x38, q[1] & 0x03), (0x10, 0x02))
        self.assertEqual((q[0] & 0x1C00, q[1] & 0x18), (0x1000, 0x08))
        s.update(in_level="con", out_level="pro")
        q = ff.config_words(s)
        self.assertEqual((q[0] & 0x38, q[1] & 0x03), (0x20, 0x03))
        self.assertEqual((q[0] & 0x1C00, q[1] & 0x18), (0x0800, 0x18))

    def test_inputs(self):
        s = ff.default_settings()
        s["jacks"] = ["rear", "both", "front"]
        s["p48"] = [True, False, False, True]          # AN 7, AN 10
        s.update(drive=True, limiter=True, speaker_emu=True)
        q = ff.config_words(s)
        self.assertEqual(q[1] & 0x9E4, 0x004 | 0x040 | 0x020 | 0x080)   # AN 1 rear only
        self.assertEqual(q[0] & 0x183, 0x001 | 0x100)
        self.assertTrue(q[0] & 0x200 and q[1] & 0x200)   # drive is in both quadlets
        self.assertTrue(q[0] & 0x004)                    # speaker emulation
        self.assertTrue(q[2] & 0x10000)                  # limiter

    def test_options(self):
        s = ff.default_settings()
        s.update(clock="spdif", spdif_in="optical", opt_out="spdif", spdif_pro=True,
                 emphasis=True, non_audio=True, word_single=True)
        q = ff.config_words(s)
        self.assertEqual(q[2], 0x8000001E | 0xC00 | 0x200 | 0x100 | 0x20 | 0x40 | 0x80 | 0x2000)

    def test_clock_sources(self):
        s = ff.default_settings()
        for key, bits in (("internal", 0x1), ("adat_1", 0), ("adat_2", 0x400),
                          ("spdif", 0xC00), ("word", 0x1400)):
            s["clock"] = key
            self.assertEqual(ff.config_words(s)[2] & 0x1C01, bits)

    def test_nothing_sent_until_known(self):
        s = ff.default_settings()
        self.assertEqual(ff.device_words(s), (0, []))
        s["known"] = True
        self.assertEqual(ff.device_words(s), (1, ff.config_words(s)))


class StatusTest(unittest.TestCase):
    def test_settings_from_status(self):
        s = ff.default_settings()
        s["in_level"] = "pro"
        ff.settings_from_status(s, 0x00000400 | 0x200 | 0x100 | 0x40 | 0x20 | 0x2000 | 0x06)
        self.assertEqual((s["clock"], s["spdif_in"], s["opt_out"]), ("adat_2", "optical", "spdif"))
        self.assertTrue(s["spdif_pro"] and s["emphasis"] and s["word_single"] and s["known"])
        self.assertEqual(s["in_level"], "pro")           # not in the status: kept
        ff.settings_from_status(s, 0x00000001)
        self.assertEqual((s["clock"], s["spdif_in"], s["opt_out"]), ("internal", "coax", "adat"))

    def test_decode_internal(self):
        # internal at 48 kHz; ADAT 1 locked and synced, S/PDIF locked at 44.1k
        q0 = 0x01C00000 | 0x1000 | 0x400 | 0x40000 | 0x8000
        st = ff.decode_status(q0, 0x00000007)
        self.assertEqual((st["rate"], st["source"]), (48000, "Internal"))
        inputs = {label: (state, rate) for label, state, rate in st["inputs"]}
        self.assertEqual(inputs["ADAT 1"], ("Sync", None))
        self.assertEqual(inputs["S/PDIF"], ("Lock", 44100))
        self.assertEqual(inputs["Word Clock"], ("No Lock", None))

    def test_decode_external(self):
        # clocked from word clock at 96 kHz
        q0 = 0x01000000 | 0x0E000000 | 0x20000000 | 0x40000000
        st = ff.decode_status(q0, 0x00001000 | 0x0E)
        self.assertEqual((st["rate"], st["source"]), (96000, "Word Clock"))
        self.assertIn(("Word Clock", "Sync", 96000), st["inputs"])


class LayoutTest(unittest.TestCase):
    def test_channels(self):
        self.assertEqual(ff.channels("in", 1), list(range(28)))
        self.assertEqual(ff.channels("out", 2),
                         list(range(12)) + [12, 13, 14, 15] + [20, 21, 22, 23])
        self.assertEqual(ff.channels("play", 4), list(range(12)))

    def test_labels(self):
        self.assertEqual(ff.chan_label("in", 9), "AN 10")
        self.assertEqual(ff.chan_label("out", 8), "PH 9")
        self.assertEqual(ff.chan_label("in", 11), "SPDIF R")
        self.assertEqual(ff.chan_label("out", 12), "A1 1")
        self.assertEqual(ff.chan_label("in", 27), "A2 8")
        self.assertEqual(ff.pair_label("out", 8), "PH 9/10")
        self.assertEqual(ff.pair_label("in", 10), "SPDIF")
        self.assertEqual(ff.pair_label("out", 20), "A2 1/2")

    def test_channels_fit_the_mix_model(self):
        for kind, limit in (("in", m.N_IN), ("play", m.N_PLAY), ("out", m.N_OUT)):
            self.assertLess(max(ff.channels(kind, 1)), limit)

    def test_channel_settings(self):
        has = [c for c in range(ff.N_PHYS_IN) if ff.channel_has_settings("in", c)]
        self.assertEqual(has, [0, 6, 7, 8, 9])
        self.assertFalse(ff.channel_has_settings("out", 0))
        s = ff.default_settings()
        self.assertFalse(ff.channel_settings_active(s, "in", 9))
        s["p48"][3] = True
        self.assertTrue(ff.channel_settings_active(s, "in", 9))
        s["jacks"][1] = "rear"
        self.assertTrue(ff.channel_settings_active(s, "in", 6))
        s["limiter"] = True
        self.assertTrue(ff.channel_settings_active(s, "in", 0))

    def test_upgrade_settings(self):
        s = ff.upgrade_settings({"clock": "word", "p48": [True], "known": True})
        self.assertEqual((s["clock"], s["known"]), ("word", True))
        self.assertEqual(s["p48"], [False] * 4)          # wrong length: reset
        self.assertEqual(s["jacks"], ["front"] * 3)


class DeviceTest(unittest.TestCase):
    def test_device(self):
        dev = devices.DEVICES["ff800"]
        self.assertEqual(dev.unit_version, 1)
        self.assertEqual(dev.service, "openface-mixer-engine@ff800.service")
        self.assertEqual(dev.phones_pair(1), 4)
        self.assertEqual(dev.pair_label("out", 0, 1), "AN 1/2")


class FileTest(unittest.TestCase):
    def test_matrix_file_carries_config_once_known(self):
        dev = devices.DEVICES["ff800"]
        st = m.default_state(dev.phones_pair(1))
        dev.upgrade_settings(st)
        base = 4 + (m.N_OUT * m.N_SRC + m.N_OUT) * 4
        self.assertEqual(len(config.matrix_file_bytes(st, dev)), base)
        st["hw"]["known"] = True
        data = config.matrix_file_bytes(st, dev)
        self.assertEqual(len(data), base + 8 + config.N_DEV_CMD * 4)
        magic, flag, q0, q1, q2, unused = struct.unpack_from("<6I", data, base)
        self.assertEqual((magic, flag, unused), (config.DEVICE_MAGIC, 1, 0))
        self.assertEqual([q0, q1, q2], ff.config_words(st["hw"]))


if __name__ == "__main__":
    unittest.main()
