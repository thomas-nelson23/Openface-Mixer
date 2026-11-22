"""Preset snapshots, like TotalMix's 8 mix snapshots, plus export/import of single mixes and
of workspaces (everything for one interface)."""
import json
from pathlib import Path

from . import arc
from .config import BAD_FILE, atomic_write, load_meter_prefs, ready_state
from .devices import DEVICES
from .model import N_SLOTS, checked_mix, convert_pan_law, is_index, upgrade_state

FILE_FORMAT = "openface-mixer-mix"
VERSION = 2     # of presets.json and of mix files. 2: constant-power pan (convert_pan_law)
WORKSPACE_FORMAT = "openface-mixer-workspace"
WORKSPACE_VERSION = 1   # the parts inside have their own (state, presets)
# never in a workspace: solo, which never survives a restart either, the OSC remote's
# settings, which belong to this computer's network (a file must not open a port), and the
# desktop sinks and sources, which this computer's programs play to and record from
NOT_IN_WORKSPACE = ("solo", "remote", "sinks")


def read_mix(mix, data):
    """The mix found in data, a preset or mix file as parsed, checked and in today's shape.
    One from a file older than VERSION is converted, so it sounds as it did when it was saved;
    a file whose version is no number is taken as today's. Raises ValueError for a damaged
    mix."""
    mix = checked_mix(mix)
    return convert_pan_law(mix) if is_index(data.get("version", 1), VERSION) else mix


def read_slots(data):
    """(slots, damaged) from a preset file as parsed: N_SLOTS slots, each None or a checked
    {"name", "mix"}. damaged is True if data isn't a preset file or a slot had to be left
    empty because its mix is damaged."""
    slots = [None] * N_SLOTS
    found = data.get("slots") if isinstance(data, dict) else None
    damaged = not isinstance(found, list)
    for i, s in enumerate(() if damaged else found[:N_SLOTS]):
        if s is None:
            continue
        try:
            mix = read_mix(s.get("mix") if isinstance(s, dict) else None, data)
        except ValueError:
            damaged = True
            continue
        name = s.get("name")
        slots[i] = {"name": name if name and isinstance(name, str) else f"Mix {i + 1}",
                    "mix": mix}
    return slots, damaged


class PresetBank:
    """8 slots; each is None or {"name": str, "mix": {...}} (mix = model.extract_mix())."""

    def __init__(self, path):
        self.path = Path(path)
        self.slots = [None] * N_SLOTS
        self.load()

    def load(self):
        """Read the slots. A file that can't be read or isn't a preset file gives eight empty
        slots; a single slot whose mix is damaged is left empty. The next save() then keeps
        that file as presets.json.bak, so what it held is not lost for good."""
        self.slots = [None] * N_SLOTS
        self.damaged = False
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except BAD_FILE:
            data = None
        self.slots, self.damaged = read_slots(data)

    def save(self):
        atomic_write(self.path, json.dumps({"version": VERSION, "slots": self.slots}, indent=1),
                     keep_backup=self.damaged)
        self.damaged = False

    def store(self, i, name, mix):
        self.slots[i] = {"name": name, "mix": mix}
        self.save()

    def rename(self, i, name):
        if self.slots[i]:
            self.slots[i]["name"] = name
            self.save()

    def clear(self, i):
        self.slots[i] = None
        self.save()

    def name(self, i):
        return self.slots[i]["name"] if self.slots[i] else None


def export_mix(path, name, mix):
    atomic_write(Path(path), json.dumps({"format": FILE_FORMAT, "version": VERSION, "name": name,
                                         "mix": mix}, indent=1))


def import_mix(path):
    """Returns (name, mix), the mix checked and in today's shape (model.checked_mix). Raises
    ValueError for files that aren't Openface Mixer mixes or are damaged, OSError for files that
    can't be read."""
    try:
        data = json.loads(Path(path).read_text())
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
        raise ValueError("not an Openface Mixer mix file")
    mix = read_mix(data.get("mix"), data)
    if not mix:
        raise ValueError("mix file contains no mixer settings")
    name = data.get("name")
    return name if name and isinstance(name, str) else Path(path).stem, mix


def export_workspace(path, dev, st, slots, ui):
    """Everything for one interface in one file, as TotalMix's workspace: the state (mix,
    names, colours, icons, layouts, control room, device and effect settings), the preset
    slots and the viewer's options from ui.json (ui: load_meter_prefs and arc_keys)."""
    atomic_write(Path(path), json.dumps({
        "format": WORKSPACE_FORMAT, "version": WORKSPACE_VERSION, "device": dev.key,
        "state": {k: v for k, v in st.items() if k not in NOT_IN_WORKSPACE},
        "presets": {"version": VERSION, "slots": slots}, "ui": ui}, indent=1))


def import_workspace(path, dev):
    """(state, preset slots, ui options) from a workspace file for dev, each checked as the
    files they come from are (upgrade_state, ready_state, read_slots, load_meter_prefs,
    arc.assignment), and converted from older versions as those are. Raises ValueError, with
    a sentence to show, for a file that is not a workspace, is for another interface or has a
    damaged mix or preset; OSError for one that can't be read. Nothing is changed. The state
    has no solo, no OSC remote settings and no desktop sinks and sources (NOT_IN_WORKSPACE)."""
    try:
        data = json.loads(Path(path).read_text())
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict) or data.get("format") != WORKSPACE_FORMAT:
        raise ValueError("This is not an Openface Mixer workspace.")
    key = data.get("device")
    if key != dev.key:
        other = DEVICES.get(key) if isinstance(key, str) else None
        other = f"the {other.name}" if other else "another interface"
        raise ValueError(f"This workspace is for {other}, not the {dev.name}.")
    try:
        st = ready_state(upgrade_state(data.get("state"), dev.phones_pair(1), strict=True), dev)
    except ValueError:
        raise ValueError("The mix in this workspace is damaged.") from None
    st.pop("remote", None)
    st.pop("sinks", None)
    slots, damaged = read_slots(data.get("presets"))
    if damaged:
        raise ValueError("The presets in this workspace are damaged.")
    ui = data.get("ui") if isinstance(data.get("ui"), dict) else {}
    return st, slots, {**load_meter_prefs(ui), "arc_keys": arc.assignment(ui.get("arc_keys"))}
