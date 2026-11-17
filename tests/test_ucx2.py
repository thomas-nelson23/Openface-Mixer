import unittest

from openface_mixer import config, devices, fireface802 as ff802, model as m, ucx2


class CommandTest(unittest.TestCase):
    def test_commands(self):
        s = ucx2.default_settings()
        s["in"]["phase"][9] = True
        s["in"]["gain"][0] = 80.0                  # clamped to 75 dB
        s["in"]["gain"][4] = 12.5
        s["in"]["p48"][1] = True
        s["in"]["level"][0] = 1                    # AN 3 at +19 dBu
        s["in"]["hiz"][1] = True
        s["out"]["level"][6] = 1                   # phones high
        s.update(clock="aes", opt_out="spdif")
        cmds = ucx2.usb_commands(s)
        for word in (0x02470001, 0x000802EE, 0x0108007D, 0x00490001, 0x00890001, 0x00CB0001,
                     0x06890001, 0x30640003, 0x30780001, 0x307B0000, 0x30000000, 0x30140000,
                     0x0503FD76, 0x34010000, 0x05020000, 0x004F0000):
            self.assertIn(word, cmds, hex(word))
        self.assertLessEqual(len(cmds), config.N_DEV_CMD)   # the engine's dev_cmd slots

    def test_no_command_is_an_empty_slot(self):
        """The engine skips a 0 word. Input 1's mute, register 0 = 0, is sent with its parity
        bit (which the engine puts on the wire in any case) so that it goes out too."""
        cmds = ucx2.usb_commands(ucx2.default_settings())
        self.assertNotIn(0, cmds)
        self.assertEqual(len(cmds), len(set(cmds)))
        self.assertIn(0x80000000, cmds)                     # AN 1 mute off
        self.assertIn(0x00400000, cmds)                     # AN 2: odd parity as it is
        for c in range(ucx2.N_IN):
            word = c << 6 << 16                             # mute off, as it was sent before
            self.assertIn(ff802.with_parity(word), cmds, hex(word))
        # the engine's ff802_parity() drops bit 31 and sets it again: the same on the wire
        self.assertEqual(ff802.with_parity(0x80000000), ff802.with_parity(0))

    def test_status(self):
        st = ucx2.decode_usb_status(0x80000000 | 5 << 8 | 4)
        self.assertEqual((st["rate"], st["source"]), (96000, "Optical"))


class LayoutTest(unittest.TestCase):
    def test_layout(self):
        self.assertEqual(ucx2.channels("in", 1), list(range(20)))
        self.assertEqual(ucx2.channels("out", 4), list(range(12)))
        self.assertEqual([ucx2.pair_label("out", c) for c in (0, 6, 8, 10, 12)],
                         ["AN 1/2", "PH 7/8", "SPDIF", "AES", "A 1/2"])
        self.assertTrue(ucx2.channel_settings_active(
            {"in": {"phase": [False] * 20, "gain": [0.0] * 8, "p48": [True, False],
                    "level": [0] * 6, "hiz": [False, False]}}, "in", 0))

    def test_device(self):
        dev = devices.DEVICES["ucx2"]
        self.assertEqual((dev.family, dev.bus, dev.phones_pair(1)), ("latter", "USB", 3))
        st = m.default_state(4)
        dev.upgrade_settings(st)
        cfg, cmds = dev.device_words(st)
        self.assertEqual(cfg, 0)
        self.assertIn(0x307B0000, cmds)
        self.assertIn("Class Compliant", dev.missing_hint())


if __name__ == "__main__":
    unittest.main()
