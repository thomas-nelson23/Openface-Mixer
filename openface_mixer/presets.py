"""Preset snapshots, like TotalMix's 8 mix snapshots, plus export/import of single mixes."""
import json
from pathlib import Path

from .config import atomic_write
from .model import MIX_KEYS, N_SLOTS

FILE_FORMAT = "openface-mixer-mix"


class PresetBank:
    """8 slots; each is None or {"name": str, "mix": {...}} (mix = model.extract_mix())."""

    def __init__(self, path):
        self.path = Path(path)
        self.slots = [None] * N_SLOTS
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text())
            slots = data.get("slots", [])
            self.slots = [s if isinstance(s, dict) and "mix" in s else None for s in slots][:N_SLOTS]
            self.slots += [None] * (N_SLOTS - len(self.slots))
        except (OSError, ValueError):
            self.slots = [None] * N_SLOTS

    def save(self):
        atomic_write(self.path, json.dumps({"version": 1, "slots": self.slots}, indent=1))

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
    Path(path).write_text(json.dumps({"format": FILE_FORMAT, "version": 1, "name": name, "mix": mix},
                                     indent=1))


def import_mix(path):
    """Returns (name, mix). Raises ValueError for files that aren't Openface Mixer mixes."""
    data = json.loads(Path(path).read_text())
    if data.get("format") != FILE_FORMAT or not isinstance(data.get("mix"), dict):
        raise ValueError("not an Openface Mixer mix file")
    if not any(k in data["mix"] for k in MIX_KEYS):
        raise ValueError("mix file contains no mixer settings")
    return data.get("name") or Path(path).stem, data["mix"]
