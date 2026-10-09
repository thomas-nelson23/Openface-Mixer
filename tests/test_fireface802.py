import struct
import tempfile
import unittest
from pathlib import Path

from openface_mixer import config, devices, fireface802 as ff, model as m


class CommandTest(unittest.TestCase):
    def test_commands_fit_the_engine(self):
        cmds = ff.commands(ff.default_settings())
        self.assertLessEqual(len(cmds), config.N_DEV_CMD)
        self.assertNotIn(0, cmds)              # 0 marks an unused slot
        self.assertEqual(len(cmds), len(set(cmds)))

    def test_slots_are_stable(self):
        """The engine sends a slot again when its word changes, so a setting must not move."""
        a = ff.default_settings()
        b = ff.default_settings()
        b["in"]["p48"][1] = True
        b["out"]["level"][3] = 2
        ca, cb = ff.commands(a), ff.commands(b)
        self.assertEqual(len(ca), len(cb))
        self.assertEqual(sum(x != y for x, y in zip(ca, cb)), 2)

    def test_input_commands(self):
        s = ff.default_settings()
        s["in"]["phase"][3] = True
        s["in"]["gain"][0] = 6.5
        s["in"]["level"][7] = 0
        s["in"]["p48"][0] = True
        cmds = ff.commands(s)
        self.assertIn(0x03060001, cmds)                  # AN 4 phase invert
        self.assertIn(0x00070041, cmds)                  # AN 1 gain 6.5 dB = 65
        self.assertIn(0x07080000, cmds)                  # AN 8 level Lo Gain
        self.assertIn(0x08080001, cmds)                  # AN 9 48V on

    def test_inst_switches_phantom_off(self):
        s = ff.default_settings()
        s["in"]["p48"][2] = s["in"]["inst"][2] = True
        cmds = ff.commands(s)
        self.assertIn(0x0A090001, cmds)                  # AN 11 Inst
        self.assertIn(0x0A080000, cmds)                  # AN 11 48V off
        self.assertNotIn(0x0A080001, cmds)

    def test_output_commands(self):
        s = ff.default_settings()
        s["out"]["phase"][12] = True
        s["out"]["level"][0] = 2
        cmds = ff.commands(s)
        self.assertIn(((30 + 12) << 24) | 0x070001, cmds)   # AES L phase
        self.assertIn((30 << 24) | 0x080002, cmds)          # AN 1 Hi Gain

    def test_effects_off(self):
        cmds = ff.commands(ff.default_settings())
        self.assertIn((ff.FX_CH << 24) | (ff.REVERB_ON << 16), cmds)
        self.assertIn((ff.FX_CH << 24) | (ff.ECHO_ON << 16), cmds)
        self.assertIn((5 << 24) | (ff.EQ_ON << 16), cmds)                     # AN 6 EQ off
        self.assertIn(((30 + 9) << 24) | (ff.DYN_ON << 16), cmds)             # PH 10 dyn off
        self.assertIn(((30 + 1) << 24) | (ff.OUT_FROM_FX << 16) | (-650 & 0xFFFF), cmds)
        self.assertIn(ff.virt_cmd(0x1E, 0x20, 0), cmds)                      # play 1 -> FX off

    def test_parity_matches_ctl_services(self):
        # write_dsp_cmd() in snd-firewire-ctl-services: odd parity in bit 31
        for word in (0x00060001, 0x40209000, 0x1E00FFC4, 0):
            self.assertEqual(bin(ff.with_parity(word)).count("1") % 2, 1)
        self.assertEqual(ff.with_parity(0x40209000), 0xC0209000)


class ConfigTest(unittest.TestCase):
    def test_default(self):
        self.assertEqual(ff.config_word(ff.default_settings()), 0x2000)

    def test_options(self):
        s = ff.default_settings()
        s.update(clock="adat_b", aes_in="optical", opt_out="spdif", aes_pro=True,
                 word_single=True)
        self.assertEqual(ff.config_word(s), 0x2000 | 0x1000 | 0x200 | 0x100 | 0x20 | 0x10)

    def test_clock_sources(self):
        s = ff.default_settings()
        for key, bits in (("word", 0x400), ("aes", 0x800), ("adat_a", 0xC00)):
            s["clock"] = key
            self.assertEqual(ff.config_word(s) & 0x1C00, bits)


class StatusTest(unittest.TestCase):
    def test_decode(self):
        # 48 kHz on ADAT A, ADAT A synced at 48k, word clock locked at 44.1k
        word = (0x2 << 28) | (0x2 << 20) | (0x1 << 12) | 0x600 | 0x40 | 0x04 | 0x01
        st = ff.decode_status(word)
        self.assertEqual(st["rate"], 48000)
        self.assertEqual(st["source"], "ADAT A")
        inputs = {label: (state, rate) for label, state, rate in st["inputs"]}
        self.assertEqual(inputs["ADAT A"], ("Sync", 48000))
        self.assertEqual(inputs["Word Clock"], ("Lock", 44100))
        self.assertEqual(inputs["AES"], ("No Lock", None))


class LayoutTest(unittest.TestCase):
    def test_channels(self):
        self.assertEqual(ff.channels("in", 1), list(range(30)))
        self.assertEqual(ff.channels("out", 2),
                         list(range(14)) + [14, 15, 16, 17] + [22, 23, 24, 25])
        self.assertEqual(ff.channels("play", 4), list(range(14)))

    def test_labels(self):
        self.assertEqual(ff.chan_label("in", 8), "AN 9")
        self.assertEqual(ff.chan_label("out", 8), "PH 9")
        self.assertEqual(ff.chan_label("in", 13), "AES R")
        self.assertEqual(ff.chan_label("out", 14), "A 1")
        self.assertEqual(ff.chan_label("in", 29), "B 8")
        self.assertEqual(ff.pair_label("out", 10), "PH 11/12")
        self.assertEqual(ff.pair_label("in", 12), "AES")
        self.assertEqual(ff.pair_label("out", 22), "B 1/2")

    def test_channels_fit_the_mix_model(self):
        for kind, limit in (("in", m.N_IN), ("play", m.N_PLAY), ("out", m.N_OUT)):
            self.assertLess(max(ff.channels(kind, 1)), limit)

    def test_settings_active(self):
        s = ff.default_settings()
        self.assertFalse(ff.channel_settings_active(s, "in", 9))
        s["in"]["p48"][1] = True
        self.assertTrue(ff.channel_settings_active(s, "in", 9))
        self.assertFalse(ff.channel_settings_active(s, "out", 9))

    def test_upgrade_settings(self):
        s = ff.upgrade_settings({"clock": "aes", "in": {"phase": [True]}})
        self.assertEqual(s["clock"], "aes")
        self.assertEqual(len(s["in"]["phase"]), ff.N_PHYS_IN)   # wrong length: reset
        self.assertEqual(s["out"], ff.default_settings()["out"])


class DeviceTest(unittest.TestCase):
    def test_find_firewire_unit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, spec, ver in (("fw0.0", "0x00609e", "0x010483"),   # a host controller
                                    ("fw1.0", "0x000a35", "0x000005")):
                (root / name).mkdir()
                (root / name / "specifier_id").write_text(spec + "\n")
                (root / name / "version").write_text(ver + "\n")
            (root / "fw1").mkdir()
            self.assertEqual(devices.find_firewire_unit(0x000A35, 5, root), "fw1")
            self.assertIsNone(devices.find_firewire_unit(0x000A35, 4, root))

    def test_names(self):
        ff802 = devices.DEVICES["ff802"]
        digi = devices.DEVICES["digiface"]
        self.assertEqual(digi.shm_name, "openface-mixer")
        self.assertEqual(digi.service, "openface-mixer-engine.service")
        self.assertEqual(ff802.shm_name, "openface-mixer-ff802")
        self.assertEqual(ff802.service, "openface-mixer-engine@ff802.service")

    def test_digiface_layout_unchanged(self):
        digi = devices.DEVICES["digiface"]
        self.assertEqual(digi.channels("out", 1), list(range(34)))
        self.assertEqual(digi.channels("in", 2), list(range(16)))
        self.assertEqual(digi.chan_label("out", 32, 1), "Ph L")
        self.assertEqual(digi.pair_label("in", 0, 1), "AD1 1/2")


class FileTest(unittest.TestCase):
    def test_matrix_file_carries_settings(self):
        dev = devices.DEVICES["ff802"]
        st = m.default_state(4)
        dev.upgrade_settings(st)
        st["hw"]["clock"] = "aes"
        data = config.matrix_file_bytes(st, dev)
        base = 4 + (m.N_OUT * m.N_SRC + m.N_OUT) * 4
        self.assertEqual(len(data), base + 8 + config.N_DEV_CMD * 4)
        magic, cfg = struct.unpack_from("<II", data, base)
        self.assertEqual(magic, config.DEVICE_MAGIC)
        self.assertEqual(cfg, 0x2000 | 0x800)
        first, = struct.unpack_from("<I", data, base + 8)
        self.assertEqual(first, ff.commands(st["hw"])[0])

    def test_digiface_matrix_file_unchanged(self):
        data = config.matrix_file_bytes(m.default_state())
        self.assertEqual(len(data), 4 + (m.N_OUT * m.N_SRC + m.N_OUT) * 4)

    def test_device_dirs(self):
        self.assertEqual(config.state_file(devices.DEVICES["digiface"]),
                         config.CONFIG_DIR / "state.json")
        self.assertEqual(config.state_file(devices.DEVICES["ff802"]),
                         config.CONFIG_DIR / "ff802" / "state.json")


if __name__ == "__main__":
    unittest.main()
