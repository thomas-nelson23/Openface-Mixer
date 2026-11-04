"""RME Fireface 400 over FireWire: channel layout, configuration and status. Pure Python, no Qt.

The Fireface 400 is the Fireface 800's smaller sibling and speaks the same "former" protocol:
the engine drives its mixer, output volumes and configuration exactly as the 800's
(engine/fireface_fw.c), at the 400's addresses. Its status quadlets read like the 800's, so this
module reuses fireface800's status decoding and settings plumbing and only adds the 400's own
configuration bits and its input amplifiers. Protocol from snd-firewire-ctl-services
(protocols/fireface/src/former/ff400.rs); see docs/HARDWARE.md.

The 400 also sets its analog gains through an amplifier register: the engine follows the
output volumes there itself, and the GUI sends the four input gains (AN 1/2 mic, AN 3/4
instrument/line) as dev_cmd[3..6].

Channel layout (the same numbering for meters and the mixer):
  inputs   0-7 AN 1-8, 8-9 S/PDIF, 10-17 ADAT
  outputs  0-5 AN 1-6, 6-7 PH 7/8 (phones), 8-9 S/PDIF, 10-17 ADAT
  playback 0-17, named like the outputs
AN 1/2 are the front mic/line inputs (48V), AN 3/4 the front instrument/line inputs.
"""
from . import fireface800 as ff800

N_ANALOG, SPDIF, ADAT = 8, 8, 10
N_LINE_OUT = 6
PHONES_PAIRS = (3,)             # PH 7/8
MIC_INPUTS = (0, 1)             # AN 1/2: 48V, gain 0-65 dB
INST_INPUTS = (2, 3)            # AN 3/4: instrument, pad, gain 0-18 dB
MIC_GAIN_MAX = 65               # dB
LINE_GAIN_MAX = 36              # half dB (18 dB)

CLOCK_SOURCES = (               # (key, label, configuration bits in quadlet 2)
    ("internal", "Internal", 0x00000001),
    ("adat_1", "ADAT", 0x00000000),
    ("spdif", "S/PDIF", 0x00000C00),
    ("word", "Word Clock", 0x00001000),
)
IN_LEVELS = (                   # line inputs AN 5-8: (key, label, quadlet 0 bits, quadlet 1 bits)
    ("low", "Lo Gain", 0x00000010, 0x00000000),
    ("pro", "+4 dBu", 0x00000008, 0x00000002),
    ("con", "-10 dBV", 0x00000020, 0x00000003),
)
OUT_LEVELS = (                  # line outputs AN 1-6
    ("con", "-10 dBV", 0x00001000, 0x00000008),
    ("pro", "+4 dBu", 0x00000800, 0x00000018),
    ("high", "Hi Gain", 0x00000400, 0x00000010),
)
PHONES_LEVELS = (               # PH 7/8 (quadlet 0)
    ("con", "-10 dBV", 0x00020000, 0),
    ("pro", "+4 dBu", 0x00000000, 0),
    ("high", "Hi Gain", 0x00040000, 0),
)
P48_BITS = (0x00000001, 0x00000080)        # quadlet 0, AN 1/2
INST_BITS = (0x00000200, 0x00000004)       # quadlet 0, AN 3/4
PAD_BITS = (0x00000100, 0x00000002)
Q2_MIDI_LOW_0000 = 0x04000000   # MIDI to the address the kernel driver registered

STATUS_INPUTS = (               # (label, lock bit, sync bit) in status quadlet 0
    ("ADAT", 0x00001000, 0x00000400),
    ("S/PDIF", 0x00040000, 0x00100000),
    ("Word Clock", 0x20000000, 0x40000000),
)
ACTIVE_SOURCES = {0x01C00000: "Internal", 0x00000000: "ADAT", 0x00C00000: "S/PDIF",
                  0x01000000: "Word Clock", 0x01400000: "LTC"}
AMP_VALID = 0x80000000          # dev_cmd[3..6]: set on amplifier commands (engine's FORMER_AMP_VALID)


# ------------------------------------------------------------------ channels

def channels(kind, mode):
    """Channel numbers present at speed mode 1/2/4. The kernel driver streams 18/14/10
    channels: ADAT 1-4 at double speed and no ADAT at quad speed."""
    fixed = list(range(ADAT))
    return fixed if mode >= 4 else fixed + list(range(ADAT, ADAT + 8 // mode))


def chan_label(kind, c):
    if c >= ADAT:
        return f"A {c - ADAT + 1}"
    if c >= SPDIF:
        return "SPDIF " + ("L" if c % 2 == 0 else "R")
    if kind != "in" and c >= N_LINE_OUT:
        return f"PH {c + 1}"
    return f"AN {c + 1}"


def pair_label(kind, c):
    a = chan_label(kind, c)
    if a.startswith("SPDIF"):
        return "SPDIF"
    return f"{a}/{chan_label(kind, c + 1).split()[1]}"


# ------------------------------------------------------------------ settings

def default_settings():
    return {
        "known": False,
        "clock": "internal",
        "spdif_in": "coax",
        "opt_out": "adat",
        "spdif_pro": False,
        "emphasis": False,
        "non_audio": False,
        "word_single": False,
        "in_level": "low",
        "out_level": "high",
        "phones_level": "high",
        "p48": [False, False],    # AN 1/2
        "inst": [False, False],   # AN 3/4
        "pad": [False, False],    # AN 3/4
        "mic_gain": [0, 0],       # AN 1/2, dB
        "line_gain": [0, 0],      # AN 3/4, half dB
    }


def upgrade_settings(s):
    return ff800.upgrade_settings(s, default_settings())


settings_from_status = ff800.settings_from_status


def config_words(s):
    """The three configuration quadlets for settings s."""
    q = [0, 0, ff800.Q2_CONTINUE_AT_ERRORS | Q2_MIDI_LOW_0000 | ff800.Q2_RATES]
    q[2] |= ff800._bits(CLOCK_SOURCES, s["clock"])[2]
    for table, key in ((IN_LEVELS, "in_level"), (OUT_LEVELS, "out_level"),
                       (PHONES_LEVELS, "phones_level")):
        _, _, b0, b1 = ff800._bits(table, s[key])
        q[0] |= b0
        q[1] |= b1
    for key, bits in (("p48", P48_BITS), ("inst", INST_BITS), ("pad", PAD_BITS)):
        for on, bit in zip(s[key], bits):
            if on:
                q[0] |= bit
    for key, bit in (("spdif_in", ff800.Q2_SPDIF_IN_OPTICAL), ("opt_out", ff800.Q2_OPT_OUT_SPDIF),
                     ("spdif_pro", ff800.Q2_SPDIF_OUT_PRO),
                     ("emphasis", ff800.Q2_SPDIF_OUT_EMPHASIS),
                     ("non_audio", ff800.Q2_SPDIF_OUT_NON_AUDIO),
                     ("word_single", ff800.Q2_WORD_OUT_SINGLE)):
        if s[key] not in (False, "coax", "adat"):
            q[2] |= bit
    return q


def amp_commands(s):
    """The input amplifier commands (channel << 16 | level), marked for the engine."""
    gains = [min(max(int(g), 0), MIC_GAIN_MAX) for g in s["mic_gain"]] + \
            [min(max(int(g), 0), LINE_GAIN_MAX) for g in s["line_gain"]]
    return [AMP_VALID | ch << 16 | g for ch, g in enumerate(gains)]


def device_words(s):
    """(dev_config, dev_cmd) for the engine once the settings are known, else (0, [])."""
    return (1, config_words(s) + amp_commands(s)) if s["known"] else (0, [])


def decode_status(q0, q1):
    return ff800.decode_status(q0, q1, STATUS_INPUTS, ACTIVE_SOURCES)


def channel_has_settings(kind, c):
    return kind == "in" and c in MIC_INPUTS + INST_INPUTS


def channel_settings_active(s, kind, c):
    """True if input c has a setting TotalMix would flag (48V, instrument, pad)."""
    if kind != "in":
        return False
    if c in MIC_INPUTS:
        return s["p48"][MIC_INPUTS.index(c)]
    if c in INST_INPUTS:
        i = INST_INPUTS.index(c)
        return s["inst"][i] or s["pad"][i]
    return False
