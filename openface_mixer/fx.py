"""TotalMix FX effects of the Fireface 802 and UCX II: model, defaults and DSP commands. No Qt.

Both interfaces have the same effects, in the same parameter order, at different addresses:

* channel strip effects on every hardware input and output: Low Cut, a 3-band parametric EQ,
  Dynamics (compressor and expander) and Auto Level;
* one FX unit with Reverb and Echo, fed by an FX send on every input and playback channel and
  returned to every hardware output by its FX return level;
* the UCX II only: Room EQ (delay, volume calibration and 9 bands) on the outputs.

The 802 takes them as DSP commands, (channel << 24) | (command << 16) | value, over FireWire and
(as the same words) over USB: low cut 0x20-0x22, EQ 0x40-0x4b, dynamics 0x60-0x67, auto level
0x80-0x83, reverb 0x00-0x0d and echo 0x20-0x26 on the FX channel 0x3c (Takashi Sakamoto's
snd-firewire-ctl-services, protocols/fireface/src/latter.rs, and the oscmix wiki's Fireface 802
page). The UCX II takes register writes, (channel << 6) | setting: low cut 0x0c-0x0e, EQ
0x0f-0x1a, dynamics 0x1b-0x22, auto level 0x23-0x26, reverb 0x3000-0x300d, echo 0x3014-0x301a,
room EQ 0x3400 + 0x20 * output (oscmix's device_ffucxii.c and wiki page "Protocol").

Playback channels reach the FX unit through mixers 30 (left) and 31 (right) of the DSP matrix on
both: 0x4000 + 0x40 * mixer + 0x20 + playback channel as a register (UCX II 0x47a0/0x47e0).

The settings live in st["hw"]["fx"] in display units (dB, Hz, ms, seconds); commands() turns
them into raw values. A switched-off effect sends only its switch: its parameter slots are 0
(unused), so the engine leaves them alone until the effect is switched on again.
"""
import math

# ------------------------------------------------------------------ parameters
# (key, scale (raw = value * scale), low, high, default), low/high/default in display units.
LOWCUT = ("lc", (
    ("freq", 1, 20, 500, 100),
    ("slope", 1, 0, 3, 1),               # 6 / 12 / 18 / 24 dB/oct
))
EQ = ("eq", (
    ("t1", 1, 0, 3, 0),
    ("g1", 10, -20.0, 20.0, 0.0),
    ("f1", 1, 20, 20000, 100),
    ("q1", 10, 0.7, 5.0, 1.0),
    ("g2", 10, -20.0, 20.0, 0.0),
    ("f2", 1, 20, 20000, 1000),
    ("q2", 10, 0.7, 5.0, 1.0),
    ("t3", 1, 0, 3, 0),
    ("g3", 10, -20.0, 20.0, 0.0),
    ("f3", 1, 20, 20000, 10000),
    ("q3", 10, 0.7, 5.0, 1.0),
))
DYNAMICS = ("dyn", (
    ("gain", 10, -30.0, 30.0, 0.0),
    ("attack", 1, 0, 200, 10),
    ("release", 1, 100, 999, 100),
    ("cthr", 10, -60.0, 0.0, -20.0),
    ("cratio", 10, 1.0, 10.0, 2.0),
    ("ethr", 10, -99.9, -20.0, -60.0),
    ("eratio", 10, 1.0, 10.0, 1.0),
))
AUTOLEVEL = ("al", (
    ("maxgain", 10, 0.0, 18.0, 6.0),
    ("headroom", 10, 3.0, 12.0, 6.0),
    ("rise", 10, 0.1, 9.9, 3.0),
))
STRIP_SECTIONS = (LOWCUT, EQ, DYNAMICS, AUTOLEVEL)

REVERB = ("reverb", (
    ("type", 1, 0, 14, 1),
    ("predelay", 1, 0, 999, 0),
    ("lowcut", 1, 20, 500, 100),
    ("roomscale", 100, 0.5, 3.0, 1.0),
    ("attack", 1, 5, 400, 5),
    ("hold", 1, 5, 400, 100),
    ("release", 1, 5, 500, 300),
    ("highcut", 1, 2000, 20000, 15000),
    ("time", 10, 0.1, 4.9, 1.5),
    ("damp", 1, 2000, 20000, 15000),
    ("smooth", 1, 0, 100, 50),
    ("volume", 10, -65.0, 6.0, -6.0),
    ("width", 100, 0.0, 1.0, 1.0),
))
ECHO = ("echo", (
    ("type", 1, 0, 2, 0),
    ("delay", 1000, 0.0, 2.0, 0.3),
    ("feedback", 1, 0, 100, 30),
    ("highcut", 1, 0, 5, 0),
    ("volume", 10, -65.0, 6.0, -6.0),
    ("width", 100, 0.0, 1.0, 1.0),
))

ROOM_BANDS = 9
ROOM_TYPE_BANDS = (1, 8, 9)           # bands with a filter type; the others are peak filters
ROOM_EQ_DEFAULT_FREQS = (60, 120, 250, 500, 1000, 2000, 4000, 8000, 16000)
ROOM_DELAY = ("delay", 10, 0.0, 42.5, 0.0)          # ms, 0.1 ms steps
VOLUME_CAL = ("volcal", 100, -24.0, 3.0, 0.0)        # dB, per output (Room EQ panel)

FX_MIN_DB = -65.0                     # FX send / return: -65.0 dB and below is off
FX_OFF_RAW = -650

SLOPES = ("6 dB/oct", "12 dB/oct", "18 dB/oct", "24 dB/oct")
REVERB_TYPES = (
    "Small Room", "Medium Room", "Large Room", "Walls", "Shorty", "Attack", "Swagger",
    "Old School", "Echoistic", "8plus9", "Grand Wide", "Thicker", "Envelope", "Gated", "Space",
)
ROOM_TYPES = range(12)                # reverb types that use Room Scale
ENVELOPE, GATED, SPACE = 12, 13, 14
ECHO_TYPES = ("Stereo Echo", "Stereo Cross", "Pong Echo")
ECHO_HIGHCUTS = ("Off", "16 kHz", "12 kHz", "8 kHz", "4 kHz", "2 kHz")


def reverb_uses(rtype, key):
    """True if reverb parameter key matters for reverb type rtype (TotalMix greys the rest)."""
    if key == "roomscale":
        return rtype in ROOM_TYPES
    if key == "attack":
        return rtype == ENVELOPE
    if key in ("hold", "release"):
        return rtype in (ENVELOPE, GATED)
    if key in ("time", "damp"):
        return rtype == SPACE
    return True


# ------------------------------------------------------------------ the two interfaces
class Model:
    """What differs between the 802 and the UCX II: addresses, channel counts and a few
    ranges."""
    key = ""
    n_in = n_out = n_play = 0
    out_offset = 0                    # DSP channel of output 0
    room_eq = False
    eq_types = (("Peak", "Low Shelf", "High Pass"), ("Peak", "High Shelf", "Low Pass"))
    overrides = {}                    # (section, key) -> (scale, low, high, default)

    def strip_word(self, ch, section, i, raw):
        raise NotImplementedError

    def fx_word(self, section, i, raw):
        raise NotImplementedError

    def in_send_word(self, c, raw):
        raise NotImplementedError

    def out_return_word(self, c, raw):
        raise NotImplementedError

    def param(self, section, p):
        """(key, scale, low, high, default) of parameter p of section, for this interface."""
        name, params = section
        key = params[p][0]
        return (key,) + self.overrides.get((name, key), params[p][1:])

    def params(self, section):
        return [self.param(section, i) for i in range(len(section[1]))]


def _phys(ch, cmd, raw):
    return (ch << 24) | (cmd << 16) | (int(raw) & 0xFFFF)


def _reg(reg, raw):
    return (reg << 16) | (int(raw) & 0xFFFF)


class FF802(Model):
    key = "ff802"
    n_in = n_out = n_play = 30
    out_offset = 30
    FX_CH = 0x3C
    STRIP_BASE = {"lc": 0x20, "eq": 0x40, "dyn": 0x60, "al": 0x80}
    FX_BASE = {"reverb": 0x00, "echo": 0x20}
    overrides = {
        ("eq", "t1"): (1, 0, 2, 0),
        ("eq", "t3"): (1, 0, 2, 0),
        # the oscmix wiki's 802 page: echo delay in 1/100 s, FX volumes up to 0 dB
        ("echo", "delay"): (100, 0.0, 2.0, 0.3),
        ("echo", "volume"): (10, -65.0, 0.0, -6.0),
        ("reverb", "volume"): (10, -65.0, 0.0, -6.0),
    }

    def strip_word(self, ch, section, i, raw):
        return _phys(ch, self.STRIP_BASE[section] + i, raw)

    def fx_word(self, section, i, raw):
        return _phys(self.FX_CH, self.FX_BASE[section] + i, raw)

    def in_send_word(self, c, raw):
        return _phys(c, 0x01, raw)

    def out_return_word(self, c, raw):
        return _phys(self.out_offset + c, 0x03, raw)


class UCX2(Model):
    key = "ucx2"
    n_in = n_out = n_play = 20
    out_offset = 20
    room_eq = True
    STRIP_BASE = {"lc": 0x0C, "eq": 0x0F, "dyn": 0x1B, "al": 0x23}
    FX_BASE = {"reverb": 0x3000, "echo": 0x3014}
    eq_types = (("Peak", "Low Shelf", "High Pass", "Low Pass"),
                ("Peak", "High Shelf", "Low Pass", "High Pass"))
    overrides = {
        ("eq", "q1"): (10, 0.4, 9.9, 1.0),
        ("eq", "q2"): (10, 0.4, 9.9, 1.0),
        ("eq", "q3"): (10, 0.4, 9.9, 1.0),
    }
    ROOM_BASE = 0x3400
    REG_VOLCAL = 0x0B

    def strip_word(self, ch, section, i, raw):
        return _reg(ch << 6 | (self.STRIP_BASE[section] + i), raw)

    def fx_word(self, section, i, raw):
        return _reg(self.FX_BASE[section] + i, raw)

    def in_send_word(self, c, raw):
        return _reg(c << 6 | 0x01, raw)

    def out_return_word(self, c, raw):
        return _reg((self.out_offset + c) << 6 | 0x03, raw)

    def room_word(self, out, i, raw):
        return _reg(self.ROOM_BASE + 0x20 * out + i, raw)

    def volcal_word(self, out, raw):
        return _reg((self.out_offset + out) << 6 | self.REG_VOLCAL, raw)


MODELS = {"ff802": FF802(), "ucx2": UCX2()}


def room_band_keys(b):
    """Parameter keys of Room EQ band b (1-9), in register order."""
    keys = [f"t{b}"] if b in ROOM_TYPE_BANDS else []
    return keys + [f"g{b}", f"f{b}", f"q{b}"]


def room_param(m, key):
    """(scale, low, high, default) of a Room EQ parameter."""
    kind, band = key[0], int(key[1:])
    if kind == "t":
        return (1, 0, len(m.eq_types[0]) - 1, 0)
    if kind == "g":
        return (10, -20.0, 20.0, 0.0)
    if kind == "f":
        return (1, 20, 20000, ROOM_EQ_DEFAULT_FREQS[band - 1])
    return (10, 0.4, 9.9, 1.0)


# ------------------------------------------------------------------ settings
def default_channel(m):
    ch = {}
    for section in STRIP_SECTIONS:
        ch[section[0]] = False
        for key, _, _, _, default in m.params(section):
            ch[f"{section[0]}_{key}"] = default
    return ch


def default_room(m):
    room = {"on": False, "delay": ROOM_DELAY[4], "volcal": VOLUME_CAL[4]}
    for b in range(1, ROOM_BANDS + 1):
        for key in room_band_keys(b):
            room[key] = room_param(m, key)[3]
    return room


def default_unit(m, section):
    unit = {"on": False}
    for key, _, _, _, default in m.params(section):
        unit[key] = default
    return unit


def default_settings(m):
    fx = {
        "in": [default_channel(m) for _ in range(m.n_in)],
        "out": [default_channel(m) for _ in range(m.n_out)],
        "send": {"in": [None] * m.n_in, "play": [None] * m.n_play},
        "ret": [None] * m.n_out,           # FX return level per output, dB or None (off)
        "reverb": default_unit(m, REVERB),
        "echo": default_unit(m, ECHO),
    }
    if m.room_eq:
        fx["room"] = [default_room(m) for _ in range(m.n_out)]
    return fx


def _fill(cur, base):
    """cur with base's keys added where missing or of another type; None for a level is kept."""
    if not isinstance(cur, dict):
        return base
    for k, v in base.items():
        c = cur.get(k)
        if isinstance(v, bool):
            ok = isinstance(c, bool)
        elif isinstance(v, (int, float)):
            ok = isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c)
        else:
            ok = isinstance(c, type(v))
        if not ok:
            cur[k] = v
    return cur


def _levels(cur, n):
    if not isinstance(cur, list) or len(cur) != n:
        return [None] * n
    return [v if isinstance(v, (int, float)) and not isinstance(v, bool) else None for v in cur]


def upgrade_settings(fx, m):
    base = default_settings(m)
    if not isinstance(fx, dict):
        return base
    for kind in ("in", "out"):
        cur = fx.get(kind)
        n = len(base[kind])
        if not isinstance(cur, list) or len(cur) != n:
            cur = base[kind]
        fx[kind] = [_fill(c, b) for c, b in zip(cur, base[kind])]
    send = fx.get("send") if isinstance(fx.get("send"), dict) else {}
    fx["send"] = {k: _levels(send.get(k), len(v)) for k, v in base["send"].items()}
    fx["ret"] = _levels(fx.get("ret"), m.n_out)
    for unit in ("reverb", "echo"):
        fx[unit] = _fill(fx.get(unit), base[unit])
    if m.room_eq:
        cur = fx.get("room")
        if not isinstance(cur, list) or len(cur) != m.n_out:
            cur = base["room"]
        fx["room"] = [_fill(c, b) for c, b in zip(cur, base["room"])]
    return fx


# ------------------------------------------------------------------ commands
def raw_value(value, scale, lo, hi):
    return round(max(lo, min(hi, value)) * scale)


def level_raw(db):
    """FX send / return level in 0.1 dB, -650 (off) for None or -65 dB and below."""
    if db is None or db <= FX_MIN_DB:
        return FX_OFF_RAW
    return round(min(0.0, db) * 10)


def mixer_gain(db, usb):
    """A DSP mixer crosspoint gain for a level in dB (None = off), as the engine encodes them
    (ff802_encode_gain / ff802usb_encode_gain): 0x8000 | gain * 0x1000, or over USB gain * 0x8000
    below -6 dB for finer steps."""
    if db is None or db <= FX_MIN_DB:
        return 0
    lin = 10 ** (min(6.0, db) / 20)
    if usb and lin <= 0.5:
        return max(1, round(lin * 0x8000))
    return 0x8000 | min(0x2000, round(lin * 0x1000))


def _strip_words(m, ch_id, ch):
    words = []
    for section in STRIP_SECTIONS:
        name = section[0]
        on = bool(ch[name])
        words.append(m.strip_word(ch_id, name, 0, on))
        for i, (key, scale, lo, hi, _) in enumerate(m.params(section)):
            words.append(m.strip_word(ch_id, name, i + 1,
                                      raw_value(ch[f"{name}_{key}"], scale, lo, hi)) if on else 0)
    return words


def _unit_words(m, section, unit):
    on = bool(unit["on"])
    words = [m.fx_word(section[0], 0, on)]
    for i, (key, scale, lo, hi, _) in enumerate(m.params(section)):
        words.append(m.fx_word(section[0], i + 1, raw_value(unit[key], scale, lo, hi))
                     if on else 0)
    return words


def _room_words(m, out, room):
    on = bool(room["on"])
    _, scale, lo, hi, _ = ROOM_DELAY
    words = [m.room_word(out, 0, raw_value(room["delay"], scale, lo, hi)),
             m.room_word(out, 1, on)]
    i = 2
    for b in range(1, ROOM_BANDS + 1):
        for key in room_band_keys(b):
            scale, lo, hi, _ = room_param(m, key)
            words.append(m.room_word(out, i, raw_value(room[key], scale, lo, hi)) if on else 0)
            i += 1
    _, scale, lo, hi, _ = VOLUME_CAL
    words.append(m.volcal_word(out, raw_value(room["volcal"], scale, lo, hi)))
    return words


FX_MIXERS = (30, 31)                  # DSP mixers feeding the FX unit, left and right
PLAY_SOURCE = 0x20                    # mixer source of playback channel 0


def play_send_word(mixer, p, gain):
    return 0x40000000 | ((0x40 * mixer + PLAY_SOURCE + p) << 16) | (gain & 0xFFFF)


def commands(fx, m, play_stereo=None, usb=False):
    """All effect commands for fx (st["hw"]["fx"]), in a fixed slot order: 0 is an unused slot.

    play_stereo: the stereo flags of the playback pairs (st["stereo"]["play"]). A linked pair
    sends its left channel to the FX unit's left input and its right channel to the right, as
    TotalMix does; a mono playback channel goes to both."""
    words = []
    for c in range(m.n_in):
        words += _strip_words(m, c, fx["in"][c])
    for c in range(m.n_out):
        words += _strip_words(m, m.out_offset + c, fx["out"][c])
    for c in range(m.n_in):
        words.append(m.in_send_word(c, level_raw(fx["send"]["in"][c])))
    for p in range(m.n_play):
        linked = bool(play_stereo and p // 2 < len(play_stereo) and play_stereo[p // 2])
        g = mixer_gain(fx["send"]["play"][p], usb)
        for side, mixer in enumerate(FX_MIXERS):
            words.append(play_send_word(mixer, p, g if not linked or p % 2 == side else 0))
    for c in range(m.n_out):
        words.append(m.out_return_word(c, level_raw(fx["ret"][c])))
    words += _unit_words(m, REVERB, fx["reverb"])
    words += _unit_words(m, ECHO, fx["echo"])
    if m.room_eq:
        for c in range(m.n_out):
            words += _room_words(m, c, fx["room"][c])
    return words


# ------------------------------------------------------------------ for the strips
def eq_active(fx, kind, c):
    """TotalMix lights a strip's EQ button while its Low Cut or EQ is on."""
    ch = fx[kind][c]
    return bool(ch["lc"] or ch["eq"])


def dyn_active(fx, kind, c):
    """... and its D button while Dynamics or Auto Level is on."""
    ch = fx[kind][c]
    return bool(ch["dyn"] or ch["al"])


def room_active(fx, c):
    return bool(fx.get("room") and fx["room"][c]["on"])


STRIP_KEYS = tuple(f"{s[0]}{suffix}" for s in STRIP_SECTIONS
                   for suffix in [""] + [f"_{p[0]}" for p in s[1]])


def copy_channel(fx, kind, c):
    """The channel's strip effects (Low Cut, EQ, Dynamics, Auto Level), for Paste."""
    return {k: fx[kind][c][k] for k in STRIP_KEYS}


def paste_channel(fx, kind, channels, data, m):
    """Paste copied strip effects into channels, clamped to this interface's ranges (a type the
    other row does not have stays as it was)."""
    for c in channels:
        ch = fx[kind][c]
        for section in STRIP_SECTIONS:
            name = section[0]
            if isinstance(data.get(name), bool):
                ch[name] = data[name]
            for key, scale, lo, hi, _ in m.params(section):
                v = data.get(f"{name}_{key}")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    v = max(lo, min(hi, v))
                    ch[f"{name}_{key}"] = round(v) if isinstance(lo, int) else float(v)


# ------------------------------------------------------------------ curves
def _biquad(kind, f0, gain_db, q, rate=48000.0):
    """RBJ cookbook coefficients (b0, b1, b2, a0, a1, a2)."""
    w0 = 2 * math.pi * min(f0, rate * 0.49) / rate
    cw, sw = math.cos(w0), math.sin(w0)
    a = 10 ** (gain_db / 40)
    alpha = sw / (2 * max(q, 0.05))
    if kind == "peak":
        return (1 + alpha * a, -2 * cw, 1 - alpha * a, 1 + alpha / a, -2 * cw, 1 - alpha / a)
    if kind in ("lowshelf", "highshelf"):
        s = 2 * math.sqrt(a) * alpha
        if kind == "lowshelf":
            return (a * ((a + 1) - (a - 1) * cw + s), 2 * a * ((a - 1) - (a + 1) * cw),
                    a * ((a + 1) - (a - 1) * cw - s), (a + 1) + (a - 1) * cw + s,
                    -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - s)
        return (a * ((a + 1) + (a - 1) * cw + s), -2 * a * ((a - 1) + (a + 1) * cw),
                a * ((a + 1) + (a - 1) * cw - s), (a + 1) - (a - 1) * cw + s,
                2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - s)
    if kind == "highpass":
        return ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2, 1 + alpha, -2 * cw, 1 - alpha)
    return ((1 - cw) / 2, 1 - cw, (1 - cw) / 2, 1 + alpha, -2 * cw, 1 - alpha)   # lowpass


def _mag_db(coef, f, rate=48000.0):
    b0, b1, b2, a0, a1, a2 = coef
    w = 2 * math.pi * f / rate
    z1, z2 = complex(math.cos(-w), math.sin(-w)), complex(math.cos(-2 * w), math.sin(-2 * w))
    h = (b0 + b1 * z1 + b2 * z2) / (a0 + a1 * z1 + a2 * z2)
    return 20 * math.log10(max(abs(h), 1e-9))


BAND_KINDS = {
    "Peak": "peak", "Low Shelf": "lowshelf", "High Shelf": "highshelf",
    "High Pass": "highpass", "Low Pass": "lowpass",
}


def eq_filters(ch, m):
    """The channel's active filters as biquads, for the response curve."""
    filters = []
    if ch["lc"]:
        # 6 dB/oct per order: a first-order-like slope drawn as cascaded 2nd-order sections
        order = ch["lc_slope"] + 1
        for _ in range(order // 2):
            filters.append(_biquad("highpass", ch["lc_freq"], 0, 0.707))
        if order % 2:
            filters.append(_biquad("highpass", ch["lc_freq"], 0, 0.5))
    if ch["eq"]:
        types1, types3 = m.eq_types
        bands = ((types1[min(ch["eq_t1"], len(types1) - 1)], 1), ("Peak", 2),
                 (types3[min(ch["eq_t3"], len(types3) - 1)], 3))
        for name, b in bands:
            filters.append(_biquad(BAND_KINDS[name], ch[f"eq_f{b}"], ch[f"eq_g{b}"],
                                   ch[f"eq_q{b}"]))
    return filters


def room_filters(room, m):
    if not room["on"]:
        return []
    types1, types3 = m.eq_types
    out = []
    for b in range(1, ROOM_BANDS + 1):
        name = "Peak"
        if b == 1:
            name = types1[min(room["t1"], len(types1) - 1)]
        elif b in (8, 9):
            name = types3[min(room[f"t{b}"], len(types3) - 1)]
        out.append(_biquad(BAND_KINDS[name], room[f"f{b}"], room[f"g{b}"], room[f"q{b}"]))
    return out


def response(filters, freqs):
    """Summed magnitude response in dB of filters at each frequency."""
    return [sum(_mag_db(c, f) for c in filters) for f in freqs]


def dynamics_curve(ch, levels):
    """Output level for each input level (dB) of the compressor/expander, with make-up gain,
    for the transfer curve TotalMix draws."""
    out = []
    for x in levels:
        y = x
        if x > ch["dyn_cthr"]:
            y = ch["dyn_cthr"] + (x - ch["dyn_cthr"]) / max(ch["dyn_cratio"], 1.0)
        elif x < ch["dyn_ethr"]:
            y = ch["dyn_ethr"] - (ch["dyn_ethr"] - x) * max(ch["dyn_eratio"], 1.0)
        out.append(y + ch["dyn_gain"])
    return out
