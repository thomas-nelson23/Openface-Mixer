"""RME ARC USB remote: finding it, reading its MIDI stream, and turning messages into actions.

The ARC USB (USB 2a39:0101, "RME ARC") is a class-compliant USB MIDI device, so snd-usb-audio
exposes it as an ALSA card named "ARC" with one rawmidi port (/dev/snd/midiC<card>D0). It is read
here with plain file I/O, no MIDI library needed. Pure Python, no Qt; app.py watches the port
with a QSocketNotifier.

What it sends (captured from a real unit, firmware in its default "note" mode):
* the 15 keys, row by row, left to right, then Talkback, Speaker B, Dim: Note On channel 1,
  notes 0x36..0x44, velocity 0x7F on press and 0x00 on release;
* the encoder: CC 16 channel 1, relative signed-bit (0x01..0x3F up, 0x41..0x7F down);
* nothing when idle (no active sensing).
TotalMix lights a key's LED by sending the same note back (see led_message()).
"""
import os
import re
from pathlib import Path

from .control_room import control_room

CARDS = Path("/proc/asound/cards")
CARD_NAME = re.compile(r"\bARC\b", re.I)

# Actions, as TotalMix offers them for ARC keys. "snapshot:N" (1..8) recalls preset slot N.
ACTIONS = ("dim", "mono", "speaker_b", "talkback", "ext_in", "phones", "main") + tuple(
    f"snapshot:{n}" for n in range(1, 9))

# TotalMix's default ARC USB layout (printed on the unit): rows 1-2 snapshots 1-8; row 3 Mono,
# Volume Phones 1, Volume Phones 2, External Input; bottom Talkback, Speaker B, Dim.
# The Digiface has one phones output, so both phones keys select it.
DEFAULT_KEYS = [f"snapshot:{n}" for n in range(1, 9)] + [
    "mono", "phones", "phones", "ext_in", "talkback", "speaker_b", "dim"]
FIRST_NOTE = 0x36
N_KEYS = 15
ENCODER_CC = 16
TALKBACK_HOLD_S = 0.4     # held longer than this, Talkback is momentary; a tap latches it


def find_port(cards_text=None):
    """Path of the ARC's rawmidi device, or None."""
    if cards_text is None:
        try:
            cards_text = CARDS.read_text()
        except OSError:
            return None
    for line in cards_text.splitlines():
        m = re.match(r"\s*(\d+)\s+\[(.*?)\s*\]:\s*(.*)", line)
        if m and (CARD_NAME.search(m.group(2)) or CARD_NAME.search(m.group(3))):
            return f"/dev/snd/midiC{m.group(1)}D0"
    return None


def decode(msg):
    """("key", index, pressed), ("encoder", clicks) or None for anything else."""
    if len(msg) == 3 and msg[0] in (0x80, 0x90) and FIRST_NOTE <= msg[1] < FIRST_NOTE + N_KEYS:
        return ("key", msg[1] - FIRST_NOTE, msg[0] == 0x90 and msg[2] > 0)
    if len(msg) == 3 and msg[0] == 0xB0 and msg[1] == ENCODER_CC and msg[2] & 0x3F:
        n = msg[2] & 0x3F
        return ("encoder", -n if msg[2] & 0x40 else n)
    return None


def led_message(key, on):
    return bytes((0x90, FIRST_NOTE + key, 0x7F if on else 0x00))


def key_lit(st, action, active_slot):
    """Whether the key bound to action should be lit, given the mixer state."""
    cr = control_room(st)
    if action.startswith("snapshot:"):
        return active_slot == int(action.split(":")[1]) - 1
    if action == "phones":
        return cr["encoder"] == "phones"
    if action == "main":
        return cr["encoder"] == "main"
    return bool(cr.get(action))


class MidiParser:
    """Splits a raw MIDI byte stream into complete messages (tuples of ints).

    Handles running status, SysEx (F0..F7) and real-time bytes interleaved anywhere.
    """
    DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}
    SYS_LEN = {0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF6: 0}

    def __init__(self):
        self.status = None
        self.buf = []
        self.sysex = None

    def feed(self, data):
        out = []
        for b in data:
            if b >= 0xF8:                       # real-time: never interrupts anything
                if b != 0xFE:                   # drop active sensing
                    out.append((b,))
                continue
            if b == 0xF0:
                self.sysex, self.status = [b], None
                continue
            if b == 0xF7:
                if self.sysex is not None:
                    out.append(tuple(self.sysex + [b]))
                self.sysex = None
                continue
            if self.sysex is not None:
                if b < 0x80:
                    self.sysex.append(b)
                    continue
                self.sysex = None               # unterminated SysEx: drop it
            if b >= 0x80:
                self.status, self.buf = b, []
                if b in self.SYS_LEN and self.SYS_LEN[b] == 0:
                    out.append((b,))
                    self.status = None
                continue
            if self.status is None:
                continue
            self.buf.append(b)
            need = self.SYS_LEN.get(self.status) or self.DATA_LEN.get(self.status & 0xF0, 0)
            if len(self.buf) == need:
                out.append((self.status, *self.buf))
                self.buf = []
                if self.status >= 0xF0:
                    self.status = None
        return out


class ArcPort:
    """The open rawmidi device. fileno() is for a socket notifier; read() returns messages."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.parser = MidiParser()

    def fileno(self):
        return self.fd

    def read(self):
        msgs = []
        while True:
            try:
                data = os.read(self.fd, 256)
            except BlockingIOError:
                break
            if not data:
                raise OSError("ARC USB disconnected")
            msgs += self.parser.feed(data)
        return msgs

    def write(self, data):
        try:
            os.write(self.fd, bytes(data))
        except BlockingIOError:
            pass

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
