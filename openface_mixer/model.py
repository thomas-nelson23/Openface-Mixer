"""Mixer state and the math around it. Pure Python, no Qt, so it is easy to unit-test.

Concepts
--------
* Sources are mono channels: up to 32 hardware inputs ("in") and 34 playback channels from the
  computer ("play"). These are the Digiface's counts; other devices (devices.py) use a subset.
* Outputs are handled in stereo pairs (17 pairs at 1x speed; on the Digiface the last pair is
  the headphones).
* A *send* is the level (dB, or None for -inf) and pan (-1..1) of one source channel into one
  output pair. The GUI's faders show the sends into the currently selected output pair, which
  is TotalMix's "submix" workflow.
* Adjacent source channels can be stereo-linked; a linked pair shares one strip.
* Solo works like TotalMix: solo-in-place, post fader, and only in the current submix. While
  any source is soloed, the other sources are silent in the selected output pair; every other
  submix is untouched. Solo is momentary monitoring, so it is neither saved nor part of a mix.
* compute_matrix() flattens the sends into the linear gain matrix that the engine loads into the
  Digiface's DSP mixer, and compute_out_gains() gives the output master levels. They are kept
  apart because the DSP has its own output faders.
"""
import copy
import math
from array import array

N_IN, N_PLAY, N_OUT = 32, 34, 34
N_SRC = N_IN + N_PLAY
N_PAIRS = N_OUT // 2
N_GROUPS = 4
N_SLOTS = 8

NEG_INF = float("-inf")
FADER_MAX_DB = 6.0
FADER_MIN_DB = -80.0     # below this a fader snaps to -inf

STATE_VERSION = 4
# Keys of the state that make up a "mix" (what presets store). UI-only keys are left out.
MIX_KEYS = ("stereo", "mute", "sends", "out", "groups")


# ------------------------------------------------------------------ dB helpers

def db2lin(db):
    return 0.0 if db is None or db == NEG_INF else 10.0 ** (db / 20.0)


def lin2db(v):
    return 20.0 * math.log10(v) if v > 1e-9 else NEG_INF


def fmt_db(db):
    if db is None or db == NEG_INF:
        return "-∞"
    return f"{db:+.1f}" if abs(db) >= 0.05 else "0.0"


# Fader taper (position 0..1 <-> dB): piecewise linear with most travel around 0 dB, like Logic.
TAPER = [(0.0, -90.0), (0.05, -60.0), (0.12, -45.0), (0.25, -30.0), (0.40, -20.0),
         (0.60, -10.0), (0.80, 0.0), (1.0, 6.0)]


def pos2db(p):
    if p <= 0.001:
        return NEG_INF
    for (p0, d0), (p1, d1) in zip(TAPER, TAPER[1:]):
        if p <= p1:
            return d0 + (p - p0) / (p1 - p0) * (d1 - d0)
    return TAPER[-1][1]


def db2pos(db):
    if db is None or db == NEG_INF or db <= TAPER[0][1]:
        return 0.0
    for (p0, d0), (p1, d1) in zip(TAPER, TAPER[1:]):
        if db <= d1:
            return p0 + (db - d0) / (d1 - d0) * (p1 - p0)
    return 1.0


# ------------------------------------------------------------------ channel naming

def speed_mode(rate):
    """1, 2 or 4 (single/double/quad speed). Channel counts divide by this."""
    return 4 if rate and rate > 100000 else 2 if rate and rate > 50000 else 1


def chan_label(i, n_adat_ch, is_output):
    """'AD2 3' = optical port 2, channel 3. Channels past the ADAT block are the phones."""
    per = n_adat_ch // 4
    if is_output and i >= n_adat_ch:
        return "Ph " + ("L" if (i - n_adat_ch) % 2 == 0 else "R")
    return f"AD{i // per + 1} {i % per + 1}"


def pair_label(i, n_adat_ch, is_output):
    if is_output and i >= n_adat_ch:
        return "Phones"
    a, b = chan_label(i, n_adat_ch, is_output), chan_label(i + 1, n_adat_ch, is_output)
    return f"{a}/{b.split()[1]}"


# ------------------------------------------------------------------ state

def default_state(selected=N_PAIRS - 1):
    """A fresh state. selected is the output pair shown first (the device's phones)."""
    st = {
        "version": STATE_VERSION,
        "selected": selected,             # output pair whose submix the faders edit
        "tab": 0,                         # 0 = Mixer, 1 = Matrix
        "active_slot": None,              # last recalled preset slot
        "stereo": {"in": [False] * (N_IN // 2), "play": [True] * (N_PLAY // 2)},
        "mute": {"in": [False] * N_IN, "play": [False] * N_PLAY},
        "solo": {"in": [False] * N_IN, "play": [False] * N_PLAY},   # not saved, see save_state
        # sends[kind][channel][pair] = [gain_db or None (-inf), pan -1..1]
        "sends": {
            "in": [[[None, 0.0] for _ in range(N_PAIRS)] for _ in range(N_IN)],
            "play": [[[None, 0.0] for _ in range(N_PAIRS)] for _ in range(N_PLAY)],
        },
        "out": [{"gain": 0.0, "mute": False} for _ in range(N_PAIRS)],
        "groups": {},                     # strip key ("in:0", "out:16") -> fader group 1..4
    }
    # transparent default: playback n -> output n, like the hardware with the mixer off
    for c in range(N_PLAY):
        st["sends"]["play"][c][c // 2] = [0.0, 0.0]
    return st


def upgrade_state(st, selected=N_PAIRS - 1):
    """Accept older/partial state dicts (e.g. from TotalMix Lite) and fill in missing keys."""
    if not isinstance(st, dict) or "sends" not in st:
        return default_state(selected)
    base = default_state(selected)
    for k, v in base.items():
        st.setdefault(k, v)
    st.pop("mixer_mode", None)          # software (PipeWire) mixing was removed in version 4
    st["solo"] = base["solo"]           # solo never survives a restart, like TotalMix
    st["version"] = STATE_VERSION
    return st


def any_solo(st):
    return any(st["solo"]["in"]) or any(st["solo"]["play"])


def compute_matrix(st, solo=True):
    """Flatten the sends into gains[out * N_SRC + src] (array of float32, out-major).

    Pan, source mutes and solo are folded in; output masters are not (see compute_out_gains).
    With solo=False the solo state is ignored, for the copy saved to disk.
    """
    g = array("f", bytes(N_OUT * N_SRC * 4))
    soloing = solo and any_solo(st)
    for kind, base in (("in", 0), ("play", N_IN)):
        n = N_IN if kind == "in" else N_PLAY
        for c in range(n):
            if st["mute"][kind][c]:
                continue
            stereo = st["stereo"][kind][c // 2]
            left = c % 2 == 0
            # solo-in-place: unsoloed sources drop out of the current submix only
            silenced = st["selected"] if soloing and not st["solo"][kind][c] else None
            for p in range(N_PAIRS):
                if p == silenced:
                    continue
                gdb, pan = st["sends"][kind][c][p]
                lin = db2lin(gdb)
                if lin == 0.0:
                    continue
                gl = lin * min(1.0, 1.0 - pan)   # balance law: centre = full level both sides
                gr = lin * min(1.0, 1.0 + pan)
                s = base + c
                if stereo:                        # linked pair: L -> L, R -> R
                    if left:
                        g[(2 * p) * N_SRC + s] += gl
                    else:
                        g[(2 * p + 1) * N_SRC + s] += gr
                else:                             # mono: panned into both sides
                    g[(2 * p) * N_SRC + s] += gl
                    g[(2 * p + 1) * N_SRC + s] += gr
    return g


def compute_out_gains(st):
    """Output master level per output channel (array of float32), 0 when muted."""
    g = array("f", bytes(N_OUT * 4))
    for p, out in enumerate(st["out"]):
        g[2 * p] = g[2 * p + 1] = 0.0 if out["mute"] else db2lin(out["gain"])
    return g


# ------------------------------------------------------------------ strips and fader groups
# A strip is identified by a key "kind:index": "in:4", "play:0", or "out:16" (output pair).
# For a stereo-linked source pair the index is the left channel.

def strip_key(kind, index):
    return f"{kind}:{index}"


def parse_key(key):
    kind, idx = key.split(":")
    return kind, int(idx)


def strip_channels(st, kind, c):
    """Channels controlled by the source strip starting at channel c."""
    if c % 2 == 0 and st["stereo"][kind][c // 2]:
        return [c, c + 1]
    return [c]


def get_strip_db(st, key):
    """The value the strip's fader shows: send into the selected pair, or the output master."""
    kind, idx = parse_key(key)
    if kind == "out":
        g = st["out"][idx]["gain"]
    else:
        g = st["sends"][kind][idx][st["selected"]][0]
    return NEG_INF if g is None else g


def set_strip_db(st, key, db):
    kind, idx = parse_key(key)
    val = None if db is None or db == NEG_INF else round(db, 2)
    if kind == "out":
        st["out"][idx]["gain"] = val
    else:
        for c in strip_channels(st, kind, idx):
            st["sends"][kind][c][st["selected"]][0] = val


def group_members(st, key):
    g = st["groups"].get(key)
    if not g:
        return []
    return [k for k, v in st["groups"].items() if v == g and k != key]


def group_follow(st, key, old_db, new_db):
    """Apply the leader's fader move to the rest of its group, TotalMix style (relative, in dB).

    -inf is treated as the bottom of the fader so a group can be pulled all the way down and
    brought back up together. Returns {member_key: new_db} for the strips that changed.
    """
    lo = FADER_MIN_DB
    old = lo if old_db == NEG_INF else old_db
    new = lo if new_db == NEG_INF else new_db
    delta = new - old
    changed = {}
    if delta == 0:
        return changed
    for k in group_members(st, key):
        cur = get_strip_db(st, k)
        cur = lo if cur == NEG_INF else cur
        val = min(FADER_MAX_DB, cur + delta)
        val = NEG_INF if val <= lo else val
        if val != get_strip_db(st, k):
            set_strip_db(st, k, val)
            changed[k] = val
    return changed


# ------------------------------------------------------------------ presets

def extract_mix(st):
    return copy.deepcopy({k: st[k] for k in MIX_KEYS})


def apply_mix(st, mix):
    for k in MIX_KEYS:
        if k in mix:
            st[k] = copy.deepcopy(mix[k])
    return st
