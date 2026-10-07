"""TotalMix-style control room functions: Main Out, Dim, Mono, Speaker B, Talkback, External In.

These are what the ARC USB's keys and encoder drive. Pure Python, no Qt. They live in
st["control_room"] (not in MIX_KEYS, so presets don't recall them, as in TotalMix) and are applied
on top of the gain matrix and output masters that model.py computes, by apply().
"""
from .model import FADER_MAX_DB, FADER_MIN_DB, N_IN, N_PAIRS, N_SRC, NEG_INF, db2lin

ENCODER_STEP_DB = 0.5     # per encoder click, like TotalMix's ARC volume steps
ENCODER_TARGETS = ("main", "phones")


def default_control_room():
    return {
        "main": 0,                # output pair that is Main Out (AD1 1/2)
        "main_b": 1,              # Speaker B pair (AD1 3/4)
        "phones": N_PAIRS - 1,    # the Digiface's headphone pair
        "dim": False,
        "dim_db": -20.0,
        "mono": False,
        "speaker_b": False,
        "talkback": False,
        "talkback_src": 0,        # hardware input channel used as the talkback mic
        "talkback_dests": [N_PAIRS - 1],
        "talkback_dim_db": -20.0,  # other signals on the talkback outputs while talking
        "ext_in": False,
        "ext_src": 0,             # left channel of the hardware input pair for External Input
        "encoder": "main",        # what the ARC encoder controls, one of ENCODER_TARGETS
    }


def control_room(st):
    """st["control_room"], created or completed with defaults."""
    cr = st.setdefault("control_room", {})
    for k, v in default_control_room().items():
        cr.setdefault(k, v)
    return cr


def toggle(st, key):
    cr = control_room(st)
    cr[key] = not cr[key]
    return cr[key]


def encoder_pair(st):
    cr = control_room(st)
    return cr["phones"] if cr["encoder"] == "phones" else cr["main"]


def step_volume(st, clicks):
    """Turn the encoder by clicks (+ = up). Returns the pair changed and its new gain (dB)."""
    pair = encoder_pair(st)
    out = st["out"][pair]
    cur = FADER_MIN_DB if out["gain"] is None or out["gain"] == NEG_INF else out["gain"]
    new = min(FADER_MAX_DB, cur + clicks * ENCODER_STEP_DB)
    out["gain"] = None if new <= FADER_MIN_DB else round(new, 2)
    return pair, NEG_INF if out["gain"] is None else out["gain"]


def apply(st, gains, out_gains, talkback=True):
    """Fold the control room switches into gains[out * N_SRC + src] and out_gains, in place.

    talkback=False leaves Talkback out, for the copy saved to disk."""
    cr = control_room(st)
    main = cr["main"]
    ml, mr = 2 * main, 2 * main + 1

    def row(o):
        return range(o * N_SRC, (o + 1) * N_SRC)

    if cr["ext_in"]:
        for o in (ml, mr):
            for i in row(o):
                gains[i] = 0.0
        src = min(cr["ext_src"], N_IN - 2)
        gains[ml * N_SRC + src] = 1.0
        gains[mr * N_SRC + src + 1] = 1.0

    if cr["mono"]:
        for il, ir in zip(row(ml), row(mr)):
            gains[il] = gains[ir] = 0.5 * (gains[il] + gains[ir])

    active = main
    if cr["speaker_b"] and cr["main_b"] != main:
        b = cr["main_b"]
        for o, ob in ((ml, 2 * b), (mr, 2 * b + 1)):
            for i, ib in zip(row(o), row(ob)):
                gains[ib], gains[i] = gains[i], 0.0
        out_gains[2 * b] = out_gains[ml]
        out_gains[2 * b + 1] = out_gains[mr]
        out_gains[ml] = out_gains[mr] = 0.0
        active = b

    if cr["dim"]:
        f = db2lin(cr["dim_db"])
        out_gains[2 * active] *= f
        out_gains[2 * active + 1] *= f

    if talkback and cr["talkback"]:
        f = db2lin(cr["talkback_dim_db"])
        src = min(cr["talkback_src"], N_IN - 1)
        for p in cr["talkback_dests"]:
            for o in (2 * p, 2 * p + 1):
                for i in row(o):
                    gains[i] *= f
                gains[o * N_SRC + src] = 1.0
    return gains, out_gains
