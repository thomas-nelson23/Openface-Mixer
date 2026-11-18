"""Desktop sinks and sources: a stereo sink on any pair of playback channels ("Openface Music" on
playback 3/4) and a mono or stereo source on any input channel or pair (a mic for a chat app),
which the engine makes through PipeWire. No Qt.

st["sinks"] is the device's list, [{"kind": "sink" or "source", "ch": its first channel,
"stereo": bool, "name": str}], part of its state but not of a mix. The default is the playback
sink the engine always made, on the pair the driver's first two playback channels feed. The
engine reads the list as sinks.conf (file_text()), with every channel as the interface's PCM
channel at each speed, so one on a channel the interface lacks at a sample rate is left out
there and comes back at a rate that has it.
"""
import re

from .model import N_IN, N_PLAY, is_index

KINDS = ("sink", "source")
MAX = 32                # MAX_NODES in engine/openface-mixer-engine.c
NAME_MAX = 60
DESC_BYTES = 95         # what the engine takes (struct pw_node_cfg's desc, without the NUL)
PREFIX = "Openface"
SINK_NAME = "openface_mixer_playback"      # SINK_NAME in the engine: the playback sink's node


def kind_of(entry):
    """The mixer kind a sink feeds ("play") or a source records ("in")."""
    return "play" if entry["kind"] == "sink" else "in"


def channels(entry):
    return [entry["ch"], entry["ch"] + 1] if entry["stereo"] else [entry["ch"]]


def description(name):
    """What the desktop's sound settings show: "Openface Music" for "Music"."""
    return name if name.startswith(PREFIX) else f"{PREFIX} {name}"


def clean_name(text):
    """A name as typed, made fit for the engine: no quotes or backslashes, no control
    characters, one space between words, and short enough."""
    name = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", re.sub(r'["\\]', "", text)).split())
    name = name[:NAME_MAX]
    while len(description(name).encode()) > DESC_BYTES:
        name = name[:-1].rstrip()
    return name


def engine_name(dev):
    """The interface as the engine names it in the playback sink ("Fireface 802 USB")."""
    return dev.name.replace("RME ", "").replace("HDSPe ", "").replace(" (USB)", " USB")


def default_pair(dev):
    """The playback pair the driver's first two channels feed: the playback sink's."""
    return dev.pcm_channels("play", 1)[0]


def default_list(dev):
    name = "Openface Mixer Playback" + ("" if dev.key == "digiface" else
                                        f" ({engine_name(dev)})")
    return [{"kind": "sink", "ch": default_pair(dev), "stereo": True, "name": name}]


def node_name(dev, entry):
    """The PipeWire node name, which the desktop remembers its choices by: it follows the
    channels, not the name, and the sink on the first pair keeps the playback sink's."""
    key = "" if dev.key == "digiface" else f"_{dev.key}"
    if entry["kind"] == "sink" and entry["ch"] == default_pair(dev):
        return SINK_NAME + key
    where = "_".join(str(c + 1) for c in channels(entry))
    return f"openface_mixer{key}_{'play' if entry['kind'] == 'sink' else 'in'}_{where}"


def pcm_map(dev, entry):
    """The entry's PCM channels at 1x, 2x and 4x speed: [(left, right)], -1 for none (the
    right of a mono source, or both where the interface lacks one of its channels)."""
    out = []
    for mode in (1, 2, 4):
        pcm = dev.pcm_channels(kind_of(entry), mode)
        chans = channels(entry)
        if all(c in pcm for c in chans):
            idx = [pcm.index(c) for c in chans]
            out.append((idx[0], idx[1] if len(idx) > 1 else -1))
        else:
            out.append((-1, -1))
    return out


def find(lst, kind, chans):
    """The index of the entry of this kind on any of these channels, or None."""
    return next((i for i, e in enumerate(lst)
                 if e["kind"] == kind and set(channels(e)) & set(chans)), None)


def checked(lst, dev):
    """st["sinks"] as read from a file. Not a list: the default. An entry that is damaged, on
    channels the interface never has, or on a channel an earlier one has is left out."""
    if not isinstance(lst, list):
        return default_list(dev)
    out = []
    for e in lst:
        if not isinstance(e, dict) or len(out) == MAX:
            continue
        kind, ch, stereo = e.get("kind"), e.get("ch"), e.get("stereo")
        name = clean_name(e["name"]) if isinstance(e.get("name"), str) else ""
        n = N_PLAY if kind == "sink" else N_IN
        if (kind not in KINDS or not isinstance(stereo, bool) or not name
                or (kind == "sink" and not stereo) or not is_index(ch, n)
                or (stereo and (ch % 2 or ch + 1 >= n))):
            continue
        entry = {"kind": kind, "ch": ch, "stereo": stereo, "name": name}
        if pcm_map(dev, entry) == [(-1, -1)] * 3 or find(out, kind, channels(entry)) is not None:
            continue
        out.append(entry)
    return out


def file_text(lst, dev):
    """sinks.conf for the engine (nodes_parse() in engine/openface-mixer-engine.c)."""
    lines = ["# The desktop sinks and sources of Openface Mixer's engine. Written by the app:"
             " change them there."]
    for e in lst:
        pcm = " ".join(f"{a} {b}" for a, b in pcm_map(dev, e))
        lines.append(f"{e['kind']} {node_name(dev, e)} {pcm} {description(e['name'])}")
    return "\n".join(lines) + "\n"
