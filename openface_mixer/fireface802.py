"""RME Fireface 802 over FireWire: channel layout, DSP commands and registers. Pure Python, no Qt.

The 802's TotalMix FX DSP is controlled with asynchronous FireWire transactions. The kernel's
snd-fireface driver only streams audio; mixer, input and output settings are left to userspace.
The engine (engine/fireface_fw.c) does the transactions; this module turns the GUI's settings
into the raw command words it sends. Protocol from Takashi Sakamoto's snd-firewire-ctl-services
(protocols/fireface/src/latter.rs, latter/ff802.rs) and the kernel driver; see docs/HARDWARE.md.

DSP commands are 32-bit words written to register 0xffff'0000'001c (the engine adds the odd
parity bit 31):
* (channel << 24) | (command << 16) | value     settings of a hardware input or output channel.
  Channels 0-29 are the hardware inputs, 30-59 the hardware outputs, 0x3c the FX unit.
* 0x40000000 | ((0x40 * mixer + source) << 16) | gain     a mixer crosspoint (engine only).

Channel layout (the same numbering for meters and the mixer):
  inputs   0-7 AN 1-8 (line), 8-11 AN 9-12 (mic/inst), 12-13 AES, 14-21 ADAT A, 22-29 ADAT B
  outputs  0-7 AN 1-8, 8-11 PH 9-12 (two phones pairs), 12-13 AES, 14-21 ADAT A, 22-29 ADAT B
  playback 0-29, named like the outputs (mixer sources 0x20 + n)
"""

N_LINE_IN, N_MIC_IN, N_AES, N_ADAT = 8, 4, 2, 16
N_LINE_OUT, N_PHONES_OUT = 8, 4
N_PHYS_IN = N_LINE_IN + N_MIC_IN + N_AES + N_ADAT          # 30
N_PHYS_OUT = N_LINE_OUT + N_PHONES_OUT + N_AES + N_ADAT    # 30
N_PLAY = 30
OUT_CH_OFFSET = N_PHYS_IN       # DSP channel of output 0
FX_CH = 0x3C
MIXER_STEP = 0x40
FX_MIXERS = (0x1E, 0x1F)        # mixers 30/31 feed the reverb/echo unit
STREAM_OFFSET = 0x20            # mixer source of playback channel 0
PHONES_PAIRS = (4, 5)           # PH 9/10, PH 11/12
ADAT_A, ADAT_B = 14, 22         # first channel of each optical port

# input commands
IN_TO_FX, IN_STEREO_LINK, IN_PHASE, IN_LINE_GAIN, IN_LINE_LEVEL = 0x01, 0x02, 0x06, 0x07, 0x08
IN_MIC_POWER, IN_MIC_INST = 0x08, 0x09
# output commands
OUT_VOL, OUT_FROM_FX, OUT_PHASE, OUT_LINE_LEVEL = 0x00, 0x03, 0x07, 0x08
# channel strip effects (inputs and outputs) and FX unit: only their on/off switches are used
HPF_ON, EQ_ON, DYN_ON, AUTOLEVEL_ON = 0x20, 0x40, 0x60, 0x80
REVERB_ON, ECHO_ON = 0x00, 0x20
VOL_MIN = -650                  # -65.0 dB, in 0.1 dB

IN_LEVELS = ("Lo Gain", "+4 dBu")                     # AN 1-8
OUT_LEVELS = ("-10 dBV", "+4 dBu", "Hi Gain")          # AN 1-8
LINE_GAIN_MAX_DB = 12.0

# configuration register 0xffff'0000'0014 (write only)
CFG_MIDI_LOW_OFFSET_0000 = 0x00002000   # MIDI goes to offset 0 of the driver's address space
CFG_AES_IN_OPTICAL = 0x00000200
CFG_OPT_OUT_SPDIF = 0x00000100
CFG_AES_OUT_PRO = 0x00000020
CFG_WORD_OUT_SINGLE = 0x00000010
CLOCK_SOURCES = (                       # (key, label, config bits), TotalMix's order
    ("internal", "Internal", 0x00000000),
    ("word", "Word Clock", 0x00000400),
    ("aes", "AES", 0x00000800),
    ("adat_a", "ADAT A", 0x00000C00),
    ("adat_b", "ADAT B", 0x00001000),
)

# sync status register 0x0000'801c'0000, as the kernel decodes it (ff-protocol-latter.c)
RATES = {0x0: 32000, 0x1: 44100, 0x2: 48000, 0x4: 64000, 0x5: 88200, 0x6: 96000,
         0x8: 128000, 0x9: 176400, 0xA: 192000}
STATUS_SOURCES = {0xE00: "Internal", 0x200: "Word Clock", 0x400: "AES", 0x600: "ADAT A",
                  0x800: "ADAT B"}
STATUS_INPUTS = (       # (label, lock bit, sync bit, rate shift)
    ("Word Clock", 0x01, 0x10, 12),
    ("AES", 0x02, 0x20, 16),
    ("ADAT A", 0x04, 0x40, 20),
    ("ADAT B", 0x08, 0x80, 24),
)


# ------------------------------------------------------------------ channels

def channels(kind, mode):
    """DSP channel numbers present at speed mode 1/2/4 (kind "in", "play" or "out").

    FireWire 400 has room for ADAT 1-4 per port at double speed and no ADAT at quad speed
    (the kernel driver's stream setup). Channel numbers stay fixed; the missing ones are skipped.
    """
    analog_aes = list(range(ADAT_A))
    if mode >= 4:
        return analog_aes
    per_port = 8 // mode
    return analog_aes + list(range(ADAT_A, ADAT_A + per_port)) + \
        list(range(ADAT_B, ADAT_B + per_port))


def chan_label(kind, c):
    if c >= ADAT_B:
        return f"B {c - ADAT_B + 1}"
    if c >= ADAT_A:
        return f"A {c - ADAT_A + 1}"
    if c >= N_LINE_IN + N_MIC_IN:
        return "AES " + ("L" if c % 2 == 0 else "R")
    if kind != "in" and c >= N_LINE_OUT:
        return f"PH {c + 1}"
    return f"AN {c + 1}"


def pair_label(kind, c):
    a = chan_label(kind, c)
    if a.startswith("AES"):
        return "AES"
    return f"{a}/{chan_label(kind, c + 1).split()[1]}"


# ------------------------------------------------------------------ settings

def default_settings():
    """Hardware settings as TotalMix shows them (settings dialog and channel settings)."""
    return {
        "clock": "internal",
        "aes_in": "xlr",          # or "optical" (AES/S/PDIF in from the second optical port)
        "opt_out": "adat",        # or "spdif" (S/PDIF out on the optical port)
        "aes_pro": False,         # AES/S/PDIF output channel status: professional
        "word_single": False,     # word clock out always at single speed
        "in": {
            "phase": [False] * N_PHYS_IN,
            "gain": [0.0] * N_LINE_IN,        # AN 1-8, 0..+12 dB
            "level": [1] * N_LINE_IN,         # index into IN_LEVELS
            "p48": [False] * N_MIC_IN,        # AN 9-12
            "inst": [False] * N_MIC_IN,       # AN 9-12
        },
        "out": {
            "phase": [False] * N_PHYS_OUT,
            "level": [1] * N_LINE_OUT,        # index into OUT_LEVELS
        },
    }


def upgrade_settings(s):
    base = default_settings()
    if not isinstance(s, dict):
        return base
    for k, v in base.items():
        if k in ("in", "out"):
            part = s.get(k) if isinstance(s.get(k), dict) else {}
            for kk, vv in v.items():
                cur = part.get(kk)
                part[kk] = cur if isinstance(cur, list) and len(cur) == len(vv) else vv
            s[k] = part
        else:
            s.setdefault(k, v)
    return s


def phys_cmd(ch, cmd, value):
    return (ch << 24) | (cmd << 16) | (int(value) & 0xFFFF)


def virt_cmd(mixer, source, gain):
    return 0x40000000 | ((MIXER_STEP * mixer + source) << 16) | (gain & 0xFFFF)


def with_parity(word):
    """The word as written to the device: bit 31 makes the number of set bits odd."""
    word &= 0x7FFFFFFF
    return word if bin(word).count("1") % 2 else word | 0x80000000


def commands(s):
    """All DSP setting commands for settings s, in the order they are sent.

    The channel strip effects (low cut, EQ, dynamics, auto level) and the FX unit (reverb,
    echo, FX sends and returns) are switched off: Openface Mixer has no controls for them yet,
    and the device keeps whatever TotalMix last stored, which the mix could not show.
    """
    cmds = []
    i, o = s["in"], s["out"]
    for c in range(N_PHYS_IN):
        cmds.append(phys_cmd(c, IN_PHASE, i["phase"][c]))
    for c in range(N_LINE_IN):
        gain = round(max(0.0, min(LINE_GAIN_MAX_DB, i["gain"][c])) * 10)
        cmds.append(phys_cmd(c, IN_LINE_GAIN, gain))
        cmds.append(phys_cmd(c, IN_LINE_LEVEL, i["level"][c]))
    for m in range(N_MIC_IN):
        ch = N_LINE_IN + m
        cmds.append(phys_cmd(ch, IN_MIC_INST, i["inst"][m]))
        # Inst switches phantom power off, as on the unit
        cmds.append(phys_cmd(ch, IN_MIC_POWER, i["p48"][m] and not i["inst"][m]))
    for c in range(N_PHYS_OUT):
        cmds.append(phys_cmd(OUT_CH_OFFSET + c, OUT_PHASE, o["phase"][c]))
    for c in range(N_LINE_OUT):
        cmds.append(phys_cmd(OUT_CH_OFFSET + c, OUT_LINE_LEVEL, o["level"][c]))

    # effects off
    for ch in list(range(N_PHYS_IN)) + [OUT_CH_OFFSET + c for c in range(N_PHYS_OUT)]:
        for cmd in (HPF_ON, EQ_ON, DYN_ON, AUTOLEVEL_ON):
            cmds.append(phys_cmd(ch, cmd, 0))
    for c in range(N_PHYS_IN):
        cmds.append(phys_cmd(c, IN_TO_FX, VOL_MIN))
    for p in range(N_PLAY):
        for mixer in FX_MIXERS:
            cmds.append(virt_cmd(mixer, STREAM_OFFSET + p, 0))
    for c in range(N_PHYS_OUT):
        cmds.append(phys_cmd(OUT_CH_OFFSET + c, OUT_FROM_FX, VOL_MIN))
    cmds.append(phys_cmd(FX_CH, REVERB_ON, 0))
    cmds.append(phys_cmd(FX_CH, ECHO_ON, 0))
    return cmds


def config_word(s):
    word = CFG_MIDI_LOW_OFFSET_0000
    word |= dict((k, bits) for k, _, bits in CLOCK_SOURCES).get(s["clock"], 0)
    if s["aes_in"] == "optical":
        word |= CFG_AES_IN_OPTICAL
    if s["opt_out"] == "spdif":
        word |= CFG_OPT_OUT_SPDIF
    if s["aes_pro"]:
        word |= CFG_AES_OUT_PRO
    if s["word_single"]:
        word |= CFG_WORD_OUT_SINGLE
    return word


def decode_status(word):
    """Sync status register -> {"rate": Hz or None, "source": label, "inputs": [(label,
    "No Lock" | "Lock" | "Sync", rate or None)]}."""
    inputs = []
    for label, lock, sync, shift in STATUS_INPUTS:
        state = "Sync" if word & lock and word & sync else "Lock" if word & lock else "No Lock"
        rate = RATES.get((word >> shift) & 0xF) if word & lock else None
        inputs.append((label, state, rate))
    return {
        "rate": RATES.get((word >> 28) & 0xF),
        "source": STATUS_SOURCES.get(word & 0xE00, "—"),
        "inputs": inputs,
    }


def channel_settings_active(s, kind, c):
    """True if channel c has a setting TotalMix would flag (phase, 48V, Inst, gain)."""
    if kind == "in":
        i = s["in"]
        return bool(i["phase"][c] or (c < N_LINE_IN and i["gain"][c]) or
                    (N_LINE_IN <= c < N_LINE_IN + N_MIC_IN and
                     (i["p48"][c - N_LINE_IN] or i["inst"][c - N_LINE_IN])))
    return bool(s["out"]["phase"][c])
