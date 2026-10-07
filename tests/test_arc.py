import unittest

from openface_mixer import arc
from openface_mixer import control_room as cr
from openface_mixer import model as m

CARDS = """\
 0 [NVidia         ]: HDA-Intel - HDA NVidia
 3 [USB24255255    ]: USB-Audio - Digiface USB (24255255)
 5 [ARC            ]: USB-Audio - ARC USB
"""


class FindPortTest(unittest.TestCase):
    def test_finds_arc(self):
        self.assertEqual(arc.find_port(CARDS), "/dev/snd/midiC5D0")

    def test_absent(self):
        self.assertIsNone(arc.find_port(CARDS.splitlines()[0]))

    def test_does_not_match_inside_words(self):
        self.assertIsNone(arc.find_port(" 2 [Search]: USB-Audio - Research box"))


class MidiParserTest(unittest.TestCase):
    def test_notes_and_running_status(self):
        p = arc.MidiParser()
        self.assertEqual(p.feed([0x90, 60, 127, 61, 0]), [(0x90, 60, 127), (0x90, 61, 0)])

    def test_split_across_reads(self):
        p = arc.MidiParser()
        self.assertEqual(p.feed([0xB0, 16]), [])
        self.assertEqual(p.feed([65]), [(0xB0, 16, 65)])

    def test_sysex(self):
        p = arc.MidiParser()
        msg = [0xF0, 0x00, 0x20, 0x0D, 0x70, 0x50, 0x00, 0x10, 0x00, 0xF7]
        self.assertEqual(p.feed(msg[:4]), [])
        self.assertEqual(p.feed(msg[4:]), [tuple(msg)])

    def test_realtime_inside_message(self):
        p = arc.MidiParser()
        self.assertEqual(p.feed([0x90, 0xF8, 60, 0xFE, 1]), [(0xF8,), (0x90, 60, 1)])

    def test_program_change(self):
        self.assertEqual(arc.MidiParser().feed([0xC2, 5, 6]), [(0xC2, 5), (0xC2, 6)])

    def test_data_without_status_is_ignored(self):
        self.assertEqual(arc.MidiParser().feed([1, 2, 3]), [])


class DecodeTest(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(arc.decode((0x90, 0x36, 0x7F)), ("key", 0, True))
        self.assertEqual(arc.decode((0x90, 0x44, 0x00)), ("key", 14, False))
        self.assertEqual(arc.decode((0x80, 0x40, 0x40)), ("key", 10, False))
        self.assertIsNone(arc.decode((0x90, 0x35, 0x7F)))
        self.assertIsNone(arc.decode((0x90, 0x45, 0x7F)))

    def test_encoder(self):
        self.assertEqual(arc.decode((0xB0, 16, 0x01)), ("encoder", 1))
        self.assertEqual(arc.decode((0xB0, 16, 0x41)), ("encoder", -1))
        self.assertEqual(arc.decode((0xB0, 16, 0x43)), ("encoder", -3))
        self.assertIsNone(arc.decode((0xB0, 16, 0x40)))
        self.assertIsNone(arc.decode((0xB0, 17, 0x01)))

    def test_default_layout(self):
        self.assertEqual(len(arc.DEFAULT_KEYS), arc.N_KEYS)
        self.assertEqual(arc.DEFAULT_KEYS[-3:], ["talkback", "speaker_b", "dim"])
        self.assertTrue(all(a in arc.ACTIONS for a in arc.DEFAULT_KEYS))

    def test_leds(self):
        st = m.default_state()
        self.assertEqual(arc.led_message(14, True), bytes((0x90, 0x44, 0x7F)))
        self.assertFalse(arc.key_lit(st, "dim", None))
        cr.toggle(st, "dim")
        self.assertTrue(arc.key_lit(st, "dim", None))
        self.assertTrue(arc.key_lit(st, "snapshot:3", 2))
        self.assertFalse(arc.key_lit(st, "snapshot:3", None))
        self.assertFalse(arc.key_lit(st, "phones", None))


if __name__ == "__main__":
    unittest.main()
