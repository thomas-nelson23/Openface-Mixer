import struct
import tempfile
import unittest
from pathlib import Path

from openface_mixer import config, devices, fireface802 as ff, model as m


class CommandTest(unittest.TestCase):
    def test_commands_fit_the_engine(self):
        cmds = ff.commands(ff.default_settings())
        self.assertLessEqual(len(cmds), config.N_DEV_CMD)
        self.assertNotIn(0, cmds)              # 0 would be an unused slot: the engine skips it
        self.assertEqual(len(cmds), len(set(cmds)))
        s = ff.default_settings()              # everything on: every slot in use, none twice
        for ch in s["fx"]["in"] + s["fx"]["out"]:
            ch.update(lc=True, eq=True, dyn=True, al=True)
        s["fx"]["reverb"]["on"] = s["fx"]["echo"]["on"] = True
        cmds = ff.commands(s)
        self.assertNotIn(0, cmds)
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

    def test_inst_clears_the_stored_phantom_power(self):
        """48V must not come back by itself when Inst is switched off again."""
        s = ff.default_settings()
        s["in"]["p48"][2] = s["in"]["p48"][3] = True
        self.assertIn(0x0A080001, ff.commands(s))        # AN 11 48V on
        ff.set_inst(s, [2], True)
        self.assertEqual(s["in"]["p48"], [False, False, False, True])   # AN 12 keeps its own
        cmds = ff.commands(s)
        self.assertIn(0x0A090001, cmds)
        self.assertIn(0x0A080000, cmds)
        ff.set_inst(s, [2], False)
        cmds = ff.commands(s)
        self.assertIn(0x0A090000, cmds)                  # Inst off
        self.assertIn(0x0A080000, cmds)                  # ... and 48V stays off
        self.assertIn(0x0B080001, cmds)

    def test_a_saved_state_with_inst_and_phantom_loses_the_phantom(self):
        s = ff.default_settings()
        s["in"]["p48"] = [True, True, False, False]
        s["in"]["inst"] = [True, False, False, True]
        s = ff.upgrade_settings(s)
        self.assertEqual(s["in"]["p48"], [False, True, False, False])
        self.assertEqual(s["in"]["inst"], [True, False, False, True])

    def test_output_commands(self):
        s = ff.default_settings()
        s["out"]["phase"][12] = True
        s["out"]["level"][0] = 2
        cmds = ff.commands(s)
        self.assertIn(((30 + 12) << 24) | 0x070001, cmds)   # AES L phase
        self.assertIn((30 << 24) | 0x080002, cmds)          # AN 1 Hi Gain

    def test_effects_off(self):
        cmds = ff.commands(ff.default_settings())
        self.assertIn((ff.FX_CH << 24) | (0x00 << 16), cmds)                  # reverb off
        self.assertIn((ff.FX_CH << 24) | (0x20 << 16), cmds)                  # echo off
        self.assertIn((5 << 24) | (0x40 << 16), cmds)                         # AN 6 EQ off
        self.assertIn(((30 + 9) << 24) | (0x60 << 16), cmds)                  # PH 10 dyn off
        self.assertIn(((30 + 1) << 24) | (ff.OUT_FROM_FX << 16) | (-650 & 0xFFFF), cmds)
        self.assertIn(ff.virt_cmd(0x1E, 0x20, 0), cmds)                      # play 1 -> FX off

    def test_parity_matches_ctl_services(self):
        # write_dsp_cmd() in snd-firewire-ctl-services: odd parity in bit 31
        for word in (0x00060001, 0x40209000, 0x1E00FFC4, 0):
            self.assertEqual(bin(ff.with_parity(word)).count("1") % 2, 1)
        self.assertEqual(ff.with_parity(0x40209000), 0xC0209000)


class ConfigTest(unittest.TestCase):
    def test_default(self):
        self.assertEqual(ff.config_word(ff.default_settings()), 0x2000 | 0x40)   # + effects on inputs

    def test_options(self):
        s = ff.default_settings()
        s.update(clock="adat_b", aes_in="optical", opt_out="spdif", aes_pro=True,
                 word_single=True)
        self.assertEqual(ff.config_word(s), 0x2040 | 0x1000 | 0x200 | 0x100 | 0x20 | 0x10)

    def test_clock_sources(self):
        s = ff.default_settings()
        for key, bits in (("word", 0x400), ("aes", 0x800), ("adat_a", 0xC00)):
            s["clock"] = key
            self.assertEqual(ff.config_word(s) & 0x1C00, bits)

    def test_configuration_is_the_units_until_a_setting_changes(self):
        # connecting must not change the unit's clock: nothing is written until the clock has
        # been read from its status and the user has changed a setting
        dev = devices.DEVICES["ff802"]
        st = m.default_state(4)
        dev.upgrade_settings(st)
        st["hw"]["fx"]["send"]["in"][2] = -12.0
        config_word, cmds = dev.device_words(st)
        self.assertEqual(config_word, 0)
        ff.settings_from_status(st["hw"], (0x2 << 28) | 0x201)     # running from word clock
        self.assertEqual(st["hw"]["clock"], "word")
        self.assertEqual(dev.device_words(st)[0], 0)
        st["hw"].update(edited=True, aes_pro=True)                 # what the settings panel does
        self.assertEqual(dev.device_words(st)[0], 0x2040 | 0x400 | 0x20)
        st["hw"]["known"] = False                                  # changed before any connect
        self.assertEqual(dev.device_words(st)[0], 0)
        # the DSP commands are not held back: channel settings and effects go out all along,
        # and the effects follow a mute
        self.assertEqual(dev.device_words(st)[1], cmds)
        self.assertIn(0x0201FF88, cmds)                            # AN 3 FX send -12.0 dB
        st["mute"]["in"][2] = True
        self.assertIn(0x0201FD76, dev.device_words(st)[1])         # ... off while muted

    def test_clock_from_status(self):
        s = ff.default_settings()
        for bits, key in ((0xE00, "internal"), (0x201, "word"), (0x402, "aes"),
                          (0x604, "adat_a"), (0x808, "adat_b"), (0x60C, "adat_a")):
            ff.settings_from_status(s, bits)
            self.assertEqual(s["clock"], key)
        # the source codes of ADAT A and B are disputed: an input without lock can't be the
        # source, so it is not taken for one (and never written)
        for bits in (0x608, 0x804, 0x200, 0x401):
            ff.settings_from_status(s, bits)
            self.assertIsNone(s["clock"])
        ff.settings_from_status(s, 0xA00)                # no source we know: never written
        s["edited"] = True
        self.assertIsNone(s["clock"])
        self.assertFalse(ff.config_ready(s))

    def test_upgrade_keeps_a_configuration_the_user_made(self):
        # states from before "edited": sent on every connect, so a changed one stays the user's
        old = ff.default_settings()
        del old["known"], old["edited"]
        self.assertFalse(ff.config_ready(ff.upgrade_settings(dict(old))))
        s = ff.upgrade_settings(dict(old, clock="word"))
        self.assertTrue(ff.config_ready(s))
        self.assertEqual(ff.config_word(s) & 0x1C00, 0x400)


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


class UsbTest(unittest.TestCase):
    def test_usb_commands(self):
        s = ff.default_settings()
        s.update(clock="adat_b", opt_out="spdif", aes_pro=True)
        cmds = ff.usb_commands(s)
        self.assertLessEqual(len(cmds), config.N_DEV_CMD)
        self.assertEqual(cmds[:len(ff.commands(s))], ff.commands(s))
        self.assertIn(0x3D200004, cmds)                  # clock: ADAT B
        self.assertIn(0x3D460001, cmds)                  # optical out: S/PDIF
        self.assertIn(0x3D420001, cmds)                  # AES out: professional
        self.assertIn(0x3D400000, cmds)                  # AES in: XLR
        self.assertNotIn(0, cmds[len(ff.commands(s)):])

    def test_registers_are_the_firewire_commands(self):
        # oscmix's 802 register map: AN 1 gain 0x0007, Mic 9 48V 0x0808, AN 1 out level 0x1E08
        self.assertEqual(ff.phys_cmd(0, ff.IN_LINE_GAIN, 0) >> 16, 0x0007)
        self.assertEqual(ff.phys_cmd(8, ff.IN_MIC_POWER, 0) >> 16, 0x0808)
        self.assertEqual(ff.phys_cmd(ff.OUT_CH_OFFSET, ff.OUT_LINE_LEVEL, 0) >> 16, 0x1E08)
        self.assertEqual(ff.virt_cmd(29, 0x3D, 0) >> 16 & 0x7FFF, 0x477D)   # PB ADAT 16 -> ADAT 16

    def test_decode_usb_status(self):
        st = ff.decode_usb_status(ff.USB_STATUS_VALID | (2 << 8) | 3)
        self.assertEqual(st["rate"], 48000)
        self.assertEqual(st["source"], "ADAT A")
        self.assertEqual(st["inputs"], [])
        self.assertIsNone(ff.decode_usb_status(0)["rate"])

    def test_usb_channels(self):
        self.assertEqual(len(ff.usb_channels("play", 1)), 22)
        self.assertEqual(ff.usb_channels("play", 4), list(range(14)))
        self.assertEqual(ff.usb_channels("in", 1), ff.channels("in", 1))


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

    def test_usb_device(self):
        usb = devices.DEVICES["ff802usb"]
        self.assertEqual(usb.service, "openface-mixer-engine@ff802usb.service")
        self.assertEqual(usb.family, "latter")
        st = m.default_state(4)
        usb.upgrade_settings(st)
        self.assertEqual(usb.device_words(st)[0], 0)
        self.assertEqual(config.state_file(usb), config.CONFIG_DIR / "ff802usb" / "state.json")

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
        st["hw"].update(clock="aes", known=True, edited=True)
        data = config.matrix_file_bytes(st, dev)
        base = 4 + (m.N_OUT * m.N_SRC + m.N_OUT) * 4
        self.assertEqual(len(data), base + 12 + config.N_DEV_CMD * 4)
        magic, cfg = struct.unpack_from("<II", data, base)
        self.assertEqual(magic, config.DEVICE_MAGIC)
        self.assertEqual(cfg, 0x2040 | 0x800)
        first, = struct.unpack_from("<I", data, base + 12)
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
