import copy
import importlib.util
import os
import struct
import subprocess
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest import mock

from openface_mixer import config, devices, hardware, hdspe as rd, model as m

HAVE_QT = importlib.util.find_spec("PySide6") is not None

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
 2 [HDSPe         ]: HDSPe - RME AIO Pro_00012345
                      RME AIO Pro S/N 00012345 at 0xf7b00000 irq 17
"""


TWO_CARDS = CARDS + """\
 3 [HDSPMx4d5e6f   ]: HDSPM - RME RayDAT_4d5e6f
                      RME RayDAT S/N 0x4d5e6f at 0xf7a00000, irq 18
"""

RAYDAT, AIO, AES = rd.MODELS["raydat"], rd.MODELS["aio"], rd.MODELS["aes"]


def m_default_mix():
    st = m.default_state(0)
    return m.compute_matrix(st), m.compute_out_gains(st)


class ChannelTest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual([RAYDAT.n_channels(mode) for mode in (1, 2, 4)], [36, 20, 12])
        for kind, limit in (("in", m.N_IN), ("play", m.N_PLAY), ("out", m.N_OUT)):
            self.assertEqual(len(RAYDAT.channels(kind, 1)), limit)

    def test_labels(self):
        self.assertEqual(RAYDAT.chan_label("in", 0, 1), "AES L")
        self.assertEqual(RAYDAT.chan_label("out", 3, 1), "SPDIF R")
        self.assertEqual(RAYDAT.chan_label("in", 4, 1), "A1 1")
        self.assertEqual(RAYDAT.chan_label("in", 35, 1), "A4 8")
        self.assertEqual(RAYDAT.chan_label("in", 19, 2), "A4 4")       # packed at double speed
        self.assertEqual(RAYDAT.chan_label("play", 11, 4), "A4 2")
        self.assertEqual(RAYDAT.pair_label("out", 0, 1), "AES")
        self.assertEqual(RAYDAT.pair_label("out", 2, 1), "SPDIF")
        self.assertEqual(RAYDAT.pair_label("out", 12, 1), "A2 1/2")

    def test_aio(self):
        # the driver's channel maps: no phones inputs, ADAT packed from 12 at every speed
        self.assertEqual(AIO.channels("in", 1), [0, 1] + list(range(8, 20)))
        self.assertEqual(AIO.channels("out", 2), [0, 1, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
        self.assertEqual(AIO.channels("play", 4), [0, 1, 6, 7, 8, 9, 10, 11, 12, 13])
        self.assertEqual([AIO.pair_label("out", c, 1) for c in (0, 6, 8, 10, 12, 18)],
                         ["AN 1/2", "PH", "AES", "SPDIF", "A 1/2", "A 7/8"])
        self.assertEqual(AIO.chan_label("in", 19, 1), "A 8")
        dev = devices.DEVICES["aio"]
        self.assertEqual(dev.phones_pair(1), 3)
        self.assertEqual(dev.pcm_channels("play", 1)[:2], [0, 1])     # the playback sink: AN 1/2
        self.assertEqual(devices.DEVICES["aiopro"].channels("in", 1), AIO.channels("in", 1))

    def test_aes(self):
        self.assertEqual(AES.channels("out", 4), list(range(16)))
        self.assertEqual(AES.chan_label("in", 15, 1), "AES 16")
        self.assertEqual(AES.pair_label("in", 2, 1), "AES 3/4")
        # snd-hdspm's AES32 status controls
        self.assertEqual(AES.status_inputs[1], ("AES 1", "AES1 Sync Check", "AES1 Frequency"))
        self.assertEqual(AES.status_inputs[-1], ("Sync In", "SYNC IN Sync Check",
                                                 "SYNC IN Frequency"))
        self.assertEqual(AIO.status_inputs[3], ("ADAT", "ADAT SyncCheck", "ADAT Frequency"))


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

    def test_rate_under_hdspm(self):
        # the measured clock is at single speed; the rate that was set holds the speed
        c = hardware.parse_amixer_contents(AMIXER)
        c["Internal Clock"] = {"value": 5, "values": [5], "rw": True, "numid": 1,
                               "items": [f"{r / 1000:g} kHz" for r in rd.HDSPM_RATES]}
        for measured, item, rate in ((47998, 2, 48000), (48001, 5, 96000), (44099, 5, 88200),
                                     (44100, 8, 176400), (44101, 2, 44100), (96003, 5, 96000),
                                     (46000, 2, 46000), (0, 5, 0)):
            c["System Sample Rate"]["value"] = measured
            c["Internal Clock"]["value"] = item
            self.assertEqual(rd.decode_status(c)["rate"], rate)
        self.assertEqual(rd.hdspm_rate(47998, None), 48000)

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
        self.assertEqual(rd.display_item("AES3"), "AES 3")
        self.assertEqual(rd.display_item("44.1 KHz"), "44.1 kHz")

    def test_profiles(self):
        hdspm = hardware.parse_amixer_contents(AMIXER)
        self.assertEqual(rd.profile(hdspm), rd.HDSPM)
        self.assertEqual(rd.decode_status(hdspm)["driver"], rd.HDSPM)
        self.assertEqual(rd.control(hdspm, "internal"), (None, None))   # not in the fixture
        self.assertEqual(rd.control(hdspm, "spdif_in"), (None, None))

    def test_aio_hdspm(self):
        # snd-hdspm names the AIO's clock controls like the AES32's, not the RayDAT's
        c = {"System Clock Mode": {"items": ["Master", "AutoSync"], "value": 1, "rw": True},
             "Preferred Sync Reference": {"items": ["Word Clock", "ADAT"], "value": 1,
                                          "rw": True},
             "Input Level": {"items": ["Lo Gain", "+4 dBu", "-10 dBV"], "value": 0, "rw": True},
             "ADAT SyncCheck": {"items": ["No Lock", "Lock", "Sync", "N/A"], "value": 2},
             "ADAT Frequency": {"items": ["No Lock", "48 kHz"], "value": 1}}
        st = rd.decode_status(c, AIO)
        self.assertEqual(st["source"], "AutoSync, prefers ADAT")
        self.assertEqual(st["inputs"], [("ADAT", "Sync", "48 kHz")])
        self.assertEqual(rd.control(c, "in_level")[0], "Input Level")
        self.assertEqual(rd.control(c, "clock")[0], "System Clock Mode")

    def test_driver_usable(self):
        self.assertTrue(rd.driver_usable(rd.HDSPM, 0x10ee))
        self.assertFalse(rd.driver_usable(rd.HDSPM, rd.VENDOR_RME))
        self.assertTrue(rd.driver_usable(rd.HDSPE, rd.VENDOR_RME))

    def test_clock_mode_in_effect(self):
        # what the Clock light compares with the chosen mode
        hdspm = hardware.parse_amixer_contents(AMIXER)
        self.assertEqual(rd.decode_status(hdspm)["mode"], "AutoSync")      # ADAT 1 in sync
        hdspm["ADAT1 SyncCheck"]["value"] = hdspm["ADAT1 SyncCheck"]["values"][0] = 0
        self.assertIsNone(rd.decode_status(hdspm)["mode"])                 # nothing to follow
        hdspm["Clock Mode"]["value"] = hdspm["Clock Mode"]["values"][0] = 0
        self.assertEqual(rd.decode_status(hdspm)["mode"], "Master")
        c = hardware.parse_amixer_contents(AMIXER_HDSPE)
        self.assertEqual(rd.decode_status(c)["mode"], "AutoSync")          # on ADAT 1
        cur = c["Current AutoSync Reference"]
        cur["value"] = cur["values"][0] = cur["items"].index("Intern")
        st = rd.decode_status(c)
        self.assertEqual((st["mode"], st["source"]), (None, "AutoSync, on Internal"))

    def test_limited(self):
        gains = array("f", bytes(4 * 4 * 3))
        out_gains = array("f", [1.0, m.db2lin(6.0), 0.0, 1.5])
        gains[0 * 3 + 0] = 1.99          # +6 dB send, output at 0 dB: fine
        gains[1 * 3 + 0] = 1.0           # 0 dB into an output at +6 dB: just fits
        gains[1 * 3 + 1] = 1.2           # more than the card can do
        gains[2 * 3 + 2] = 1.99          # muted output
        gains[3 * 3 + 2] = 1.5
        self.assertEqual(rd.limited(gains, out_gains, 3), {(1, 1), (3, 2)})
        self.assertEqual(rd.limited(*m_default_mix(), m.N_SRC), set())

    def test_run(self):
        done = subprocess.CompletedProcess([], 0, "out", "")
        for effect, want in ((FileNotFoundError(), (None, "missing")),
                             (subprocess.TimeoutExpired("amixer", 2), (None, "timeout")),
                             (PermissionError(13, "Permission denied"),
                              (None, "[Errno 13] Permission denied")),
                             ([done], ("out", None)),
                             ([subprocess.CompletedProcess([], 1, "", "x\namixer: busy\n")],
                              (None, "amixer: busy")),
                             ([subprocess.CompletedProcess([], 1, "", "")],
                              (None, "amixer failed"))):
            with mock.patch.object(hardware.subprocess, "run", side_effect=effect) as run:
                self.assertEqual(hardware.run(["amixer"]), want)
            self.assertEqual(run.call_args.kwargs["timeout"], 2)
            self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_read_says_why(self):
        hw = hardware.Hardware(RAYDAT.card)
        with mock.patch.object(Path, "read_text", return_value=CARDS), \
                mock.patch.object(hardware.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertIsNone(hw.read())
        self.assertEqual((hw.card, hw.error), (1, "missing"))
        with mock.patch.object(Path, "read_text", return_value=""):
            self.assertIsNone(hw.read())
        self.assertEqual((hw.card, hw.error), (None, None))

    def test_write_by_numid(self):
        hw = hardware.Hardware(RAYDAT.card)
        hw.card, hw.ctrls = 1, hardware.parse_amixer_contents(AMIXER_HDSPE)
        refused = subprocess.CompletedProcess(
            [], 1, "", "amixer: Control hw:1 element write error: Device or resource busy\n")
        with mock.patch.object(hardware.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, "", ""), refused,
                subprocess.TimeoutExpired("amixer", 2)]) as run:
            self.assertIsNone(hw.write("Clock Mode", 1))
            self.assertIn("busy", hw.write("Unknown", 0))
            self.assertEqual(hw.write("Clock Mode", 0), "timeout")
        self.assertEqual(run.call_args_list[0].args[0][4:], ["cset", "numid=11", "1"])
        self.assertEqual(run.call_args_list[1].args[0][4:], ["cset", "name=Unknown", "0"])

    def test_set_card_profile(self):
        with mock.patch.object(hardware.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertEqual(hardware.set_card_profile("card", "pro-audio"), "missing")

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
            self.assertEqual(hardware.Hardware.find_card(RAYDAT.card), 1)
            self.assertIsNone(hardware.Hardware.find_card())       # no Digiface
            self.assertTrue(devices.DEVICES["raydat"].present())
            self.assertEqual(hardware.Hardware.card_driver(1), "HDSPM")
            self.assertEqual(hardware.Hardware.card_driver(0), "HDA-Intel")
            self.assertFalse(devices.DEVICES["digiface"].present())
            # "RME AIO" is not the start of "RME AIO Pro"
            self.assertFalse(devices.DEVICES["aio"].present())
            self.assertEqual(hardware.Hardware.find_card(AIO.card + " Pro"), 2)
            self.assertTrue(devices.DEVICES["aiopro"].present())


    def test_use_anyway(self):
        dev = devices.DEVICES["aio"]
        st = m.default_state(dev.phones_pair(1))
        dev.upgrade_settings(st)
        self.assertEqual(st["hw"], {"use_anyway": False, "card": None})
        self.assertIsNone(config.device_block(st, dev))        # nothing for the engine
        self.assertNotIn(b"OFD2", config.matrix_file_bytes(st, dev))
        st["hw"] = {"use_anyway": True, "junk": 1, "card": 7}
        dev.upgrade_settings(st)
        self.assertEqual(st["hw"], {"use_anyway": True, "card": None})
        self.assertEqual(config.device_block(st, dev)[0], 1)   # the engine's dev_config
        self.assertIn(b"OFD2", config.matrix_file_bytes(st, dev))

    def test_second_card(self):
        self.assertEqual(hardware.Hardware.find_cards(""), [])
        with mock.patch.object(Path, "read_text", return_value=TWO_CARDS):
            self.assertEqual(hardware.Hardware.find_cards(RAYDAT.card),
                             [(1, "RME RayDAT_1a2b3c"), (3, "RME RayDAT_4d5e6f")])
            self.assertEqual(hardware.Hardware.find_card(RAYDAT.card), 1)
            self.assertEqual(hardware.Hardware.find_card(RAYDAT.card, "RME RayDAT_4d5e6f"), 3)
            # the chosen card was taken out: the one that is there
            self.assertEqual(hardware.Hardware.find_card(RAYDAT.card, "RME RayDAT_777777"), 1)
            hw = hardware.Hardware(RAYDAT.card)
            hw.exact = "RME RayDAT_4d5e6f"
            with mock.patch.object(hardware.subprocess, "run",
                                   return_value=subprocess.CompletedProcess([], 0, AMIXER, "")):
                hw.read()
            self.assertEqual(hw.card, 3)
        # the engine gets the name as a C string in the command slots
        dev = devices.DEVICES["raydat"]
        st = m.default_state(0)
        dev.upgrade_settings(st)
        st["hw"]["card"] = "RME RayDAT_4d5e6f"
        config_word, cmds = config.device_block(st, dev)
        raw = struct.pack(f"<{len(cmds)}I", *cmds)
        self.assertEqual((config_word, raw[:raw.index(0)]), (0, b"RME RayDAT_4d5e6f"))
        st["hw"]["card"] = "x" * 40                         # the card info's name is 32 bytes
        self.assertEqual(dev.device_words(st)[1], [0x78787878] * 7 + [0x00787878])

    def test_missing_hint(self):
        aio, aiopro, raydat = (devices.DEVICES[k] for k in ("aio", "aiopro", "raydat"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, vendor, device, rev in (("0000:00:1f.3", 0x8086, 0xa348, 0x10),
                                              ("0000:03:00.0", 0x1d18, 0x3fc6, 212),
                                              ("0000:04:00.0", 0x10ee, 0x0007, 211)):
                (root / name).mkdir()
                for key, v in (("vendor", vendor), ("device", device), ("revision", rev)):
                    (root / name / key).write_text(f"0x{v:04x}\n")
            self.assertTrue(devices.find_pci_card(aio.model.pci_revisions, root))
            self.assertTrue(devices.find_pci_card(aiopro.model.pci_revisions, root))
            self.assertFalse(devices.find_pci_card(raydat.model.pci_revisions, root))
            self.assertFalse(devices.find_pci_card((212,), root / "none"))
        with mock.patch.object(hardware.Hardware, "find_card", return_value=None):
            with mock.patch.object(devices, "find_pci_card", return_value=True):
                self.assertIn("no driver", aio.missing_hint())
            with mock.patch.object(devices, "find_pci_card", return_value=False):
                self.assertEqual(aio.missing_hint(), "")
        # an AIO Pro under snd-hdspm is an "RME AIO" card
        cards = CARDS.replace("HDSPe - RME AIO Pro_00012345", "HDSPM - RME AIO_1a2b3d")
        with mock.patch.object(Path, "read_text", return_value=cards):
            self.assertIn("gives it that name", aiopro.missing_hint())
        with mock.patch.object(Path, "read_text", return_value=CARDS):      # and the reverse
            self.assertIn("is an AIO Pro", aio.missing_hint())


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


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class PanelTest(unittest.TestCase):
    """What the settings panel and a strip show (Qt widgets, drawn nowhere)."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def keep(self, widget):
        """Have the widget deleted when its test is over: one that is still there when the
        interpreter exits can crash Qt on the way out."""
        import shiboken6
        self.addCleanup(shiboken6.delete, widget)
        return widget

    def panel(self, key="raydat", **hw):
        from openface_mixer import settings_panel
        dev = devices.DEVICES[key]
        st = m.default_state(dev.phones_pair(1))
        dev.upgrade_settings(st)
        st["hw"].update(hw)
        with mock.patch.object(settings_panel.updater, "install_source", return_value=None):
            return self.keep(settings_panel.SettingsPanel(hardware.Hardware(dev.alsa_card),
                                                          dev, st))

    @staticmethod
    def lit(light):
        return light.ON in light.text()

    def test_clock_light_in_autosync(self):
        p = self.panel()
        ctrls = hardware.parse_amixer_contents(AMIXER)
        p.hw.card = 1
        with mock.patch.object(Path, "read_text", return_value=CARDS):
            p.update_hdspe(ctrls)
            self.assertEqual(p.rd_combos["clock"].currentText(), "AutoSync")
            self.assertTrue(self.lit(p.cur_src))               # following ADAT 1
            self.assertIn("this source", p.cur_src.toolTip())
            ctrls["ADAT1 SyncCheck"]["value"] = ctrls["ADAT1 SyncCheck"]["values"][0] = 0
            p.update_hdspe(ctrls)
            self.assertFalse(self.lit(p.cur_src))              # no input to follow
            self.assertIn("AutoSync, prefers ADAT 1", p.cur_src.toolTip())
            ctrls["Clock Mode"]["value"] = ctrls["Clock Mode"]["values"][0] = 0
            p.update_hdspe(ctrls)
            self.assertTrue(self.lit(p.cur_src))               # Master

    def test_read_failures(self):
        p = self.panel()
        for card, error, ctrls, want in ((None, None, None, "Card not found"),
                                         (1, "missing", None, "amixer is not installed"),
                                         (1, "timeout", None, "amixer did not answer"),
                                         (1, "amixer: no", None, "Can't read settings"),
                                         (1, None, {}, "Can't read settings")):
            p.hw.card, p.hw.error = card, error
            p.update_hdspe(ctrls)
            self.assertIn(want, p.rate.text())
        self.assertIn("ALSA controls", p.rate.toolTip())
        p.hw.card = 1
        with mock.patch.object(Path, "read_text", return_value=CARDS):
            p.update_hdspe(hardware.parse_amixer_contents(AMIXER))
        self.assertEqual((p.rate.text(), p.rate.toolTip()), ("96 kHz", ""))

    def test_refused_setting_says_why(self):
        from openface_mixer import settings_panel
        p = self.panel()
        said = []
        p.notice.connect(said.append)
        for why in ("amixer: Control hw:1 element write error: Device or resource busy",
                    "missing", "timeout", None):
            with mock.patch.object(p.hw, "write", return_value=why):
                p._write("Clock Mode", 1)
        self.assertEqual(len(said), 3)
        self.assertIn("playback and recording are open in two programs", said[0])
        self.assertEqual(said[1:], ["Not changed: amixer is not installed",
                                    "Not changed: amixer did not answer"])
        self.assertEqual(settings_panel.write_failure("timeout", devices.DEFAULT, "pactl"),
                         "Not changed: pactl did not answer")
        # on other interfaces the reason is the end of what amixer says
        self.assertEqual(settings_panel.write_failure(
            "amixer: Control hw:0 element write error: Device or resource busy",
            devices.DEFAULT), "Not changed: Device or resource busy")

    def test_use_anyway(self):
        p = self.panel("aio")
        status = {"hw": "wrong-driver", "hw_nodes": 0, "hw_levels": 0, "processing": True,
                  "sink_linked": False}
        p.update_hw_mixer(status)
        self.assertFalse(p.use_btn.isHidden())
        self.assertIn("Check driver", p.hw_label.text())
        self.assertIn("Use anyway", p.status_note.text())
        changed = []
        p.hw_changed.connect(lambda: changed.append(1))
        p.use_btn.click()
        self.assertEqual((p.st["hw"]["use_anyway"], changed), (True, [1]))
        p.update_hw_mixer(dict(status, hw="active"))
        self.assertTrue(p.use_btn.isHidden())
        p.update_hw_mixer(dict(status, hw="wrong-driver"))
        p.update_hw_mixer(None)
        self.assertTrue(p.use_btn.isHidden())

    def test_doubted_driver_turns_settings_off(self):
        ctrls = hardware.parse_amixer_contents(AMIXER)
        for anyway in (False, True):
            p = self.panel(use_anyway=anyway)
            p.hw.card = 1
            with mock.patch.object(Path, "read_text", return_value=CARDS), \
                    mock.patch.object(hardware.Hardware, "pci_vendor",
                                      return_value=rd.VENDOR_RME):
                p.update_hdspe(ctrls)
            self.assertEqual(p.rd_combos["clock"].isEnabled(), anyway)
            self.assertEqual("Settings are off" in p.rd_info.text(), not anyway)

    def test_card_choice_only_with_several(self):
        p = self.panel()
        chosen = []
        p.card_chosen.connect(lambda: chosen.append(1))
        with mock.patch.object(Path, "read_text", return_value=CARDS):
            p.update_hdspe(None)
        self.assertTrue(p.card_combo.isHidden())
        p.hw.card = 1
        with mock.patch.object(Path, "read_text", return_value=TWO_CARDS):
            p.update_hdspe(None)
        self.assertFalse(p.card_combo.isHidden())
        self.assertEqual(p.card_combo.currentText(), "RME RayDAT_1a2b3c")    # the one in use
        p.card_combo.setCurrentIndex(1)
        p.card_combo.activated.emit(1)
        self.assertEqual((p.st["hw"]["card"], p.hw.exact, chosen),
                         ("RME RayDAT_4d5e6f", "RME RayDAT_4d5e6f", [1]))
        self.assertTrue(self.panel("aio").card_combo.isHidden())

    def test_limited_readout(self):
        from openface_mixer import widgets
        s = self.keep(widgets.Strip("in:0", "AN 1", [0], "in"))
        plain = s.value.styleSheet(), s.value.toolTip()
        s.set_limited("Held at +6 dB")
        self.assertNotEqual(s.value.styleSheet(), plain[0])
        self.assertEqual(s.value.toolTip(), "Held at +6 dB")
        s.set_limited("")
        self.assertEqual((s.value.styleSheet(), s.value.toolTip()), plain)


if __name__ == "__main__":
    unittest.main()
