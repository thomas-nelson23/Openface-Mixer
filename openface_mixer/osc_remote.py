"""Network remote control in the dialect of TotalMix FX 2.1's "Global OSC". No Qt.

Controllers that speak TotalMix's Global OSC (the Q-SYS TotalMix plugin, Bitfocus Companion's
TotalMix module, TotalMix Stream Deck plugins) can drive Openface Mixer over UDP. Every object has
an absolute address with zero-based channel numbers, as in Global OSC:

    /input/<ch>/name|stereo|color|mute          hardware input channel   (also /playback/<ch>/…)
    /mix/in/<ch>/<out>/faderlin|fader|solo      send of input ch into the submix of output out
    /mix/pb/<ch>/<out>/…                        the same for a playback channel
    /output/<ch>/name|stereo|color|mute|faderlin|volume
    /controlroom/dim|mainmono|speakerb|talkback|externalin, /globalsolo
    /snapshot/load/<n>, /snapshot/save/<n>      preset slots 1-8
    /level/in|pb|out/<ch>                       peak level in dBFS (sent, not received)
    /status/device, /status/connection          device name, hardware mixer running (sent)
    /sendall, /sendstate, /sendsubmix/<out>, /sendchan/<bus>/<ch>   requests for state

Outputs are stereo pairs here, so <out> and output channels address the pair they belong to.
`faderlin` uses TotalMix's fader curve (0..1, 0 dB = 0.8172); `fader` and `volume` are dB.
Numbers may arrive as int32 or float32; replies are float32 (strings for names).

Pure functions: snapshot() describes the whole mixer as {address: value}, so the remote link can
send a full dump or only what changed; apply() turns one received message into a model change and
says what the window must do about it.
"""
import math
import re
import struct

from . import control_room
from .model import NEG_INF, any_solo, strip_channels

DEFAULT_PORT = 7001        # TotalMix's default "Port incoming"
LEVEL_FLOOR_DB = -90.0

KINDS = {"input": "in", "playback": "play", "output": "out"}
BUS_NAMES = {v: k for k, v in KINDS.items()}
MIX_KINDS = {"in": "in", "pb": "play"}
MIX_SEGS = {v: k for k, v in MIX_KINDS.items()}
LEVEL_SEGS = {"in": "in", "play": "pb", "out": "out"}
CONTROL_ROOM = {
    "/controlroom/dim": "dim",
    "/controlroom/mainmono": "mono",
    "/controlroom/speakerb": "speaker_b",
    "/controlroom/talkback": "talkback",
    "/controlroom/externalin": "ext_in",
}
N_SLOTS = 8


# ------------------------------------------------------------------ OSC 1.0 codec

def _pad(b):
    return b + b"\0" * (-len(b) % 4)


def _osc_string(s):
    return _pad(s.encode() + b"\0")


def encode_message(address, *args):
    """One OSC message. Numbers become float32 (TotalMix's convention), str becomes s."""
    tags, data = ",", b""
    for a in args:
        if isinstance(a, str):
            tags += "s"
            data += _osc_string(a)
        elif isinstance(a, bool):
            tags += "T" if a else "F"
        else:
            tags += "f"
            data += struct.pack(">f", float(a))
    return _osc_string(address) + _osc_string(tags) + data


def encode_bundles(messages, max_size=1400):
    """Pack encoded messages into as few #bundle packets of at most max_size bytes (one UDP
    datagram each, small enough not to be fragmented) as possible."""
    head = b"#bundle\0" + struct.pack(">II", 0, 1)        # time tag 1 = immediately
    packets, cur = [], head
    for m in messages:
        item = struct.pack(">I", len(m)) + m
        if len(cur) + len(item) > max_size and cur != head:
            packets.append(cur)
            cur = head
        cur += item
    if cur != head:
        packets.append(cur)
    return packets


def _read_string(data, i):
    end = data.index(b"\0", i)
    s = data[i:end].decode(errors="replace")
    return s, end + 1 + (-(end + 1 - i) % 4)


def decode(data, depth=0):
    """All messages in a packet as (address, [args]), unwrapping bundles. Bad input raises
    ValueError."""
    try:
        if data.startswith(b"#bundle\0"):
            if depth > 8:
                raise ValueError("bundles nested too deep")
            out, i = [], 16
            while i + 4 <= len(data):
                (size,) = struct.unpack_from(">i", data, i)
                if size <= 0 or i + 4 + size > len(data):
                    raise ValueError("bad bundle element size")
                out += decode(data[i + 4:i + 4 + size], depth + 1)
                i += 4 + size
            return out
        if not data.startswith(b"/"):
            raise ValueError("not an OSC packet")
        address, i = _read_string(data, 0)
        args = []
        if i < len(data):
            tags, i = _read_string(data, i)
            for t in tags[1:]:
                if t == "f":
                    args.append(struct.unpack_from(">f", data, i)[0])
                    i += 4
                elif t == "i":
                    args.append(struct.unpack_from(">i", data, i)[0])
                    i += 4
                elif t == "d":
                    args.append(struct.unpack_from(">d", data, i)[0])
                    i += 8
                elif t == "h":
                    args.append(struct.unpack_from(">q", data, i)[0])
                    i += 8
                elif t in "sS":
                    s, i = _read_string(data, i)
                    args.append(s)
                elif t in "TF":
                    args.append(t == "T")
                elif t in "NI":
                    args.append(None)
                else:
                    raise ValueError(f"unsupported OSC type tag {t!r}")
        return [(address, args)]
    except (struct.error, IndexError, UnicodeError) as e:
        raise ValueError(str(e)) from e


# ------------------------------------------------------------------ TotalMix fader curve
# 1023 steps from -65 dB to +6 dB, as in RME's OSC tables: quadratic below -6 dB, linear above.

def db_to_faderlin(db):
    if db is None or db == NEG_INF or db <= -65.0:
        return 0.0
    if db >= 6.0:
        return 1.0
    if db >= -6.0:
        pos = (db + 26.8235294118) / 0.0320855615
    else:
        pos = 826 - math.sqrt(-34869 - 11033 * db)
    return max(0.0, min(1.0, pos / 1023))


def faderlin_to_db(v):
    """TotalMix fader position to dB; the bottom of the fader is -inf."""
    pos = max(0.0, min(1.0, v)) * 1023
    if pos <= 0:
        return NEG_INF
    if pos >= 649:
        return pos * 0.0320855615 - 26.8235294118
    return pos * pos * (-1 / 11033) + pos * 0.1497326203 - 65


def _clean_db(db):
    """dB from a remote, clamped to the fader range; -inf below -65 dB, like TotalMix."""
    if db is None or db == NEG_INF or db <= -65.0:
        return None
    return round(min(6.0, db), 2)


def level_db(lin):
    return max(LEVEL_FLOOR_DB, 20 * math.log10(lin)) if lin > 1e-9 else LEVEL_FLOOR_DB


# ------------------------------------------------------------------ state -> addresses

def _source_label(dev, kind, c, mode, stereo):
    k = "out" if kind == "play" else kind      # playback n is named like output n (app.label)
    return dev.pair_label(k, c - c % 2, mode) if stereo else dev.chan_label(k, c, mode)


def _linked(st, kind, c, present):
    return st["stereo"][kind][c // 2] and (c ^ 1) in present


def snapshot(st, dev, mode, filled_slots, hw_active):
    """The whole mixer as {address: value} for the channels the device has at this speed.

    filled_slots: set of preset slot indexes that hold a mix; hw_active: hardware mixer running.
    """
    out_chans = dev.channels("out", mode)
    pairs = [c // 2 for c in out_chans if c % 2 == 0]
    s = {"/status/device": dev.name, "/status/connection": 1.0 if hw_active else 0.0}
    for kind in ("in", "play"):
        present = set(dev.channels(kind, mode))
        bus, seg = BUS_NAMES[kind], MIX_SEGS[kind]
        for c in sorted(present):
            stereo = _linked(st, kind, c, present)
            s[f"/{bus}/{c}/name"] = _source_label(dev, kind, c, mode, stereo)
            s[f"/{bus}/{c}/stereo"] = 1.0 if stereo else 0.0
            s[f"/{bus}/{c}/color"] = 1.0
            s[f"/{bus}/{c}/mute"] = 1.0 if st["mute"][kind][c] else 0.0
            solo = 1.0 if st["solo"][kind][c] else 0.0
            for p in pairs:
                s[f"/mix/{seg}/{c}/{2 * p}/faderlin"] = db_to_faderlin(st["sends"][kind][c][p][0])
                s[f"/mix/{seg}/{c}/{2 * p}/solo"] = solo
    for c in out_chans:
        o = st["out"][c // 2]
        s[f"/output/{c}/name"] = dev.pair_label("out", c - c % 2, mode)
        s[f"/output/{c}/stereo"] = 1.0
        s[f"/output/{c}/color"] = 1.0
        s[f"/output/{c}/mute"] = 1.0 if o["mute"] else 0.0
        s[f"/output/{c}/faderlin"] = db_to_faderlin(o["gain"])
    cr = control_room.control_room(st)
    for address, key in CONTROL_ROOM.items():
        s[address] = 1.0 if cr[key] else 0.0
    s["/globalsolo"] = 1.0 if any_solo(st) else 0.0
    active = st.get("active_slot")
    for n in range(N_SLOTS):
        # TotalMix reports the loaded snapshot as 2
        s[f"/snapshot/load/{n + 1}"] = 2.0 if n == active and n in filled_slots else 0.0
    return s


def select(snap, request):
    """The part of a snapshot a /send… request asks for."""
    address, _args = request
    m = re.fullmatch(r"/sendsubmix/(\d+)", address)
    if m:
        rx = re.compile(rf"/mix/\w+/\d+/{int(m.group(1)) - int(m.group(1)) % 2}/")
        return {a: v for a, v in snap.items() if rx.match(a)}
    m = re.fullmatch(r"/sendchan/(\w+)/(\d+)", address)
    if m:
        bus, ch = m.group(1), int(m.group(2))
        seg = {"input": "in", "playback": "pb"}.get(bus)
        prefixes = [f"/{bus}/{ch}/"] + ([f"/mix/{seg}/{ch}/"] if seg else [])
        return {a: v for a, v in snap.items() if a.startswith(tuple(prefixes))}
    if address == "/sendstate":
        return {a: v for a, v in snap.items() if a.startswith("/status/")}
    return dict(snap)                                      # /sendall


# ------------------------------------------------------------------ address -> model changes

class Result:
    """What apply() did. kind is one of:
    "changed" (model edited: push the matrix and refresh the strips), "send" (a state request,
    see select()), "recall"/"store" (preset slot in .value), "solo_master" (desired state in
    .value), "stereo" ((kind, channel, on) in .value), or "ignored"."""

    def __init__(self, kind, value=None, selected=None):
        self.kind = kind
        self.value = value
        self.selected = selected       # output pair the window should show (solo from a remote)

    def __repr__(self):
        return f"Result({self.kind!r}, {self.value!r}, selected={self.selected!r})"


IGNORED = Result("ignored")


def _number(args):
    if not args or args[0] is None:
        return None
    v = args[0]
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


def _source_channels(st, kind, c, present):
    """Channels a write to source channel c affects: both of a linked pair."""
    if _linked(st, kind, c, present):
        return strip_channels(st, kind, c - c % 2)
    return [c]


def apply(st, dev, mode, address, args):
    """Apply one received message to the state. Returns a Result."""
    if address in ("/sendall", "/sendstate") or address.startswith(("/sendsubmix/", "/sendchan/")):
        return Result("send")
    v = _number(args)
    if v is None:
        return IGNORED
    on = v >= 0.5

    m = re.fullmatch(r"/mix/(in|pb)/(\d+)/(\d+)/(faderlin|fader|solo)", address)
    if m:
        kind = MIX_KINDS[m.group(1)]
        c, out_ch, what = int(m.group(2)), int(m.group(3)), m.group(4)
        present = set(dev.channels(kind, mode))
        if c not in present or out_ch not in dev.channels("out", mode):
            return IGNORED
        pair = out_ch // 2
        chans = _source_channels(st, kind, c, present)
        if what == "solo":
            # solo works in the submix being edited, so a remote's solo selects its submix
            for ch in chans:
                st["solo"][kind][ch] = on
            st["selected"] = pair
            return Result("changed", selected=pair)
        db = _clean_db(faderlin_to_db(v) if what == "faderlin" else v)
        for ch in chans:
            st["sends"][kind][ch][pair][0] = db
        return Result("changed")

    m = re.fullmatch(r"/(input|playback|output)/(\d+)/(mute|stereo|faderlin|volume|fader)", address)
    if m:
        kind, c, what = KINDS[m.group(1)], int(m.group(2)), m.group(3)
        present = set(dev.channels(kind, mode))
        if c not in present:
            return IGNORED
        if kind == "out":
            o = st["out"][c // 2]
            if what == "mute":
                o["mute"] = on
            elif what in ("faderlin", "volume", "fader"):
                o["gain"] = _clean_db(faderlin_to_db(v) if what == "faderlin" else v)
            else:
                return IGNORED                 # outputs are always stereo pairs
            return Result("changed")
        if what == "mute":
            for ch in _source_channels(st, kind, c, present):
                st["mute"][kind][ch] = on
            return Result("changed")
        if what == "stereo" and (c ^ 1) in present:
            left = c - c % 2
            if bool(st["stereo"][kind][left // 2]) != on:
                return Result("stereo", (kind, left, on))
        return IGNORED

    if address in CONTROL_ROOM:
        key = CONTROL_ROOM[address]
        cr = control_room.control_room(st)
        if key == "speaker_b" and not control_room.has_speaker_b(st):
            return IGNORED
        if cr[key] == on:
            return IGNORED
        cr[key] = on
        return Result("changed")

    if address == "/globalsolo":
        return Result("solo_master", on)

    m = re.fullmatch(r"/snapshot/(load|save)/(\d+)", address)
    if m and on and 1 <= int(m.group(2)) <= N_SLOTS:
        return Result("recall" if m.group(1) == "load" else "store", int(m.group(2)) - 1)
    return IGNORED


def changes(old, new):
    """Addresses whose value differs between two snapshots."""
    return {a: v for a, v in new.items() if old.get(a) != v}


def messages(values):
    """Encode {address: value} as OSC messages."""
    return [encode_message(a, v) for a, v in values.items()]
