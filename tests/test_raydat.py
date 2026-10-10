import copy
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import devices, hardware, model as m, raydat as rd

# amixer -c N contents, cut down to the RayDAT controls the GUI uses (snd-hdspm's names)
AMIXER = """\
numid=2,iface=MIXER,name='Clock Mode'
  ; type=ENUMERATED,access=rw---R--,values=1,items=2
  ; Item #0 'Master'
  ; Item #1 'AutoSync'
  : values=1
numid=3,iface=MIXER,name='Pref Sync Ref'
  ; type=ENUMERATED,access=rw---R--,values=1,items=8
  ; Item #0 'Word Clock'
  ; Item #1 'ADAT 1'
  ; Item #2 'ADAT 2'
  ; Item #3 'ADAT 3'
  ; Item #4 'ADAT 4'
  ; Item #5 'AES'
  ; Item #6 'SPDIF'
  ; Item #7 'Sync In'
  : values=1
numid=4,iface=MIXER,name='System Sample Rate'
  ; type=INTEGER,access=rw---R--,values=1,min=27000,max=207000,step=1
  : values=96000
numid=5,iface=MIXER,name='ADAT1 SyncCheck'
  ; type=ENUMERATED,access=r----R--,values=1,items=4
  ; Item #0 'No Lock'
  ; Item #1 'Lock'
  ; Item #2 'Sync'
  ; Item #3 'N/A'
  : values=2
numid=6,iface=MIXER,name='ADAT1 Frequency'
  ; type=ENUMERATED,access=r-------,values=1,items=3
  ; Item #0 'No Lock'
  ; Item #1 '32 kHz'
  ; Item #2 '96 kHz'
  : values=2
numid=7,iface=MIXER,name='TCO SyncCheck'
  ; type=ENUMERATED,access=r----R--,values=1,items=4
  ; Item #0 'No Lock'
  ; Item #1 'Lock'
  ; Item #2 'Sync'
  ; Item #3 'N/A'
  : values=3
numid=8,iface=MIXER,name='AES SyncCheck'
  ; type=ENUMERATED,access=r----R--,values=1,items=4
  ; Item #0 'No Lock'
  ; Item #1 'Lock'
  ; Item #2 'Sync'
  ; Item #3 'N/A'
  : values=0
numid=9,iface=MIXER,name='S/PDIF Out Professional'
  ; type=BOOLEAN,access=rw------,values=1
  : values=on
numid=10,iface=MIXER,name='Single Speed WordClock Out'
  ; type=BOOLEAN,access=rw------,values=1
  : values=off
numid=1,iface=HWDEP,name='Mixer'
  ; type=INTEGER,access=rw---R--,values=3,min=0,max=65535,step=1
  : values=0,0,0
"""

# the same card under snd-hdspe (develop branch): CARD controls, status as multi-value controls
AMIXER_HDSPE = """\
numid=5,iface=CARD,name='Running'
  ; type=BOOLEAN,access=r--v----,values=1
  : values=on
numid=2,iface=CARD,name='Firmware Build'
  ; type=INTEGER,access=r-------,values=1,min=0,max=0,step=1
  : values=12
numid=9,iface=CARD,name='Internal Frequency'
  ; type=ENUMERATED,access=rw------,values=1,items=9
  ; Item #0 '32 KHz'
  ; Item #1 '44.1 KHz'
  ; Item #2 '48 KHz'
  ; Item #3 '64 KHz'
  ; Item #4 '88.2 KHz'
  ; Item #5 '96 KHz'
  ; Item #6 '128 KHz'
  ; Item #7 '176.4 KHz'
  ; Item #8 '192 KHz'
  : values=2
numid=7,iface=HWDEP,name='Raw Sample Rate'
  ; type=INTEGER64,access=r--v----,values=2,min=0,max=0,step=0
  : values=48000000000,1000000
numid=10,iface=CARD,name='Current AutoSync Reference'
  ; type=ENUMERATED,access=r--v----,values=1,items=9
  ; Item #0 'WordClk'
  ; Item #1 'AES'
  ; Item #2 'S/PDIF'
  ; Item #3 'ADAT1'
  ; Item #4 'ADAT2'
  ; Item #5 'ADAT3'
  ; Item #6 'ADAT4'
  ; Item #7 'SyncIn'
  ; Item #8 'Intern'
  : values=3
numid=11,iface=CARD,name='Clock Mode'
  ; type=ENUMERATED,access=rw------,values=1,items=2
  ; Item #0 'AutoSync'
  ; Item #1 'Master'
  : values=0
numid=12,iface=CARD,name='Preferred AutoSync Reference'
  ; type=ENUMERATED,access=rw------,values=1,items=8
  ; Item #0 'WordClk'
  ; Item #1 'AES'
  ; Item #2 'S/PDIF'
  ; Item #3 'ADAT1'
  ; Item #4 'ADAT2'
  ; Item #5 'ADAT3'
  ; Item #6 'ADAT4'
  ; Item #7 'SyncIn'
  : values=3
numid=13,iface=CARD,name='AutoSync Status'
  ; type=ENUMERATED,access=r--v----,values=8,items=4
  ; Item #0 'No Lock'
  ; Item #1 'Lock'
  ; Item #2 'Sync'
  ; Item #3 'N/A'
  : values=0,3,1,2,0,0,0,0
numid=14,iface=CARD,name='AutoSync Frequency'
  ; type=ENUMERATED,access=r--v----,values=8,items=10
  ; Item #0 ''
  ; Item #1 '32 KHz'
  ; Item #2 '44.1 KHz'
  ; Item #3 '48 KHz'
  ; Item #4 '64 KHz'
  ; Item #5 '88.2 KHz'
  ; Item #6 '96 KHz'
  ; Item #7 '128 KHz'
  ; Item #8 '176.4 KHz'
  ; Item #9 '192 KHz'
  : values=0,0,3,3,0,0,0,0
numid=15,iface=CARD,name='S/PDIF In'
  ; type=ENUMERATED,access=rw------,values=1,items=3
  ; Item #0 'Optical'
  ; Item #1 'Coaxial'
  ; Item #2 'Internal'
  : values=1
numid=16,iface=CARD,name='S/PDIF Out Optical'
  ; type=BOOLEAN,access=rw------,values=1
  : values=off
numid=17,iface=CARD,name='ADAT1 Internal'
  ; type=BOOLEAN,access=rw------,values=1
  : values=on
numid=1,iface=HWDEP,name='Mixer'
  ; type=INTEGER,access=rw---R--,values=3,min=0,max=65535,step=1
  : values=0,0,0
"""

CARDS = """\
 0 [PCH            ]: HDA-Intel - HDA Intel PCH
                      HDA Intel PCH at 0xf7f10000 irq 32
 1 [HDSPMx1a2b3c   ]: HDSPM - RME RayDAT_1a2b3c
                      RME RayDAT S/N 0x1a2b3c at 0xf7c00000, irq 16
"""


class ChannelTest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual([rd.n_channels(mode) for mode in (1, 2, 4)], [36, 20, 12])
        for kind, limit in (("in", m.N_IN), ("play", m.N_PLAY), ("out", m.N_OUT)):
            self.assertEqual(len(rd.channels(kind, 1)), limit)

    def test_labels(self):
        self.assertEqual(rd.chan_label("in", 0, 1), "AES L")
        self.assertEqual(rd.chan_label("out", 3, 1), "SPDIF R")
        self.assertEqual(rd.chan_label("in", 4, 1), "A1 1")
        self.assertEqual(rd.chan_label("in", 35, 1), "A4 8")
        self.assertEqual(rd.chan_label("in", 19, 2), "A4 4")       # packed at double speed
        self.assertEqual(rd.chan_label("play", 11, 4), "A4 2")
        self.assertEqual(rd.pair_label("out", 0, 1), "AES")
        self.assertEqual(rd.pair_label("out", 2, 1), "SPDIF")
        self.assertEqual(rd.pair_label("out", 12, 1), "A2 1/2")


class StatusTest(unittest.TestCase):
    def test_amixer_switches(self):
        c = hardware.parse_amixer_contents(AMIXER)
        self.assertEqual(c["S/PDIF Out Professional"]["value"], 1)
        self.assertEqual(c["Single Speed WordClock Out"]["value"], 0)
        self.assertTrue(c["Clock Mode"]["rw"])
        self.assertFalse(c["ADAT1 SyncCheck"]["rw"])

    def test_decode(self):
        st = rd.decode_status(hardware.parse_amixer_contents(AMIXER))
        self.assertEqual(st["source"], "AutoSync, prefers ADAT 1")
        self.assertEqual(st["rate"], 96000)
        # TCO (no module: N/A) is left out; AES has no lock, so no rate
        self.assertEqual(st["inputs"], [("AES", "No Lock", None), ("ADAT 1", "Sync", "96 kHz")])

    def test_decode_hdspe(self):
        c = hardware.parse_amixer_contents(AMIXER_HDSPE)
        self.assertEqual(c["AutoSync Status"]["values"], [0, 3, 1, 2, 0, 0, 0, 0])
        self.assertEqual(c["Raw Sample Rate"]["values"], [48000000000, 1000000])
        self.assertEqual(c["Clock Mode"]["numid"], 11)
        st = rd.decode_status(c)
        self.assertEqual(st["driver"], rd.HDSPE)
        self.assertEqual(st["source"], "AutoSync, on ADAT 1")
        self.assertEqual(st["rate"], 48000)
        self.assertTrue(st["running"])
        self.assertEqual(st["firmware"], 12)
        # AES (N/A) left out; word clock unlocked; frequencies as kHz
        self.assertEqual(st["inputs"], [("Word Clock", "No Lock", None), ("S/PDIF", "Lock", "48 kHz"),
                                        ("ADAT 1", "Sync", "48 kHz"), ("ADAT 2", "No Lock", None),
                                        ("ADAT 3", "No Lock", None), ("ADAT 4", "No Lock", None),
                                        ("Sync In", "No Lock", None)])
        self.assertEqual(rd.control(c, "pref")[0], "Preferred AutoSync Reference")
        self.assertEqual(rd.control(c, "spdif_in")[1]["value"], 1)
        self.assertEqual(rd.display_item("ADAT1"), "ADAT 1")
        self.assertEqual(rd.display_item("44.1 KHz"), "44.1 kHz")

    def test_profiles(self):
        hdspm = hardware.parse_amixer_contents(AMIXER)
        self.assertEqual(rd.profile(hdspm), rd.HDSPM)
        self.assertEqual(rd.decode_status(hdspm)["driver"], rd.HDSPM)
        self.assertEqual(rd.control(hdspm, "internal"), (None, None))   # not in the fixture
        self.assertEqual(rd.control(hdspm, "spdif_in"), (None, None))

    def test_driver_usable(self):
        self.assertTrue(rd.driver_usable(rd.HDSPM, 0x10ee))
        self.assertFalse(rd.driver_usable(rd.HDSPM, rd.VENDOR_RME))
        self.assertTrue(rd.driver_usable(rd.HDSPE, rd.VENDOR_RME))

    def test_write_by_numid(self):
        hw = hardware.Hardware(rd.CARD_NAME)
        hw.card, hw.ctrls = 1, hardware.parse_amixer_contents(AMIXER_HDSPE)
        with mock.patch.object(hardware.subprocess, "run") as run:
            hw.write("Clock Mode", 1)
            hw.write("Unknown", 0)
        self.assertEqual(run.call_args_list[0].args[0][4:], ["cset", "numid=11", "1"])
        self.assertEqual(run.call_args_list[1].args[0][4:], ["cset", "name=Unknown", "0"])

    def test_not_found(self):
        self.assertIsNone(rd.decode_status(None))
        self.assertIsNone(rd.decode_status({}))


class DeviceTest(unittest.TestCase):
    def test_device(self):
        dev = devices.DEVICES["raydat"]
        self.assertEqual(dev.service, "openface-mixer-engine@raydat.service")
        self.assertEqual(dev.shm_name, "openface-mixer-raydat")
        self.assertEqual(dev.phones_pair(1), 0)             # no headphones: opens on AES
        self.assertEqual(dev.channels("out", 2), list(range(20)))
        self.assertEqual(dev.pair_label("play", 4, 1), "A1 1/2")

    def test_detect(self):
        with mock.patch.object(Path, "read_text", return_value=CARDS):
            self.assertEqual(hardware.Hardware.find_card(rd.CARD_NAME), 1)
            self.assertIsNone(hardware.Hardware.find_card())       # no Digiface
            self.assertTrue(devices.DEVICES["raydat"].present())
            self.assertEqual(hardware.Hardware.card_driver(1), "HDSPM")
            self.assertEqual(hardware.Hardware.card_driver(0), "HDA-Intel")
            self.assertFalse(devices.DEVICES["digiface"].present())


class OldMixTest(unittest.TestCase):
    """Mixes saved before the mix model grew to 36 channels still load."""

    def old_state(self):
        st = m.default_state(16)
        for key in ("stereo", "mute"):
            st[key]["in"] = st[key]["in"][:16 if key == "stereo" else 32]
            st[key]["play"] = st[key]["play"][:17 if key == "stereo" else 34]
        st["sends"]["in"] = [ch[:17] for ch in st["sends"]["in"][:32]]
        st["sends"]["play"] = [ch[:17] for ch in st["sends"]["play"][:34]]
        st["out"] = st["out"][:17]
        st["sends"]["in"][31][16] = [-3.0, 0.0]
        return st

    def check_full(self, st):
        self.assertEqual(len(st["mute"]["in"]), m.N_IN)
        self.assertEqual(len(st["stereo"]["play"]), m.N_PLAY // 2)
        self.assertEqual(len(st["sends"]["play"]), m.N_PLAY)
        self.assertTrue(all(len(ch) == m.N_PAIRS for ch in st["sends"]["in"]))
        self.assertEqual(len(st["out"]), m.N_PAIRS)
        self.assertEqual(st["sends"]["in"][31][16], [-3.0, 0.0])
        m.compute_matrix(st)
        m.compute_out_gains(st)

    def test_upgrade_state(self):
        self.check_full(m.upgrade_state(self.old_state(), 16))

    def test_apply_old_preset(self):
        mix = m.extract_mix(self.old_state())
        st = m.apply_mix(m.default_state(16), copy.deepcopy(mix))
        self.check_full(st)


if __name__ == "__main__":
    unittest.main()
