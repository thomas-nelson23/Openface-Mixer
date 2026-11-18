"""Mixer state and the math around it. Pure Python, no Qt, so it is easy to unit-test.

Concepts
--------
* Sources are mono channels: up to 36 hardware inputs ("in") and 36 playback channels from the
  computer ("play"), the RayDAT's counts. Each device (devices.py) uses a subset.
* Outputs are handled in stereo pairs (up to 18; on the Digiface pair 16 is the headphones).
* A *send* is the level (dB, or None for -inf) and pan (-1..1) of one source channel into one
  output pair. The GUI's faders show the sends into the currently selected output pair, which
  is TotalMix's "submix" workflow.
* A mono channel is panned with constant power, as in TotalMix: 3 dB down on both sides at the
  centre. Adjacent source channels can be stereo-linked; a linked pair shares one strip, and its
  pan is the balance of the two sides.
* Solo works like TotalMix: solo-in-place, post fader, and only in the current submix. While
  any source is soloed, the other sources are silent in the selected output pair; every other
  submix is untouched. Solo is momentary monitoring, so it is neither saved nor part of a mix.
* Strips can carry a custom name (TotalMix's names; "Custom channel names" in the settings
  switches between them and the standard ones), a colour and an icon, and whole channel groups
  (an ADAT port, AES, ...) or single strips can be hidden from view. All of these are display
  only: hidden channels keep routing as before.
* compute_matrix() flattens the sends into the linear gain matrix that the engine loads into the
  Digiface's DSP mixer, and compute_out_gains() gives the output master levels. They are kept
  apart because the DSP has its own output faders.
"""
import copy
import math
import sys
from array import array

N_IN, N_PLAY, N_OUT = 36, 36, 36
N_SRC = N_IN + N_PLAY
N_PAIRS = N_OUT // 2
N_GROUPS = 4
N_SLOTS = 8

NEG_INF = float("-inf")
FADER_MAX_DB = 6.0
FADER_MIN_DB = -80.0     # below this a fader snaps to -inf

STATE_VERSION = 6        # 6: mono channels pan with constant power (convert_pan_law)
# Keys of the state that make up a "mix" (what presets store). UI-only keys are left out.
MIX_KEYS = ("stereo", "mute", "sends", "out", "groups", "unlinked")


# ------------------------------------------------------------------ dB helpers

def db2lin(db):
    return 0.0 if db is None or db == NEG_INF else 10.0 ** (db / 20.0)


def lin2db(v):
    return 20.0 * math.log10(v) if v > 1e-9 else NEG_INF


def fmt_db(db):
    if db is None or db == NEG_INF:
        return "-∞"
    return f"{db:+.1f}" if abs(db) >= 0.05 else "0.0"


def parse_db(text):
    """A typed level: "-12", "-12.5", "+3", "-12,5" or "-12 dB" give the number, "-inf", "-∞"
    and "off" give NEG_INF. None for anything else."""
    t = text.strip().lower().removesuffix("db").strip().replace(",", ".").replace("−", "-")
    if t in ("-inf", "-∞", "off"):
        return NEG_INF
    try:
        db = float(t)
    except ValueError:
        return None
    return db if math.isfinite(db) else None


def fader_db(db):
    """db brought into a fader's range: up to FADER_MAX_DB, and -∞ below FADER_MIN_DB."""
    return NEG_INF if db < FADER_MIN_DB else min(FADER_MAX_DB, db)


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
        "unlinked": {},                   # what linked pairs go back to, see set_stereo
        "names": {},                      # strip key -> custom name
        "colors": {},                     # strip key -> "#rrggbb" (TotalMix's channel colours)
        "icons": {},                      # strip key -> icon name (theme.CHANNEL_ICONS)
        "custom_names": True,             # show custom names (else the standard ones)
        "hidden": [],                     # channel groups not shown ("AD3", "AES", see devices)
        "hidden_strips": [],              # single strips hidden with right-click > Hide channel
        "folded": [],                     # rows folded to names and meters: "in", "play", "out"
    }
    # transparent default: playback n -> output n, like the hardware with the mixer off
    for c in range(N_PLAY):
        st["sends"]["play"][c][c // 2] = [0.0, 0.0]
    return st


def is_num(v):
    """True for a number a level or setting can be: not a switch, NaN or an infinity, and not
    a whole number too large for a float (JSON can hold all of these)."""
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and abs(v) <= sys.float_info.max)


def is_index(v, n):
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n


def same_kind(v, default):
    """True if v, read from a file, can stand in for default: a switch for a switch, text for
    text, a number for a number."""
    return isinstance(v, type(default)) if isinstance(default, (bool, str)) else is_num(v)


def valid_key(key):
    """True for the strip key of a channel or output pair the model has ("in:4", "out:16")."""
    kind, _, idx = key.partition(":") if isinstance(key, str) else ("", "", "")
    n = {"in": N_IN, "play": N_PLAY, "out": N_PAIRS}.get(kind, 0)
    return idx.isascii() and idx.isdigit() and int(idx) < n


def checked_mix(mix, strict=True):
    """The parts of a mix (MIX_KEYS) that mix holds, copied into today's shape: lists saved with
    fewer channels (before version 5 there were 32 inputs, 34 playback channels and 17 output
    pairs) are padded, and levels and pans are held to what the faders and knobs can give.

    A part that has the wrong shape or type raises ValueError when strict, so a damaged file is
    refused before anything is applied; otherwise it is replaced by its default, to repair a
    saved state."""
    if not isinstance(mix, dict):
        raise ValueError("the mix is damaged")
    base = default_state(0)

    def bad(default):
        if strict:
            raise ValueError("the mix is damaged")
        return default

    def flags(v, full):
        if not isinstance(v, list):
            return bad(full)
        return [x if isinstance(x, bool) else bad(d) for x, d in zip(v, full)] + full[len(v):]

    def is_level(v):                    # a fader level: dB, or None (or -inf) for off
        return v is None or v == NEG_INF or is_num(v)

    def level(v):
        return None if v is None or v == NEG_INF else min(FADER_MAX_DB, v)

    def send(v):
        if not (isinstance(v, list) and len(v) == 2 and is_level(v[0]) and is_num(v[1])):
            return bad([None, 0.0])
        return [level(v[0]), max(-1.0, min(1.0, v[1]))]

    def sends(v, full):
        if not isinstance(v, list):
            return bad(full)
        rows = [[send(s) for s in (ch if isinstance(ch, list) else bad([]))[:N_PAIRS]]
                for ch in v[:len(full)]]
        rows += [[] for _ in range(len(full) - len(rows))]
        return [row + [[None, 0.0] for _ in range(N_PAIRS - len(row))] for row in rows]

    def outputs(v, full):
        if not isinstance(v, list):
            return bad(full)
        outs = []
        for o, default in zip(v, full):
            if not (isinstance(o, dict) and isinstance(o.get("mute"), bool) and "gain" in o
                    and is_level(o["gain"])):
                o = bad(default)
            outs.append({"gain": level(o["gain"]), "mute": o["mute"]})
        return outs + full[len(outs):]

    def groups(v, _full):
        if not isinstance(v, dict):
            return bad({})
        return {k: g for k, g in v.items()
                if (valid_key(k) and is_index(g, N_GROUPS + 1) and g) or bad(False)}

    def unlinked(v, _full):
        if not isinstance(v, dict):
            return bad({})
        own = {}
        for key, o in v.items():     # the left channel of a source pair -> what was kept
            kind, c = parse_key(key) if valid_key(key) else ("out", 0)
            if not (kind != "out" and c % 2 == 0 and isinstance(o, dict)
                    and is_index(o.get("group"), N_GROUPS + 1)):
                bad(None)
                continue
            own[key] = {"sends": sends(o.get("sends"), copy.deepcopy(base["sends"]["in"][:2])),
                        "mute": flags(o.get("mute"), [False, False]), "group": o["group"]}
        return own

    def both_kinds(check):
        def checked(v, full):
            v = v if isinstance(v, dict) else bad({})
            return {k: check(v[k], full[k]) if k in v else bad(full[k]) for k in ("in", "play")}
        return checked

    checks = {"stereo": both_kinds(flags), "mute": both_kinds(flags), "sends": both_kinds(sends),
              "out": outputs, "groups": groups, "unlinked": unlinked}
    checked = {k: checks[k](mix[k], base[k]) for k in MIX_KEYS if k in mix}
    if "stereo" in checked:             # links from a file without them: nothing was kept
        checked.setdefault("unlinked", {})
    return checked


def upgrade_state(st, selected=N_PAIRS - 1, strict=False):
    """Accept older/partial state dicts (e.g. from TotalMix Lite), fill in missing keys and put
    anything of the wrong type or out of range back to its default, so a damaged or hand-edited
    state file can't stop the window from opening. With strict, a state that isn't one or whose
    mix is damaged raises ValueError instead (config.load_state then tries the backup first)."""
    if not isinstance(st, dict) or "sends" not in st:
        if strict:
            raise ValueError("not a mixer state")
        return default_state(selected)
    base = default_state(selected)
    version = st.get("version", 0)
    for k, v in base.items():
        st.setdefault(k, v)
    st.update(checked_mix(st, strict))
    if is_index(version, 6):            # a version that is no number is taken as today's
        convert_pan_law(st)
    for k in ("names", "colors", "icons"):
        st[k] = {key: v for key, v in st[k].items() if valid_key(key) and isinstance(v, str)
                 } if isinstance(st[k], dict) else {}
    for k, ok in (("hidden", lambda v: isinstance(v, str)), ("hidden_strips", valid_key),
                  ("folded", lambda v: v in ("in", "play", "out"))):
        st[k] = [v for v in st[k] if ok(v)] if isinstance(st[k], list) else []
    if not is_index(st["selected"], N_PAIRS):
        st["selected"] = selected
    st["tab"] = 1 if st["tab"] == 1 else 0
    if not is_index(st["active_slot"], N_SLOTS):
        st["active_slot"] = None
    st["custom_names"] = st["custom_names"] is not False
    st.pop("mixer_mode", None)          # software (PipeWire) mixing was removed in version 4
    st["solo"] = base["solo"]           # solo never survives a restart, like TotalMix
    st["version"] = STATE_VERSION
    return st


def any_solo(st):
    return any(st["solo"]["in"]) or any(st["solo"]["play"])


def pan_gains(pan):
    """(left, right) gain of a mono channel at pan (-1..1). Constant power, as in TotalMix:
    full level at either end and 3 dB down on both sides at the centre."""
    t = (pan + 1.0) * math.pi / 4
    return (math.cos(t) if pan < 1.0 else 0.0), math.sin(t)


def convert_pan_law(mix):
    """Rewrite, in place, a checked mix from before mono channels were panned with constant
    power (state version 5, mix files version 1), so that it sounds as it did. Then a send
    gave min(1, 1 - pan) to the left and min(1, 1 + pan) to the right, full level on both
    sides at the centre; its new level is the power sum of the two and its new pan their
    angle. Linked pairs still use that balance law and stay as they are. A level can't go
    above the fader's top: a send that would is held there and comes out lower by the rest
    (only one that was above +3 dB at the centre)."""
    stereo = mix.get("stereo") or default_state(0)["stereo"]
    for kind, rows in mix.get("sends", {}).items():
        for c, row in enumerate(rows):
            if stereo[kind][c // 2]:
                continue
            for send in row:
                gl, gr = min(1.0, 1.0 - send[1]), min(1.0, 1.0 + send[1])
                send[1] = max(-1.0, min(1.0, math.atan2(gr, gl) * 4 / math.pi - 1.0))
                if send[0] is not None:
                    send[0] = min(FADER_MAX_DB, send[0] + 10 * math.log10(gl * gl + gr * gr))
    return mix


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
                s = base + c
                if stereo:      # linked pair: L -> L, R -> R, pan is the balance of the two
                    if left:
                        g[(2 * p) * N_SRC + s] += lin * min(1.0, 1.0 - pan)
                    else:
                        g[(2 * p + 1) * N_SRC + s] += lin * min(1.0, 1.0 + pan)
                else:           # mono: panned into both sides
                    gl, gr = pan_gains(pan)
                    g[(2 * p) * N_SRC + s] += lin * gl
                    g[(2 * p + 1) * N_SRC + s] += lin * gr
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


def set_stereo(st, kind, c, on):
    """Link channels c (the left one) and c + 1 to a stereo pair, or unlink them.

    Linked, the pair has one strip: the right channel takes the left one's level and mute, in
    every submix, and pan is the balance of the two, at the centre. What the two channels had
    on their own is kept in st["unlinked"] (their sends, their mutes and the right channel's
    fader group; its name, colour and icon stay where they are, as its effect settings do, see
    fx.effect_channels) and comes back when the pair is unlinked, so that link and unlink with
    nothing changed in between gives the mix as it was.

    A submix whose level or balance was changed while linked is unlinked as TotalMix does it:
    both channels at the pair's level, panned hard left and right, which sounds the same. So
    is a pair with nothing kept (linked from the start, or by an older version). If the mute
    was changed, both channels keep the pair's."""
    if st["stereo"][kind][c // 2] == on:
        return
    st["stereo"][kind][c // 2] = on
    key, right_key = strip_key(kind, c), strip_key(kind, c + 1)
    left, right = st["sends"][kind][c], st["sends"][kind][c + 1]
    mute, solo = st["mute"][kind], st["solo"][kind]
    own = st["unlinked"].pop(key, None)
    if on:
        st["unlinked"][key] = {"sends": copy.deepcopy([left, right]),
                               "mute": [mute[c], mute[c + 1]],
                               "group": st["groups"].pop(right_key, 0)}
        for lt, rt in zip(left, right):
            lt[1] = 0.0
            rt[:] = lt
    else:
        for p, (lt, rt) in enumerate(zip(left, right)):
            if own and lt == rt == [own["sends"][0][p][0], 0.0]:
                lt[:], rt[:] = own["sends"][0][p], own["sends"][1][p]
            else:
                rt[0] = lt[0]
                lt[1], rt[1] = -1.0, 1.0
        if own and own["group"]:
            st["groups"][right_key] = own["group"]
    kept = own and not on and mute[c] == own["mute"][0]
    mute[c + 1] = own["mute"][1] if kept else mute[c]
    solo[c + 1] = solo[c]


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


def display_name(st, key, standard):
    """The name a strip shows: its custom name while custom names are on, else the standard
    one."""
    return (st["custom_names"] and st["names"].get(key)) or standard


def strip_color(st, key):
    """The strip's own colour ("#rrggbb"), or None for the default of its row."""
    return st["colors"].get(key)


def set_strip_color(st, key, color):
    """color: "#rrggbb", or None to go back to the row's default."""
    if color:
        st["colors"][key] = color
    else:
        st["colors"].pop(key, None)


def strip_icon(st, key):
    """The strip's icon name, or None."""
    return st["icons"].get(key)


def set_strip_icon(st, key, icon):
    if icon:
        st["icons"][key] = icon
    else:
        st["icons"].pop(key, None)


def strip_hidden(st, key):
    return key in st["hidden_strips"]


def set_strip_hidden(st, key, hidden):
    keys = [k for k in st["hidden_strips"] if k != key]
    st["hidden_strips"] = keys + [key] if hidden else keys


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
    """Replace the parts of st that mix holds. The mix is checked first (checked_mix), so a
    damaged one raises ValueError and st stays as it was."""
    st.update(checked_mix(mix))
    return st


def clear_sends_outside(st, channels):
    """Switch off every send from a source or into an output pair the interface doesn't have,
    so a mix made on a bigger interface leaves no routing that no strip shows, and take such
    strips out of their fader groups, so that no group can bring a send back. The same for
    what is kept for unlinking a pair (set_stereo). channels(kind) gives the interface's
    channels of a kind at any sample rate (Device.all_channels): what a higher rate hides for
    a while is kept."""
    outs = channels("out")

    def clear(rows, have):
        for c, row in rows:
            for p, send in enumerate(row):
                if c not in have or 2 * p not in outs:
                    send[:] = [None, 0.0]
    for kind in ("in", "play"):
        clear(enumerate(st["sends"][kind]), channels(kind))
    for key, own in list(st["unlinked"].items()):
        kind, c = parse_key(key)
        if {c, c + 1} <= channels(kind):
            clear(enumerate(own["sends"], c), channels(kind))
        else:
            del st["unlinked"][key]

    def missing(key):
        kind, idx = parse_key(key)
        return (2 * idx if kind == "out" else idx) not in channels(kind)
    for key in [k for k in st["groups"] if missing(k)]:
        del st["groups"][key]
