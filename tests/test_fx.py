import unittest

from openface_mixer import config, devices, fireface802 as ff, fx, model as m, osc_remote, ucx2


def regs(words):
    """Register -> value of the words, as the USB engines send them (register = word >> 16)."""
    return {w >> 16 & 0x7FFF: w & 0xFFFF for w in words if w}


def state(dev="ff802"):
    st = m.default_state(4)
    devices.DEVICES[dev].upgrade_settings(st)
    return st


class AddressTest(unittest.TestCase):
    """Addresses from the oscmix wiki (Fireface 802 page, "Protocol" for the UCX II) and
    snd-firewire-ctl-services' latter.rs."""

    def test_ff802(self):
        s = ff.default_settings()
        f = s["fx"]
        f["in"][0].update(eq=True, eq_g1=-3.5, dyn=True, dyn_eratio=2.0)
        f["out"][0].update(lc=True, lc_freq=80, lc_slope=3)
        f["reverb"].update(on=True, width=0.5)
        f["echo"].update(on=True, delay=0.5, width=0.25)
        r = regs(ff.usb_commands(s))
        self.assertEqual(r[0x0040], 1)                  # AN 1 EQ on
        self.assertEqual(r[0x0042], -35 & 0xFFFF)       # AN 1 band 1 gain, 0.1 dB
        self.assertEqual(r[0x0067], 20)                 # AN 1 expander ratio
        self.assertEqual((r[0x1E20], r[0x1E21], r[0x1E22]), (1, 80, 3))   # AN 1 out low cut
        self.assertEqual((r[0x3C00], r[0x3C0D]), (1, 50))                 # reverb on, width
        self.assertEqual((r[0x3C20], r[0x3C22], r[0x3C26]), (1, 50, 25))  # echo, 1/100 s
        self.assertEqual(r[0x0001], -650 & 0xFFFF)      # AN 1 FX send off
        self.assertEqual(r[0x1E03], -650 & 0xFFFF)      # AN 1 out FX return off
        self.assertIn(0x47A0, r)                        # playback 1 -> FX left (mixer 30)
        self.assertIn(0x47E0, r)                        # ... right (mixer 31)

    def test_ucx2(self):
        s = ucx2.default_settings()
        f = s["fx"]
        f["in"][0]["lc"] = True
        f["out"][0].update(eq=True, al=True, al_rise=0.5)
        f["room"][0].update(on=True, q9=2.5)
        f["room"][1]["delay"] = 1.5
        f["room"][0]["volcal"] = -1.25
        f["reverb"].update(on=True, width=1.0)
        f["echo"].update(on=True, delay=0.5)
        r = regs(ucx2.usb_commands(s))
        self.assertEqual(r[0x000C], 1)                  # input 1 low cut
        self.assertEqual((r[0x000D], r[0x000E]), (100, 1))
        self.assertEqual(r[0x050F], 1)                  # output 1 EQ
        self.assertEqual(r[0x0526], 5)                  # output 1 auto level rise time
        self.assertEqual((r[0x300D], r[0x3016]), (100, 500))   # reverb width, echo delay (ms)
        self.assertEqual(r[0x3401], 1)                  # output 1 room EQ on
        self.assertEqual(r[0x341F], 25)                 # output 1 band 9 Q
        self.assertEqual(r[0x3420], 15)                 # output 2 delay, 0.1 ms
        self.assertEqual(r[0x050B], -125 & 0xFFFF)      # output 1 volume calibration
        self.assertIn(0x47A0, r)                        # playback 1 FX send left
        self.assertIn(0x47E0, r)
        # 0x35D1 is output 15's band 5 Q, not output 1's switch (which the unit reports there)
        self.assertEqual(r[0x35D1], 10)

    def test_room_eq_layout(self):
        words = fx._room_words(fx.MODELS["ucx2"], 0, dict(fx.default_room(fx.MODELS["ucx2"]),
                                                            on=True))
        addrs = [w >> 16 for w in words]
        self.assertEqual(sorted(addrs[:-1]), list(range(0x3400, 0x3420)))   # delay, on, 9 bands
        self.assertEqual(addrs[-2], 0x3401)                 # the switch after the bands
        self.assertEqual(addrs[-1], 20 << 6 | 0x0B)


class CommandTest(unittest.TestCase):
    def test_parameters_of_an_off_effect_are_sent(self):
        """With everything off, every parameter still goes out once (the engine sends every
        non-zero slot when it connects), so no effect starts with another controller's
        settings."""
        cmds = ff.commands(ff.default_settings())
        self.assertNotIn(0, cmds)
        for word in (0x03410000, 0x03420000, 0x03430064, 0x0344000A,   # AN 4 EQ band 1
                     0x03400000,                                       # AN 4 EQ off
                     0x2164FF38, 0x21600000,          # AN 4 out compressor threshold -20 dB, off
                     0x3C010001, 0x3C0D0064, 0x3C000000,               # reverb type, width, off
                     0x3C22001E, 0x3C200000):                          # echo delay 0.3 s, off
            self.assertIn(word, cmds, hex(word))
        cmds = ucx2.usb_commands(ucx2.default_settings())
        for word in (0x000D0064, 0x000C0000,          # input 1 low cut 100 Hz, off
                     0x3016012C, 0x30140000,          # echo delay 300 ms, off
                     0x3404003C, 0x3401 << 16,        # output 1 room EQ band 1 60 Hz, off
                     0x365F000A, 0x3661 << 16):       # output 20 band 9 Q, room EQ off
            self.assertIn(word, cmds, hex(word))

    def test_parameters_come_before_the_switch(self):
        """The engine sends the slots in order, at the first sync and when they change."""
        for cmds, switches in (
                (ff.commands(ff.default_settings()),
                 {0x0020: 2, 0x0040: 11, 0x0060: 7, 0x0080: 3, 0x3B20: 2, 0x3B40: 11,
                  0x3B60: 7, 0x3B80: 3, 0x3C00: 13, 0x3C20: 6}),
                (ucx2.usb_commands(ucx2.default_settings()),
                 {0x000C: 2, 0x000F: 11, 0x001B: 7, 0x0023: 3, 0x09CC: 2, 0x09CF: 11,
                  0x09DB: 7, 0x09E3: 3, 0x3000: 13, 0x3014: 6, 0x3401: 30, 0x3661: 30})):
            addrs = [w >> 16 & 0x7FFF for w in cmds]
            for switch, n in switches.items():
                at = addrs.index(switch)
                self.assertEqual(sorted(addrs[at - n:at]), list(range(switch + 1, switch + 1 + n)),
                                 hex(switch))

    def test_switching_on_changes_only_the_switch(self):
        s = ff.default_settings()
        off = ff.commands(s)
        s["fx"]["in"][3]["eq"] = True
        s["fx"]["reverb"]["on"] = True
        on = ff.commands(s)
        changed = [(a, b) for a, b in zip(off, on) if a != b]
        self.assertEqual(changed, [(0x03400000, 0x03400001), (0x3C000000, 0x3C000001)])

    def test_values_are_clamped(self):
        s = ucx2.default_settings()
        s["fx"]["in"][0].update(eq=True, eq_q1=50.0, eq_f2=5)
        r = regs(ucx2.usb_commands(s))
        self.assertEqual((r[0x0013], r[0x0015]), (99, 20))

    def test_sends_and_returns(self):
        st = state()
        s = st["hw"]
        st["stereo"]["play"] = [True] + [False] * 17
        s["fx"]["send"]["in"][2] = -12.0
        s["fx"]["ret"][4] = 0.0
        s["fx"]["send"]["play"][0] = 0.0
        s["fx"]["send"]["play"][1] = -6.0
        r = regs(ff.usb_commands(s, st))
        self.assertEqual(r[0x0201], -120 & 0xFFFF)
        self.assertEqual(r[0x2203], 0)
        # a linked pair: left channel to the left FX input only, right to the right
        self.assertEqual((r[0x47A0], r[0x47E0]), (0x9000, 0))
        self.assertEqual(r[0x47A1], 0)
        self.assertEqual(r[0x47E1], fx.mixer_gain(-6.0, True))
        # FireWire uses the engine's 0x8000 | gain * 0x1000 form
        self.assertEqual(fx.mixer_gain(-12.0, False), 0x8000 | round(10 ** (-12 / 20) * 0x1000))
        self.assertEqual(fx.mixer_gain(None, True), 0)

    def test_mono_playback_feeds_both_sides(self):
        s = ucx2.default_settings()
        s["fx"]["send"]["play"][5] = -3.0
        r = regs(ucx2.usb_commands(s))
        self.assertEqual(r[0x47A5], r[0x47E5])
        self.assertGreater(r[0x47A5], 0)


class MuteSoloTest(unittest.TestCase):
    """A muted channel, and one left out by another channel's solo, sends nothing to the FX
    unit; the stored send comes back with the mute off."""

    def setUp(self):
        self.st = state()
        self.st["stereo"]["play"] = [False] * 18
        f = self.st["hw"]["fx"]
        f["send"]["in"][0] = f["send"]["in"][2] = -12.0
        f["send"]["play"][5] = 0.0

    def words(self):
        return ff.commands(self.st["hw"], self.st)

    def test_mute(self):
        self.assertIn(0x0201FF88, self.words())             # AN 3 FX send -12.0 dB
        self.st["mute"]["in"][2] = True
        cmds = self.words()
        self.assertIn(0x0201FD76, cmds)                     # ... off (-65.0 dB) while muted
        self.assertNotIn(0x0201FF88, cmds)
        self.assertIn(0x0001FF88, cmds)                     # AN 1 is not muted
        self.assertEqual(self.st["hw"]["fx"]["send"]["in"][2], -12.0)
        self.st["mute"]["in"][2] = False
        self.assertIn(0x0201FF88, self.words())

    def test_playback_mute(self):
        on = 0x9000                                         # 0 dB, FireWire encoding
        self.assertIn(0x47A50000 | on, self.words())        # playback 6 -> FX left
        self.assertIn(0x47E50000 | on, self.words())        # ... and right
        self.st["mute"]["play"][5] = True
        cmds = self.words()
        self.assertIn(0x47A50000, cmds)
        self.assertIn(0x47E50000, cmds)

    def test_solo(self):
        self.st["solo"]["in"][0] = True
        cmds = self.words()
        self.assertIn(0x0001FF88, cmds)                     # the soloed channel keeps its send
        self.assertIn(0x0201FD76, cmds)                     # the others send nothing
        self.assertIn(0x47A50000, cmds)

    def test_ucx2(self):
        st = state("ucx2")
        st["hw"]["fx"]["send"]["in"][0] = st["hw"]["fx"]["send"]["in"][2] = -3.0
        st["mute"]["in"][2] = True
        cmds = ucx2.usb_commands(st["hw"], st)
        self.assertIn(0x0001FFE2, cmds)                     # AN 1 FX send -3.0 dB
        self.assertIn(0x0081FD76, cmds)                     # AN 3 muted: off

    def test_device_block_and_saved_file(self):
        dev = devices.DEVICES["ff802"]
        self.st["solo"]["in"][0] = True
        self.assertIn(0x0201FD76, config.device_block(self.st, dev)[1])
        data = config.matrix_file_bytes(self.st, dev)[-4 * config.N_DEV_CMD:]
        saved = [int.from_bytes(data[i:i + 4], "little") for i in range(0, len(data), 4)]
        self.assertIn(0x0201FF88, saved)
        self.assertNotIn(0x0201FD76, saved)

    def test_mute_from_the_network_remote(self):
        dev = devices.DEVICES["ff802"]
        osc_remote.apply(self.st, dev, 1, "/input/2/mute", [1.0])
        self.assertIn(0x0201FD76, dev.device_words(self.st)[1])
        osc_remote.apply(self.st, dev, 1, "/input/2/mute", [0.0])
        self.assertIn(0x0201FF88, dev.device_words(self.st)[1])


class SettingsTest(unittest.TestCase):
    def test_old_state_gets_defaults(self):
        st = m.default_state(4)
        st["hw"] = {"clock": "aes"}                # saved before effects existed
        st["hw"] = ff.upgrade_settings(st["hw"])
        self.assertFalse(st["hw"]["fx"]["in"][0]["eq"])
        self.assertEqual(len(st["hw"]["fx"]["out"]), 30)

    def test_broken_values_are_repaired(self):
        s = ucx2.default_settings()
        s["fx"]["in"][0]["eq_g1"] = "loud"
        s["fx"]["in"][1]["dyn"] = 1                   # not a bool
        s["fx"]["send"]["in"] = [0.0] * 3              # wrong length
        s["fx"]["room"] = None
        s["fx"]["ret"][2] = -10.0
        s = ucx2.upgrade_settings(s)
        self.assertEqual(s["fx"]["in"][0]["eq_g1"], 0.0)
        self.assertIs(s["fx"]["in"][1]["dyn"], False)
        self.assertEqual(s["fx"]["send"]["in"], [None] * 20)
        self.assertEqual(len(s["fx"]["room"]), 20)
        self.assertEqual(s["fx"]["ret"][2], -10.0)

    def test_802_eq_types(self):
        self.assertEqual(fx.MODELS["ff802"].param(fx.EQ, 0)[3], 2)    # Peak, Shelf, High Pass
        self.assertEqual(fx.MODELS["ucx2"].param(fx.EQ, 0)[3], 3)

    def test_copy_paste(self):
        f = fx.default_settings(fx.MODELS["ucx2"])
        f["in"][0].update(eq=True, eq_q2=8.0, eq_t1=3)
        data = fx.copy_channel(f, "in", 0)
        g = fx.default_settings(fx.MODELS["ff802"])
        fx.paste_channel(g, "out", [4, 5], data, fx.MODELS["ff802"])
        self.assertTrue(g["out"][5]["eq"])
        self.assertEqual(g["out"][4]["eq_q2"], 5.0)   # the 802's EQ goes up to Q 5.0
        self.assertEqual(g["out"][4]["eq_t1"], 2)


class CurveTest(unittest.TestCase):
    def test_flat_when_off(self):
        ch = fx.default_channel(fx.MODELS["ucx2"])
        self.assertEqual(fx.response(fx.eq_filters(ch, fx.MODELS["ucx2"]), [100, 1000]), [0, 0])

    def test_peak(self):
        ch = fx.default_channel(fx.MODELS["ucx2"])
        ch.update(eq=True, eq_g2=6.0, eq_f2=1000)
        db = fx.response(fx.eq_filters(ch, fx.MODELS["ucx2"]), [1000, 20])
        self.assertAlmostEqual(db[0], 6.0, places=1)
        self.assertAlmostEqual(db[1], 0.0, places=1)

    def test_low_cut(self):
        ch = fx.default_channel(fx.MODELS["ff802"])
        ch.update(lc=True, lc_freq=200, lc_slope=3)
        low, high = fx.response(fx.eq_filters(ch, fx.MODELS["ff802"]), [50, 5000])
        self.assertLess(low, -40)
        self.assertGreater(high, -0.5)

    def test_compressor(self):
        ch = fx.default_channel(fx.MODELS["ucx2"])
        ch.update(dyn_cthr=-20.0, dyn_cratio=4.0, dyn_gain=3.0)
        self.assertEqual(fx.dynamics_curve(ch, [0.0, -30.0]), [-12.0, -27.0])


if __name__ == "__main__":
    unittest.main()
