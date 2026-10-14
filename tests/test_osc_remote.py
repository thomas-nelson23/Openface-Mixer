import struct
import unittest

from openface_mixer import control_room as cr
from openface_mixer import model as m
from openface_mixer import osc_remote as osc
from openface_mixer.devices import DEVICES

DIGIFACE = DEVICES["digiface"]


def state():
    st = m.default_state(DIGIFACE.phones_pair(1))
    cr.control_room(st, DIGIFACE.phones_pair(1))
    return st


def apply(st, address, *args):
    return osc.apply(st, DIGIFACE, 1, address, list(args))


class CodecTest(unittest.TestCase):
    def test_spec_example(self):
        # OSC 1.0 specification example: "/oscillator/4/frequency" ,f 440.0
        self.assertEqual(osc.encode_message("/oscillator/4/frequency", 440.0),
                         b"/oscillator/4/frequency\0,f\0\0\x43\xdc\0\0")

    def test_round_trip(self):
        data = osc.encode_message("/input/3/name", "AD1 4")
        self.assertEqual(osc.decode(data), [("/input/3/name", ["AD1 4"])])
        data = osc.encode_message("/mix/in/0/2/faderlin", 0.5)
        self.assertEqual(osc.decode(data), [("/mix/in/0/2/faderlin", [0.5])])

    def test_int_and_bundle(self):
        msg = b"/a\0\0,i\0\0" + struct.pack(">i", 7)
        packets = osc.encode_bundles([msg, osc.encode_message("/b", 1.0)])
        self.assertEqual(len(packets), 1)
        self.assertEqual(osc.decode(packets[0]), [("/a", [7]), ("/b", [1.0])])

    def test_bundles_split_by_size(self):
        msgs = [osc.encode_message(f"/mix/in/{i}/0/faderlin", 0.5) for i in range(200)]
        packets = osc.encode_bundles(msgs, max_size=1400)
        self.assertGreater(len(packets), 1)
        self.assertTrue(all(len(p) <= 1400 for p in packets))
        self.assertEqual(sum(len(osc.decode(p)) for p in packets), 200)

    def test_garbage_raises_value_error(self):
        for bad in (b"", b"hello", b"/a\0\0,f\0\0\x01", b"#bundle\0" + b"\0" * 8 + b"\0\0\0\x50"):
            with self.assertRaises(ValueError):
                osc.decode(bad)


class FaderCurveTest(unittest.TestCase):
    def test_known_points(self):
        self.assertAlmostEqual(osc.db_to_faderlin(0.0), 836 / 1023, places=3)
        self.assertAlmostEqual(osc.db_to_faderlin(-6.0), 649 / 1023, places=3)
        self.assertEqual(osc.db_to_faderlin(None), 0.0)
        self.assertEqual(osc.db_to_faderlin(m.NEG_INF), 0.0)
        self.assertEqual(osc.db_to_faderlin(10.0), 1.0)

    def test_round_trip(self):
        for db in (-60.0, -30.0, -10.0, -6.0, -1.5, 0.0, 3.0, 6.0):
            self.assertAlmostEqual(osc.faderlin_to_db(osc.db_to_faderlin(db)), db, places=3)
        self.assertEqual(osc.faderlin_to_db(0.0), m.NEG_INF)


class SnapshotTest(unittest.TestCase):
    def test_digiface_channels_and_names(self):
        st = state()
        s = osc.snapshot(st, DIGIFACE, 1, set(), True)
        self.assertEqual(s["/status/device"], DIGIFACE.name)
        self.assertEqual(s["/status/connection"], 1.0)
        self.assertEqual(s["/input/0/name"], "AD1 1")            # inputs start mono
        self.assertEqual(s["/input/0/stereo"], 0.0)
        self.assertNotIn("/input/32/name", s)                   # 32 inputs at single speed
        self.assertEqual(s["/playback/0/name"], "AD1 1/2")       # playback starts stereo
        self.assertEqual(s["/playback/0/stereo"], 1.0)
        self.assertEqual(s["/output/32/name"], "Phones")
        self.assertIn("/output/33/faderlin", s)
        self.assertNotIn("/output/34/name", s)

    def test_sends_and_masters(self):
        st = state()
        st["sends"]["in"][3][2] = [-6.0, 0.0]
        st["out"][16]["gain"] = None
        st["out"][1]["mute"] = True
        s = osc.snapshot(st, DIGIFACE, 1, set(), True)
        self.assertAlmostEqual(s["/mix/in/3/4/faderlin"], 649 / 1023, places=3)
        self.assertEqual(s["/mix/in/3/0/faderlin"], 0.0)
        self.assertAlmostEqual(s["/mix/pb/0/0/faderlin"], 836 / 1023, places=3)   # play 1 -> out 1
        self.assertEqual(s["/output/32/faderlin"], 0.0)
        self.assertEqual(s["/output/2/mute"], 1.0)
        self.assertEqual(s["/output/3/mute"], 1.0)

    def test_active_snapshot_needs_a_filled_slot(self):
        st = state()
        st["active_slot"] = 2
        self.assertEqual(osc.snapshot(st, DIGIFACE, 1, {2}, True)["/snapshot/load/3"], 2.0)
        self.assertEqual(osc.snapshot(st, DIGIFACE, 1, set(), True)["/snapshot/load/3"], 0.0)

    def test_changes_and_select(self):
        st = state()
        a = osc.snapshot(st, DIGIFACE, 1, set(), True)
        st["mute"]["in"][5] = True
        b = osc.snapshot(st, DIGIFACE, 1, set(), True)
        self.assertEqual(osc.changes(a, b), {"/input/5/mute": 1.0})
        sub = osc.select(b, ("/sendsubmix/33", [1.0]))
        self.assertTrue(sub)
        self.assertTrue(all(k.startswith("/mix/") and "/32/" in k for k in sub))
        chan = osc.select(b, ("/sendchan/input/5", [1.0]))
        self.assertEqual(chan["/input/5/mute"], 1.0)
        self.assertTrue(all(k.startswith(("/input/5/", "/mix/in/5/")) for k in chan))


class ApplyTest(unittest.TestCase):
    def test_send_fader(self):
        st = state()
        r = apply(st, "/mix/in/4/2/faderlin", osc.db_to_faderlin(-10.0))
        self.assertEqual(r.kind, "changed")
        self.assertAlmostEqual(st["sends"]["in"][4][1][0], -10.0, places=1)
        apply(st, "/mix/in/4/3/fader", -20)                       # dB, int, right channel of pair
        self.assertEqual(st["sends"]["in"][4][1][0], -20.0)
        apply(st, "/mix/in/4/2/faderlin", 0.0)
        self.assertIsNone(st["sends"]["in"][4][1][0])

    def test_linked_pair_moves_together(self):
        st = state()
        apply(st, "/mix/pb/1/0/faderlin", osc.db_to_faderlin(-3.0))   # right half of play 1/2
        self.assertEqual(st["sends"]["play"][0][0][0], st["sends"]["play"][1][0][0])
        apply(st, "/playback/0/mute", 1)
        self.assertTrue(st["mute"]["play"][0] and st["mute"]["play"][1])

    def test_mono_channel_alone(self):
        st = state()
        apply(st, "/input/0/mute", 1.0)
        self.assertEqual((st["mute"]["in"][0], st["mute"]["in"][1]), (True, False))

    def test_output(self):
        st = state()
        apply(st, "/output/33/faderlin", osc.db_to_faderlin(-12.0))
        self.assertAlmostEqual(st["out"][16]["gain"], -12.0, places=1)
        apply(st, "/output/2/volume", 3.0)
        self.assertEqual(st["out"][1]["gain"], 3.0)
        apply(st, "/output/2/mute", True)
        self.assertTrue(st["out"][1]["mute"])

    def test_solo_selects_submix(self):
        st = state()
        r = apply(st, "/mix/in/2/4/solo", 1.0)
        self.assertEqual((r.kind, r.selected), ("changed", 2))
        self.assertTrue(st["solo"]["in"][2])
        self.assertEqual(st["selected"], 2)

    def test_control_room(self):
        st = state()
        self.assertEqual(apply(st, "/controlroom/dim", 1.0).kind, "changed")
        self.assertTrue(cr.control_room(st)["dim"])
        self.assertEqual(apply(st, "/controlroom/dim", 1.0).kind, "ignored")    # already on
        self.assertEqual(apply(st, "/controlroom/speakerb", 1.0).kind, "ignored")  # no pair set
        apply(st, "/controlroom/mainmono", 1)
        self.assertTrue(cr.control_room(st)["mono"])

    def test_requests_and_slots(self):
        st = state()
        self.assertEqual(apply(st, "/sendall", 2.0).kind, "send")
        self.assertEqual(apply(st, "/sendsubmix/0", 1.0).kind, "send")
        r = apply(st, "/snapshot/load/3", 1.0)
        self.assertEqual((r.kind, r.value), ("recall", 2))
        r = apply(st, "/snapshot/save/8", 1.0)
        self.assertEqual((r.kind, r.value), ("store", 7))
        self.assertEqual(apply(st, "/snapshot/load/9", 1.0).kind, "ignored")
        self.assertEqual(apply(st, "/snapshot/load/1", 0.0).kind, "ignored")

    def test_global_solo_off_clears_solos(self):
        st = state()
        self.assertEqual(apply(st, "/globalsolo", 0.0).kind, "ignored")    # nothing soloed
        st["solo"]["in"][3] = st["solo"]["play"][0] = True
        self.assertEqual(apply(st, "/globalsolo", 1.0).kind, "ignored")
        self.assertEqual(apply(st, "/globalsolo", 0.0).kind, "changed")
        self.assertFalse(any(st["solo"]["in"]) or any(st["solo"]["play"]))

    def test_names_follow_custom_names(self):
        st = state()
        st["names"]["in:2"] = "Kick"
        st["names"]["out:0"] = "Mains"
        snap = osc.snapshot(st, DIGIFACE, 1, set(), True)
        self.assertEqual((snap["/input/2/name"], snap["/output/1/name"]), ("Kick", "Mains"))
        st["custom_names"] = False
        snap = osc.snapshot(st, DIGIFACE, 1, set(), True)
        self.assertNotEqual(snap["/input/2/name"], "Kick")

    def test_stereo_request(self):
        st = state()
        r = apply(st, "/input/5/stereo", 1.0)
        self.assertEqual((r.kind, r.value), ("stereo", ("in", 4, True)))
        self.assertEqual(apply(st, "/playback/0/stereo", 1.0).kind, "ignored")   # already linked

    def test_out_of_range_and_junk_ignored(self):
        st = state()
        before = osc.snapshot(st, DIGIFACE, 1, set(), True)
        for address, args in (("/input/40/mute", [1.0]), ("/mix/in/0/40/faderlin", [0.5]),
                              ("/input/0/mute", []), ("/input/0/mute", ["x"]),
                              ("/input/0/mute", [float("nan")]), ("/unknown", [1.0])):
            self.assertEqual(osc.apply(st, DIGIFACE, 1, address, args).kind, "ignored")
        self.assertEqual(osc.snapshot(st, DIGIFACE, 1, set(), True), before)


if __name__ == "__main__":
    unittest.main()
