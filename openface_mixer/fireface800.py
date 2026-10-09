"""RME Fireface 800 over FireWire: channel layout, configuration and status. Pure Python, no Qt.

The Fireface 800 is one of RME's "former" FireWire models. Its mixer, output volumes and
configuration are plain memory blocks written with asynchronous FireWire transactions (the
engine, engine/fireface_fw.c, does that); this module turns the GUI's settings into the three
configuration quadlets and decodes the two status quadlets. Protocol from Takashi Sakamoto's
snd-firewire-ctl-services (protocols/fireface/src/former.rs, former/ff800.rs) and the kernel's
snd-fireface driver; see docs/HARDWARE.md.

The configuration register is write only. The status register reports the clock source and the
S/PDIF and optical options, so those start from what the device reports (settings_from_status);
the rest (levels, jacks, phantom power, instrument options) start at defaults, as in
snd-firewire-ctl-services. Nothing is written until the first status read has filled them in.

Channel layout (the same numbering for meters and the mixer):
  inputs   0-9 AN 1-10, 10-11 S/PDIF, 12-19 ADAT 1, 20-27 ADAT 2
  outputs  0-7 AN 1-8, 8-9 PH 9/10 (phones), 10-11 S/PDIF, 12-19 ADAT 1, 20-27 ADAT 2
  playback 0-27, named like the outputs
AN 1 is the front instrument input (or rear line 1), AN 7/8 front mic or rear line, AN 9/10 the
rear mic inputs.
"""

N_ANALOG_IN, N_SPDIF, N_ADAT = 10, 2, 16
N_LINE_OUT, N_PHONES_OUT = 8, 2
N_PHYS_IN = N_ANALOG_IN + N_SPDIF + N_ADAT                 # 28
N_PHYS_OUT = N_LINE_OUT + N_PHONES_OUT + N_SPDIF + N_ADAT  # 28
SPDIF = 10                      # first S/PDIF channel
ADAT_1, ADAT_2 = 12, 20         # first channel of each optical port
PHONES_PAIRS = (4,)             # PH 9/10
INST_IN = 0                     # AN 1
JACK_INPUTS = (0, 6, 7)         # AN 1, 7, 8: front, rear or both jacks
P48_INPUTS = (6, 7, 8, 9)       # AN 7-10

CLOCK_SOURCES = (               # (key, label, configuration bits in quadlet 2), TotalMix's order
    ("internal", "Internal", 0x00000001),
    ("adat_1", "ADAT 1", 0x00000000),
    ("adat_2", "ADAT 2", 0x00000400),
    ("spdif", "S/PDIF", 0x00000C00),
    ("word", "Word Clock", 0x00001400),
)
IN_LEVELS = (                   # (key, label, quadlet 0 bits, quadlet 1 bits)
    ("low", "Lo Gain", 0x00000008, 0x00000000),
    ("pro", "+4 dBu", 0x00000010, 0x00000002),
    ("con", "-10 dBV", 0x00000020, 0x00000003),
)
OUT_LEVELS = (
    ("con", "-10 dBV", 0x00001000, 0x00000008),
    ("pro", "+4 dBu", 0x00000800, 0x00000018),
    ("high", "Hi Gain", 0x00000400, 0x00000010),
)
JACKS = (("front", "Front"), ("rear", "Rear"), ("both", "Front + Rear"))
# (rear bit, front bit) in quadlet 1 for AN 1, 7, 8
JACK_BITS = ((0x00000004, 0x00000800), (0x00000040, 0x00000020), (0x00000100, 0x00000080))
P48_BITS = (0x00000001, 0x00000080, 0x00000002, 0x00000100)     # quadlet 0, AN 7-10

# configuration quadlet 0
Q0_INST_DRIVE = 0x00000200
Q0_INST_SPEAKER_EMU = 0x00000004
# quadlet 1
Q1_INST_DRIVE = 0x00000200
# quadlet 2
Q2_CONTINUE_AT_ERRORS = 0x80000000
Q2_INST_LIMITER = 0x00010000
Q2_WORD_OUT_SINGLE = 0x00002000
Q2_SPDIF_IN_OPTICAL = 0x00000200
Q2_OPT_OUT_SPDIF = 0x00000100
Q2_SPDIF_OUT_NON_AUDIO = 0x00000080
Q2_SPDIF_OUT_EMPHASIS = 0x00000040
Q2_SPDIF_OUT_PRO = 0x00000020
Q2_RATES = 0x0000001E           # allow 44.1/48 kHz base rates at single, double and quad speed

# status quadlet 0 (snd-firewire-ctl-services' reading; the kernel's /proc dump differs)
RATES = {0x02000000: 32000, 0x04000000: 44100, 0x06000000: 48000, 0x08000000: 64000,
         0x0A000000: 88200, 0x0E000000: 96000, 0x0C000000: 128000, 0x10000000: 176400,
         0x12000000: 192000}
SPDIF_RATES = {0x00004000: 32000, 0x00008000: 44100, 0x0000C000: 48000, 0x00010000: 64000,
               0x00014000: 88200, 0x00018000: 96000, 0x0001C000: 128000, 0x00020000: 176400,
               0x00024000: 192000}
ACTIVE_SOURCES = {0x01C00000: "Internal", 0x00000000: "ADAT 1", 0x00400000: "ADAT 2",
                  0x00C00000: "S/PDIF", 0x01000000: "Word Clock", 0x01800000: "TCO"}
STATUS_INPUTS = (               # (label, lock bit, sync bit)
    ("ADAT 1", 0x00001000, 0x00000400),
    ("ADAT 2", 0x00002000, 0x00000800),
    ("S/PDIF", 0x00040000, 0x00100000),
    ("Word Clock", 0x20000000, 0x40000000),
)
# status quadlet 1: what the configuration holds now
S1_CLOCK = {0x00000001: "internal", 0x00000000: "adat_1", 0x00000400: "adat_2",
            0x00000C00: "spdif", 0x00001000: "word"}
S1_CLOCK_MASK = 0x00001C01
S1_WORD_OUT_SINGLE = 0x00002000
S1_SPDIF_IN_OPTICAL = 0x00000200
S1_OPT_OUT_SPDIF = 0x00000100
S1_SPDIF_OUT_EMPHASIS = 0x00000040
S1_SPDIF_OUT_PRO = 0x00000020


# ------------------------------------------------------------------ channels

def channels(kind, mode):
    """Channel numbers present at speed mode 1/2/4 (kind "in", "play" or "out").

    The kernel driver streams 28/20/12 channels: ADAT 1-4 per port at double speed and no ADAT
    at quad speed. Channel numbers stay fixed; the missing ones are skipped.
    """
    analog_spdif = list(range(ADAT_1))
    if mode >= 4:
        return analog_spdif
    per_port = 8 // mode
    return analog_spdif + list(range(ADAT_1, ADAT_1 + per_port)) + \
        list(range(ADAT_2, ADAT_2 + per_port))


def chan_label(kind, c):
    if c >= ADAT_2:
        return f"A2 {c - ADAT_2 + 1}"
    if c >= ADAT_1:
        return f"A1 {c - ADAT_1 + 1}"
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
    """Hardware settings as TotalMix shows them (settings dialog and input options)."""
    return {
        "known": False,           # True once filled in from the device's status
        "clock": "internal",
        "spdif_in": "coax",       # or "optical"
        "opt_out": "adat",        # or "spdif" (S/PDIF out on the optical port)
        "spdif_pro": False,       # S/PDIF output channel status: professional
        "emphasis": False,
        "non_audio": False,
        "word_single": False,     # word clock out always at single speed
        "in_level": "low",        # key into IN_LEVELS
        "out_level": "high",      # key into OUT_LEVELS
        "jacks": ["front"] * len(JACK_INPUTS),     # AN 1, 7, 8
        "p48": [False] * len(P48_INPUTS),          # AN 7-10
        "drive": False,           # AN 1 instrument: +25 dB drive
        "limiter": False,
        "speaker_emu": False,
    }


def upgrade_settings(s):
    base = default_settings()
    if not isinstance(s, dict):
        return base
    for k, v in base.items():
        cur = s.get(k)
        if isinstance(v, list):
            s[k] = cur if isinstance(cur, list) and len(cur) == len(v) else v
        else:
            s.setdefault(k, v)
    return s


def settings_from_status(s, status1):
    """Take the options the device reports (status quadlet 1) into s and mark it known."""
    s["clock"] = S1_CLOCK.get(status1 & S1_CLOCK_MASK, s["clock"])
    s["spdif_in"] = "optical" if status1 & S1_SPDIF_IN_OPTICAL else "coax"
    s["opt_out"] = "spdif" if status1 & S1_OPT_OUT_SPDIF else "adat"
    s["spdif_pro"] = bool(status1 & S1_SPDIF_OUT_PRO)
    s["emphasis"] = bool(status1 & S1_SPDIF_OUT_EMPHASIS)
    s["word_single"] = bool(status1 & S1_WORD_OUT_SINGLE)
    s["known"] = True


def _bits(table, key):
    for row in table:
        if row[0] == key:
            return row
    return table[0]


def config_words(s):
    """The three configuration quadlets for settings s."""
    q = [0, 0, Q2_CONTINUE_AT_ERRORS | Q2_RATES]
    q[2] |= _bits(CLOCK_SOURCES, s["clock"])[2]
    _, _, b0, b1 = _bits(IN_LEVELS, s["in_level"])
    q[0] |= b0
    q[1] |= b1
    _, _, b0, b1 = _bits(OUT_LEVELS, s["out_level"])
    q[0] |= b0
    q[1] |= b1
    for jack, (rear, front) in zip(s["jacks"], JACK_BITS):
        if jack != "front":
            q[1] |= rear
        if jack != "rear":
            q[1] |= front
    for on, bit in zip(s["p48"], P48_BITS):
        if on:
            q[0] |= bit
    if s["drive"]:
        q[0] |= Q0_INST_DRIVE
        q[1] |= Q1_INST_DRIVE
    if s["speaker_emu"]:
        q[0] |= Q0_INST_SPEAKER_EMU
    if s["limiter"]:
        q[2] |= Q2_INST_LIMITER
    if s["spdif_in"] == "optical":
        q[2] |= Q2_SPDIF_IN_OPTICAL
    if s["opt_out"] == "spdif":
        q[2] |= Q2_OPT_OUT_SPDIF
    if s["spdif_pro"]:
        q[2] |= Q2_SPDIF_OUT_PRO
    if s["emphasis"]:
        q[2] |= Q2_SPDIF_OUT_EMPHASIS
    if s["non_audio"]:
        q[2] |= Q2_SPDIF_OUT_NON_AUDIO
    if s["word_single"]:
        q[2] |= Q2_WORD_OUT_SINGLE
    return q


def device_words(s):
    """(dev_config, dev_cmd) for the engine: (1, the configuration quadlets) once the settings
    are known, else (0, []) so nothing is written."""
    return (1, config_words(s)) if s["known"] else (0, [])


def decode_status(q0, q1):
    """Status quadlets -> {"rate": Hz or None, "source": label, "inputs": [(label,
    "No Lock" | "Lock" | "Sync", rate or None)]}."""
    internal = bool(q1 & 0x00000001)
    ext_rate = RATES.get(q0 & 0x1E000000)
    active = ACTIVE_SOURCES.get(q0 & 0x01C00000, "—")
    inputs = []
    for label, lock, sync in STATUS_INPUTS:
        state = "Sync" if q0 & lock and q0 & sync else "Lock" if q0 & lock else "No Lock"
        if not q0 & lock:
            rate = None
        elif label == "S/PDIF":
            rate = SPDIF_RATES.get(q0 & 0x0003C000)
        else:
            rate = ext_rate if active == label else None
        inputs.append((label, state, rate))
    from_q1 = {0x02: 32000, 0x00: 44100, 0x06: 48000, 0x0A: 64000, 0x08: 88200, 0x0E: 96000,
               0x12: 128000, 0x10: 176400, 0x16: 192000}.get(q1 & 0x1E)
    return {
        "rate": from_q1 if internal or not ext_rate else ext_rate,
        "source": "Internal" if internal else active,
        "inputs": inputs,
    }


def channel_has_settings(kind, c):
    return kind == "in" and (c in JACK_INPUTS or c in P48_INPUTS)


def channel_settings_active(s, kind, c):
    """True if input c has a setting TotalMix would flag (48V, rear jack, instrument options)."""
    if kind != "in":
        return False
    if c in P48_INPUTS and s["p48"][P48_INPUTS.index(c)]:
        return True
    if c in JACK_INPUTS and s["jacks"][JACK_INPUTS.index(c)] != "front":
        return True
    return c == INST_IN and bool(s["drive"] or s["limiter"] or s["speaker_emu"])
