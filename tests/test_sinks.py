"""The desktop sinks and sources (sinks.py): the list in the device's state, a damaged one, the
sinks.conf the engine reads, the Desktop audio status row and PipeWire's rate and buffer. No Qt;
the strip menus and the Clock rows are in tests/test_desktop_window.py."""
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import config, hardware, sinks
from openface_mixer.devices import DEVICES
from openface_mixer.model import N_IN, N_PLAY

REPO = Path(__file__).resolve().parent.parent


def sink(ch, name="Music"):
    return {"kind": "sink", "ch": ch, "stereo": True, "name": name}


def source(ch, stereo=False, name="Mic"):
    return {"kind": "source", "ch": ch, "stereo": stereo, "name": name}


class ListTest(unittest.TestCase):
    dig = DEVICES["digiface"]

    def test_the_default_is_the_playback_sink_on_the_drivers_first_pair(self):
        self.assertEqual(sinks.default_list(self.dig),
                         [sink(0, "Openface Mixer Playback")])
        self.assertEqual(sinks.default_list(DEVICES["ff802usb"]),
                         [sink(0, "Openface Mixer Playback (Fireface 802 USB)")])
        raydat = DEVICES["raydat"]       # its PCM 1/2 are ADAT 1 1/2, mixer channels 4/5
        self.assertEqual(sinks.default_list(raydat)[0]["ch"], 4)
        for dev in DEVICES.values():
            entry = sinks.default_list(dev)[0]
            self.assertEqual(sinks.pcm_map(dev, entry), [(0, 1)] * 3, dev.key)
            self.assertEqual(sinks.checked(sinks.default_list(dev), dev), [entry], dev.key)

    def test_the_names_the_engine_gives_the_interfaces(self):
        src = "".join(p.read_text() for p in (REPO / "engine").glob("*.c"))
        names = dict(re.findall(r'\.key = "(\w+)",\s*\.name = "([^"]+)"', src))
        names.update(re.findall(r'BACKEND\("(\w+)", "([^"]+)"', src))
        for key, dev in DEVICES.items():
            if key != "digiface":       # its sink has no interface in its name
                self.assertEqual(sinks.engine_name(dev), names[key], key)

    def test_node_names_follow_the_channels(self):
        self.assertEqual(sinks.node_name(self.dig, sink(0, "Anything")),
                         "openface_mixer_playback")
        self.assertEqual(sinks.node_name(self.dig, sink(2)), "openface_mixer_play_3_4")
        self.assertEqual(sinks.node_name(self.dig, source(5)), "openface_mixer_in_6")
        self.assertEqual(sinks.node_name(self.dig, source(4, True)), "openface_mixer_in_5_6")
        ff802 = DEVICES["ff802"]
        self.assertEqual(sinks.node_name(ff802, sink(0)), "openface_mixer_playback_ff802")
        self.assertEqual(sinks.node_name(ff802, sink(8)), "openface_mixer_ff802_play_9_10")
        self.assertEqual(sinks.node_name(DEVICES["raydat"], sink(0)),
                         "openface_mixer_raydat_play_1_2")      # AES, not its first pair

    def test_descriptions_and_typed_names(self):
        self.assertEqual(sinks.description("Music"), "Openface Music")
        self.assertEqual(sinks.description("Openface Mixer Playback"), "Openface Mixer Playback")
        self.assertEqual(sinks.clean_name('  Chat\t"mic"\\ \x7f one\n'), "Chat mic one")
        self.assertEqual(sinks.clean_name(" \x01 "), "")
        long = sinks.clean_name("é" * 200)
        self.assertLessEqual(len(sinks.description(long).encode()), sinks.DESC_BYTES)
        self.assertGreater(len(long), 30)

    def test_pcm_channels_at_each_speed(self):
        # the Digiface: in mixer order; a channel of the second ADAT port leaves at 2x and 4x
        self.assertEqual(sinks.pcm_map(self.dig, sink(2)), [(2, 3)] * 3)
        self.assertEqual(sinks.pcm_map(self.dig, source(20)), [(20, -1), (-1, -1), (-1, -1)])
        # the phones: channel 32 at 1x only (16 at 2x, which is ADAT 3 1/2 at 1x)
        self.assertEqual(sinks.pcm_map(self.dig, sink(32)), [(32, 33), (-1, -1), (-1, -1)])
        self.assertEqual(sinks.pcm_map(self.dig, sink(16)), [(16, 17), (16, 17), (-1, -1)])
        raydat = DEVICES["raydat"]       # ADAT 1-4 first, then AES and S/PDIF
        self.assertEqual(sinks.pcm_map(raydat, sink(0)), [(32, 33), (16, 17), (8, 9)])
        self.assertEqual(sinks.pcm_map(raydat, source(2, True)), [(34, 35), (18, 19), (10, 11)])
        aio = DEVICES["aio"]             # AN, AES, S/PDIF, ADAT, then the phones (playback)
        self.assertEqual(sinks.pcm_map(aio, sink(8)), [(2, 3)] * 3)
        self.assertEqual(sinks.pcm_map(aio, sink(12)), [(6, 7)] * 3)
        self.assertEqual(sinks.pcm_map(aio, sink(6)), [(14, 15), (10, 11), (8, 9)])
        self.assertEqual(sinks.pcm_map(aio, source(6, True)), [(-1, -1)] * 3)
        self.assertEqual(sinks.pcm_map(aio, sink(2)), [(-1, -1)] * 3)   # the AI4S/AO4S board
        usb = DEVICES["ff802usb"]        # its inputs stream as the 22 playback channels do
        self.assertEqual(sinks.pcm_map(usb, source(0)), [(0, -1)] * 3)
        self.assertEqual([len(usb.pcm_channels("in", m)) for m in (1, 2, 4)], [22, 18, 14])

    def test_a_damaged_list(self):
        dev, default = self.dig, sinks.default_list(self.dig)
        for bad in (None, "sink", {"kind": "sink"}, 3):
            self.assertEqual(sinks.checked(bad, dev), default, bad)
        good = [sink(2), source(0), source(4, True)]
        junk = [
            "sink", None, {}, [2],
            {**sink(4), "kind": "monitor"}, {**sink(4), "ch": "4"}, {**sink(4), "ch": 4.0},
            {**sink(4), "ch": True}, {**sink(4), "ch": -2}, {**sink(4), "ch": N_PLAY},
            {**sink(4), "ch": N_PLAY - 1}, {**sink(4), "ch": 5}, {**sink(4), "stereo": 1},
            {**sink(4), "stereo": False}, {**sink(4), "name": 4}, {**sink(4), "name": " \t "},
            {k: v for k, v in sink(4).items() if k != "name"},
            {**source(0), "ch": N_IN}, {**source(1, True)},
            sink(3), source(5, True),                               # on channels taken already
            sink(50), source(40),           # channels the Digiface has at no sample rate
        ]
        lst = good[:1] + junk + good[1:]
        self.assertEqual(sinks.checked(lst, dev), good)
        self.assertEqual(sinks.checked([], dev), [])
        self.assertEqual(sinks.checked([{**sink(2), "name": 'A "b"', "extra": 1}], dev),
                         [sink(2, "A b")])
        many = [source(c) for c in range(N_IN)]
        self.assertEqual(len(sinks.checked(many, dev)), sinks.MAX)
        both = [sink(2), source(2, True)]           # a sink and a source on the same numbers
        self.assertEqual(sinks.checked(both, dev), both)

    def test_the_file_for_the_engine(self):
        text = sinks.file_text([sink(0, "Openface Mixer Playback"), sink(32),
                                source(20, name="Chat mic")], self.dig)
        self.assertEqual(text.splitlines(), [
            "# The desktop sinks and sources of Openface Mixer's engine. Written by the app:"
            " change them there.",
            "sink openface_mixer_playback 0 1 0 1 0 1 Openface Mixer Playback",
            "sink openface_mixer_play_33_34 32 33 -1 -1 -1 -1 Openface Music",
            "source openface_mixer_in_21 20 -1 -1 -1 -1 -1 Openface Chat mic"])
        self.assertEqual(sinks.file_text([], self.dig).count("\n"), 1)


class StateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(config, "CONFIG_DIR", Path(tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def load_with(self, value, dev=DEVICES["digiface"]):
        st, _ = config.load_state(dev)
        if value is ...:
            del st["sinks"]
        else:
            st["sinks"] = value
        config.state_file(dev).parent.mkdir(parents=True, exist_ok=True)
        config.state_file(dev).write_text(json.dumps(st))
        loaded, problem = config.load_state(dev)
        self.assertIsNone(problem)
        return loaded["sinks"]

    def test_a_missing_or_damaged_list_falls_back(self):
        dev = DEVICES["digiface"]
        self.assertEqual(config.load_state(dev)[0]["sinks"], sinks.default_list(dev))
        self.assertEqual(self.load_with(...), sinks.default_list(dev))
        self.assertEqual(self.load_with("sink"), sinks.default_list(dev))
        self.assertEqual(self.load_with([sink(2), "x", {"kind": 1}]), [sink(2)])
        self.assertEqual(self.load_with([]), [])
        raydat = DEVICES["raydat"]
        self.assertEqual(self.load_with({"a": 1}, raydat), sinks.default_list(raydat))

    def test_the_file_is_written_only_when_it_changes(self):
        dev = DEVICES["ff802"]
        st, _ = config.load_state(dev)
        self.assertTrue(config.save_sinks(st, dev))
        path = config.sinks_file(dev)
        self.assertEqual(path, config.CONFIG_DIR / "ff802" / "sinks.conf")
        self.assertEqual(path.read_text(), sinks.file_text(sinks.default_list(dev), dev))
        self.assertFalse(config.save_sinks(st, dev))
        st["sinks"].append(sink(8))
        self.assertTrue(config.save_sinks(st, dev))
        self.assertIn(" openface_mixer_ff802_play_9_10 8 9 ", path.read_text())
        path.write_bytes(b"\xff\xfe")                   # not text: written again
        self.assertTrue(config.save_sinks(st, dev))
        self.assertFalse(config.save_sinks(st, dev))


def done(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


SETTINGS = """Found "settings" metadata 32
update: id:0 key:'log.level' value:'2' type:''
update: id:0 key:'clock.rate' value:'48000' type:''
update: id:0 key:'clock.allowed-rates' value:'[ 48000 ]' type:''
update: id:0 key:'clock.quantum' value:'1024' type:''
update: id:0 key:'clock.min-quantum' value:'32' type:''
update: id:0 key:'clock.max-quantum' value:'2048' type:''
update: id:0 key:'clock.force-quantum' value:'256' type:''
update: id:0 key:'clock.force-rate' value:'0' type:''
"""


class PipeWireClockTest(unittest.TestCase):
    def test_reading(self):
        with mock.patch.object(hardware.subprocess, "run", return_value=done(SETTINGS)) as run:
            values = hardware.pw_clock()
        self.assertEqual(run.call_args.args[0], ["pw-metadata", "-n", "settings", "0"])
        self.assertEqual(run.call_args.kwargs["timeout"], 2)
        self.assertEqual(values["clock.force-rate"], 0)
        self.assertEqual(values["clock.force-quantum"], 256)
        self.assertEqual(values["clock.max-quantum"], 2048)
        self.assertNotIn("clock.allowed-rates", values)
        for answer in (done(code=255, stderr="can't connect: Host is down"), done("nothing")):
            with mock.patch.object(hardware.subprocess, "run", return_value=answer):
                self.assertIsNone(hardware.pw_clock())
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("pw-metadata", 2)):
            with mock.patch.object(hardware.subprocess, "run", side_effect=error):
                self.assertIsNone(hardware.pw_clock())

    def test_setting(self):
        with mock.patch.object(hardware.subprocess, "run", return_value=done()) as run:
            self.assertIsNone(hardware.set_pw_clock(hardware.FORCE_RATE, 96000))
            self.assertIsNone(hardware.set_pw_clock(hardware.FORCE_QUANTUM, 0))
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            ["pw-metadata", "-n", "settings", "0", "clock.force-rate", "96000"],
            ["pw-metadata", "-n", "settings", "0", "clock.force-quantum", "0"]])
        with mock.patch.object(hardware.subprocess, "run",
                               return_value=done(code=255, stderr="can't connect: Host is down")):
            self.assertEqual(hardware.set_pw_clock(hardware.FORCE_RATE, 48000),
                             "can't connect: Host is down")


try:
    from openface_mixer import settings_panel
except ImportError:                     # no PySide6
    settings_panel = None


@unittest.skipUnless(settings_panel, "needs PySide6")
class StatusRowTest(unittest.TestCase):
    dev = DEVICES["digiface"]

    def row(self, lst, rate=48000, **status):
        return settings_panel.desktop_audio(
            self.dev, lst, {"rate": rate, "sink_linked": True, **status})

    def test_the_row(self):
        default = sinks.default_list(self.dev)
        text, tip = self.row(default, pipewire=True, pw_nodes=1, pw_linked=1)
        self.assertEqual(text, "Linked to playback AD1 1/2")
        self.assertEqual(tip, "Openface Mixer Playback: playback AD1 1/2")
        self.assertEqual(self.row(default)[0], "Linked to playback AD1 1/2")   # an older engine
        two = default + [source(20, name="Chat & co")]
        text, tip = self.row(two, pipewire=True, pw_nodes=2, pw_linked=2)
        self.assertEqual(text, "2 linked")
        self.assertIn("Openface Chat &amp; co: input AD3 5", tip)
        text, tip = self.row(two, rate=96000, pipewire=True, pw_nodes=1, pw_linked=1)
        self.assertTrue(text.startswith("Linked to playback"))
        self.assertIn("Openface Chat &amp; co: not at this sample rate", tip)
        self.assertTrue(self.row(two, pipewire=True, pw_nodes=2, pw_linked=1)[0]
                        .endswith("1 of 2 linked"))
        self.assertTrue(self.row(default, pipewire=True, pw_nodes=1, pw_linked=0)[0]
                        .endswith("Not linked to the Digiface USB"))
        self.assertTrue(self.row(default, pipewire=False)[0].endswith("PipeWire not running"))
        self.assertTrue(self.row([], pipewire=True, pw_nodes=0, pw_linked=0)[0]
                        .endswith("None"))
        self.assertTrue(self.row([source(20)], rate=96000, pipewire=True, pw_nodes=0,
                                 pw_linked=0)[0].endswith("None at this rate"))

    def test_failure_messages(self):
        fail = settings_panel.write_failure
        self.assertEqual(fail("can't connect: Host is down", self.dev, "pw-metadata"),
                         "Not changed: PipeWire is not running")
        self.assertEqual(fail("missing", self.dev, "pw-metadata"),
                         "Not changed: pw-metadata is not installed")
        self.assertEqual(fail("timeout", self.dev, "pw-metadata"),
                         "Not changed: pw-metadata did not answer")


if __name__ == "__main__":
    unittest.main()
