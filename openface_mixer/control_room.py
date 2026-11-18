"""TotalMix-style control room functions: Main Out, Dim, Recall, Mono, Speaker B, Phones,
Talkback, External In, Cue.

These are what the ARC USB's keys and encoder drive. Pure Python, no Qt. They live in
st["control_room"] (not in MIX_KEYS, so presets don't recall them, as in TotalMix) and are applied
on top of the gain matrix and output masters that model.py computes, by apply().
"""
from .model import (
    FADER_MAX_DB, FADER_MIN_DB, N_IN, N_PAIRS, N_SRC, NEG_INF, db2lin, is_index, is_num,
)

ENCODER_STEP_DB = 0.5     # per encoder click, like TotalMix's ARC volume steps
ENCODER_TARGETS = ("main", "phones", "phones2")    # Main Out, the first and the second Phones
NO_PAIR = -1              # main_b when no Speaker B pair is assigned
MAX_PHONES = 4            # as TotalMix
# 2: the Speaker B pair is silent while Speaker B is off
# 3: several Phones pairs; Talkback goes to them, and talkback_dests are further outputs
CR_VERSION = 3


def default_control_room(phones=N_PAIRS - 1):
    return {
        "main": 0,                # output pair that is Main Out (AD1 1/2, AN 1/2)
        "main_b": NO_PAIR,        # Speaker B pair, or NO_PAIR; silent while Speaker B is off
        "phones": [phones],       # up to MAX_PHONES pairs, the device's first headphones to start
        "dim": False,
        "dim_db": -20.0,
        "recall_db": -10.0,       # the Main Out level that Recall goes back to
        "mono": False,
        "speaker_b": False,
        "talkback": False,
        "talkback_src": 0,        # hardware input channel used as the talkback mic
        "talkback_dests": [],     # output pairs that get Talkback besides the Phones
        "talkback_dim_db": -20.0,  # other signals on the talkback outputs while talking
        "ext_in": False,
        "ext_src": 0,             # left channel of the hardware input pair for External Input
        "encoder": "main",        # what the ARC encoder controls, one of ENCODER_TARGETS
        "version": CR_VERSION,
    }


def control_room(st, phones=None):
    """st["control_room"], created or completed with defaults. phones is the device's phones
    pair, used only when the control room is created (load() passes it)."""
    cr = st.setdefault("control_room", {})
    if cr and cr.get("version", 1) < 2:
        # Up to version 1 the Speaker B pair (AD1 3/4 by default) kept its own mix while
        # Speaker B was off, so it may be in use as a normal output: unassign it unless
        # Speaker B is on right now.
        if not cr.get("speaker_b"):
            cr["main_b"] = NO_PAIR
        cr["version"] = CR_VERSION
    for k, v in default_control_room(N_PAIRS - 1 if phones is None else phones).items():
        cr.setdefault(k, v)
    return cr


def load(st, phones):
    """The control room of a state just read from disk: completed as in control_room(), with
    anything of the wrong type or out of range (a damaged or hand-edited file) put back to its
    default, and Talkback off, which never survives a restart."""
    def pair(v):
        return is_index(v, N_PAIRS)

    def pairs(v):
        return isinstance(v, list) and all(pair(p) for p in v)

    def cut(v):
        return is_num(v) and v <= 0

    valid = {
        "main": pair, "main_b": lambda v: v == NO_PAIR or pair(v),
        "phones": lambda v: pairs(v) and len(v) <= MAX_PHONES,
        "talkback_src": lambda v: is_index(v, N_IN), "ext_src": lambda v: is_index(v, N_IN - 1),
        "talkback_dests": pairs, "dim_db": cut, "talkback_dim_db": cut,
        "recall_db": lambda v: is_num(v) and FADER_MIN_DB <= v <= FADER_MAX_DB,
        "encoder": lambda v: v in ENCODER_TARGETS,
    }
    if not isinstance(st.get("control_room"), dict):
        st["control_room"] = {}
    cr = st["control_room"]
    one = cr.get("phones")
    if pair(one):
        # Up to version 2 there was one Phones pair, and Talkback went to talkback_dests
        # alone. Nothing on screen could change that list: it held the device's own phones
        # pair, also after another pair was chosen as Phones. Neither is a further output.
        cr["phones"] = [one]
        if pairs(cr.get("talkback_dests")):
            cr["talkback_dests"] = [p for p in cr["talkback_dests"] if p not in (one, phones)]
    for k, default in default_control_room(phones).items():
        if k in cr and not valid.get(k, lambda v: isinstance(v, type(default)))(cr[k]):
            cr[k] = default
    control_room(st, phones).update(talkback=False, version=CR_VERSION)
    return cr


def has_speaker_b(st):
    """Whether a Speaker B pair other than Main Out is assigned."""
    cr = control_room(st)
    return 0 <= cr["main_b"] < N_PAIRS and cr["main_b"] != cr["main"]


def toggle(st, key):
    cr = control_room(st)
    if key == "speaker_b" and not has_speaker_b(st):
        return cr[key]
    cr[key] = not cr[key]
    return cr[key]


def encoder_target(st, target):
    """The encoder target an ARC key stands for. With fewer than two Phones pairs the second
    Phones key is another first one: either selects the one pair, and both light up."""
    return "phones" if target == "phones2" and len(control_room(st)["phones"]) < 2 else target


def encoder_pair(st):
    """The output pair the ARC encoder turns: Main Out, or the first or second Phones pair
    (Main Out if there is none)."""
    cr = control_room(st)
    i = ENCODER_TARGETS.index(encoder_target(st, cr["encoder"])) - 1
    return cr["phones"][i] if 0 <= i < len(cr["phones"]) else cr["main"]


def step_volume(st, clicks):
    """Turn the encoder by clicks (+ = up). Returns the pair changed and its new gain (dB)."""
    pair = encoder_pair(st)
    out = st["out"][pair]
    cur = FADER_MIN_DB if out["gain"] is None or out["gain"] == NEG_INF else out["gain"]
    new = min(FADER_MAX_DB, cur + clicks * ENCODER_STEP_DB)
    out["gain"] = None if new <= FADER_MIN_DB else round(new, 2)
    return pair, NEG_INF if out["gain"] is None else out["gain"]


def apply(st, gains, out_gains, talkback=True, cue=None):
    """Fold the control room switches into gains[out * N_SRC + src] and out_gains, in place.

    talkback=False leaves Talkback out, for the copy saved to disk. cue is the output pair
    whose submix plays on Main Out instead of its own (TotalMix's Cue), or None; it is never
    saved, so the window passes it."""
    cr = control_room(st)
    main = cr["main"]
    ml, mr = 2 * main, 2 * main + 1

    def row(o):
        return range(o * N_SRC, (o + 1) * N_SRC)

    if cue is not None and cue != main:     # Main Out's own level, Dim and the rest still apply
        for o, oc in ((ml, 2 * cue), (mr, 2 * cue + 1)):
            gains[o * N_SRC:(o + 1) * N_SRC] = gains[oc * N_SRC:(oc + 1) * N_SRC]

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

    # Speaker B, as in TotalMix: the Main Out mix plays on either Main Out or the Speaker B
    # pair, and the other one is silent.
    active = main
    if has_speaker_b(st):
        b = cr["main_b"]
        on = cr["speaker_b"]
        for o, ob in ((ml, 2 * b), (mr, 2 * b + 1)):
            for i, ib in zip(row(o), row(ob)):
                gains[ib] = gains[i] if on else 0.0
                if on:
                    gains[i] = 0.0
        if on:
            out_gains[2 * b] = out_gains[ml]
            out_gains[2 * b + 1] = out_gains[mr]
            out_gains[ml] = out_gains[mr] = 0.0
            active = b
        else:
            out_gains[2 * b] = out_gains[2 * b + 1] = 0.0

    if cr["dim"]:
        f = db2lin(cr["dim_db"])
        out_gains[2 * active] *= f
        out_gains[2 * active + 1] *= f

    if talkback and cr["talkback"]:
        f = db2lin(cr["talkback_dim_db"])
        src = min(cr["talkback_src"], N_IN - 1)
        for p in {*cr["phones"], *cr["talkback_dests"]}:
            for o in (2 * p, 2 * p + 1):
                for i in row(o):
                    gains[i] *= f
                gains[o * N_SRC + src] = 1.0
    return gains, out_gains
