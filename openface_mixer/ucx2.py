"""RME Fireface UCX II over USB, in Class Compliant mode: channel layout and DSP registers.
Pure Python, no Qt.

Linux runs the UCX II only in Class Compliant (CC) mode, where its TotalMix FX DSP takes SysEx
register writes on its second USB MIDI port, the same transport as the Fireface 802 over USB
(engine/fireface_usb.c). Registers from oscmix (github.com/michaelforney/oscmix), whose main
device is the UCX II (wiki page "Protocol"); see docs/HARDWARE.md.

Registers: (channel << 6) | setting, channels 0-19 the inputs and 20-39 the outputs; the
mixer (0x4000 + 0x40 * output + source, playback from source 0x20) and output volumes are the
engine's. Clock and options sit at 0x3064-0x307b.

Channel layout (the same numbering for meters and the mixer):
  inputs   0-1 AN 1/2 (mic/line, 48V), 2-3 AN 3/4 (instrument/line), 4-7 AN 5-8, 8-9 S/PDIF,
           10-11 AES, 12-19 ADAT
  outputs  0-5 AN 1-6, 6-7 PH 7/8, 8-9 S/PDIF, 10-11 AES, 12-19 ADAT
  playback 0-19, named like the outputs
"""
from . import fireface802 as ff802

N_IN = N_OUT = 20
N_ANALOG_IN = 8
N_LINE_OUT = 6                  # AN 1-6; 6/7 are the phones
SPDIF, AES, ADAT = 8, 10, 12
PHONES_PAIRS = (3,)             # PH 7/8
MIC_INPUTS = (0, 1)             # 48V, gain 0-75 dB
LEVEL_INPUTS = (2, 3, 4, 5, 6, 7)   # reference level, gain 0-24 dB
HIZ_INPUTS = (2, 3)
MIC_GAIN_MAX_DB, LINE_GAIN_MAX_DB = 75.0, 24.0
OUT_CH = 20                     # register channel of output 0
USB_CARD_NAME = "Fireface UCX II"

IN_LEVELS = ("+13 dBu", "+19 dBu")
OUT_LEVELS = ("+4 dBu", "+13 dBu", "+19 dBu")     # AN 1-6
PHONES_LEVELS = ("Low", "High")                   # PH 7/8

# channel settings (register = channel << 6 | setting)
IN_MUTE, IN_FX_SEND, IN_PHASE, IN_GAIN, IN_48V_LEVEL, IN_HIZ = 0x00, 0x01, 0x07, 0x08, 0x09, 0x0B
OUT_BALANCE, OUT_MUTE, OUT_FX_RETURN, OUT_PHASE, OUT_LEVEL = 0x01, 0x02, 0x03, 0x08, 0x09
LOWCUT, EQ, DYNAMICS, AUTOLEVEL = 0x0C, 0x0F, 0x1B, 0x23
# global
REG_REVERB, REG_ECHO = 0x3000, 0x3014
REG_ROOM_EQ = 0x35D1            # + 0x20 * output
REG_CLOCK, REG_WORD_SINGLE = 0x3064, 0x3067
REG_OPT_OUT_SPDIF, REG_SPDIF_PRO = 0x3078, 0x3079
REG_CC_MIX = 0x307B             # 0 = TotalMix (the DSP mixer), else fixed routings
VOL_MIN = -650

CLOCK_SOURCES = (               # (key, label, register value)
    ("internal", "Internal", 0),
    ("word", "Word Clock", 1),
    ("spdif", "S/PDIF", 2),
    ("aes", "AES", 3),
    ("optical", "Optical", 4),
)
STATUS_INPUTS = ()              # CC mode reports the clock setting and rate, not lock states
OPTION_ROWS = (                 # settings panel: (label, key, ((text, value), ...))
    ("Optical out", "opt_out", (("ADAT", "adat"), ("S/PDIF", "spdif"))),
    ("S/PDIF out", "spdif_pro", (("Consumer", False), ("Professional", True))),
)


def channels(kind, mode):
    """Channels present at speed mode 1/2/4: Class Compliant mode streams every channel at
    single speed, ADAT 1-4 at double speed and none at quad speed."""
    fixed = list(range(ADAT))
    return fixed if mode >= 4 else fixed + list(range(ADAT, ADAT + 8 // mode))


def chan_label(kind, c):
    if c >= ADAT:
        return f"A {c - ADAT + 1}"
    if c >= AES:
        return "AES " + "LR"[c - AES]
    if c >= SPDIF:
        return "SPDIF " + "LR"[c - SPDIF]
    if kind != "in" and c >= N_LINE_OUT:
        return f"PH {c + 1}"
    return f"AN {c + 1}"


def pair_label(kind, c):
    a = chan_label(kind, c)
    if a.startswith(("SPDIF", "AES")):
        return a.split()[0]
    return f"{a}/{chan_label(kind, c + 1).split()[1]}"


# ------------------------------------------------------------------ settings

def default_settings():
    return {
        "clock": "internal",
        "opt_out": "adat",
        "spdif_pro": False,
        "word_single": False,
        "in": {
            "phase": [False] * N_IN,
            "gain": [0.0] * N_ANALOG_IN,      # dB
            "p48": [False] * len(MIC_INPUTS),
            "level": [0] * len(LEVEL_INPUTS),  # index into IN_LEVELS
            "hiz": [False] * len(HIZ_INPUTS),
        },
        "out": {
            "phase": [False] * N_OUT,
            "level": [0] * (N_LINE_OUT + 2),   # OUT_LEVELS index for AN 1-6, PHONES_LEVELS for PH
        },
    }


def upgrade_settings(s):
    return ff802.upgrade_settings(s, default_settings())


def reg(ch, setting, value):
    return ff802.reg_cmd(ch << 6 | setting, value)


def gain_max(c):
    return MIC_GAIN_MAX_DB if c in MIC_INPUTS else LINE_GAIN_MAX_DB


def usb_commands(s):
    """All register writes for settings s. As on the 802, the channel effects (low cut, EQ,
    dynamics, auto level, room EQ) and the FX unit are switched off, TotalMix's own mute and
    balance are cleared, and CC mix is set to TotalMix so the DSP mixer is what is heard."""
    i, o = s["in"], s["out"]
    cmds = []
    for c in range(N_IN):
        cmds.append(reg(c, IN_PHASE, i["phase"][c]))
    for c in range(N_ANALOG_IN):
        cmds.append(reg(c, IN_GAIN, round(max(0.0, min(gain_max(c), i["gain"][c])) * 10)))
    for n, c in enumerate(MIC_INPUTS):
        cmds.append(reg(c, IN_48V_LEVEL, i["p48"][n]))
    for n, c in enumerate(LEVEL_INPUTS):
        cmds.append(reg(c, IN_48V_LEVEL, i["level"][n]))
    for n, c in enumerate(HIZ_INPUTS):
        cmds.append(reg(c, IN_HIZ, i["hiz"][n]))
    for c in range(N_OUT):
        cmds.append(reg(OUT_CH + c, OUT_PHASE, o["phase"][c]))
    for c in range(N_LINE_OUT + 2):
        cmds.append(reg(OUT_CH + c, OUT_LEVEL, o["level"][c]))

    for c in range(N_IN):
        # (input 1's mute is register 0 = 0, which the engine skips like any empty command)
        cmds += [reg(c, IN_MUTE, 0), reg(c, IN_FX_SEND, VOL_MIN)]
        cmds += [reg(c, fx, 0) for fx in (LOWCUT, EQ, DYNAMICS, AUTOLEVEL)]
    for c in range(N_OUT):
        cmds += [reg(OUT_CH + c, OUT_MUTE, 0), reg(OUT_CH + c, OUT_BALANCE, 0),
                 reg(OUT_CH + c, OUT_FX_RETURN, VOL_MIN),
                 ff802.reg_cmd(REG_ROOM_EQ + 0x20 * c, 0)]
        cmds += [reg(OUT_CH + c, fx, 0) for fx in (LOWCUT, EQ, DYNAMICS, AUTOLEVEL)]
    clock = dict((k, v) for k, _, v in CLOCK_SOURCES).get(s["clock"], 0)
    cmds += [
        ff802.reg_cmd(REG_REVERB, 0),
        ff802.reg_cmd(REG_ECHO, 0),
        ff802.reg_cmd(REG_CC_MIX, 0),
        ff802.reg_cmd(REG_CLOCK, clock),
        ff802.reg_cmd(REG_WORD_SINGLE, s["word_single"]),
        ff802.reg_cmd(REG_OPT_OUT_SPDIF, s["opt_out"] == "spdif"),
        ff802.reg_cmd(REG_SPDIF_PRO, s["spdif_pro"]),
    ]
    return cmds


def decode_usb_status(word):
    return ff802.decode_usb_status(word, CLOCK_SOURCES)


def channel_has_settings(kind, c):
    return kind in ("in", "out")      # phase invert on every channel


def channel_settings_active(s, kind, c):
    if kind == "in":
        i = s["in"]
        return bool(i["phase"][c] or (c < N_ANALOG_IN and i["gain"][c]) or
                    (c in MIC_INPUTS and i["p48"][MIC_INPUTS.index(c)]) or
                    (c in HIZ_INPUTS and i["hiz"][HIZ_INPUTS.index(c)]))
    return bool(s["out"]["phase"][c])
