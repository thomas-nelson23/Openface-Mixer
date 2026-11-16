"""Preset snapshots, like TotalMix's 8 mix snapshots, plus export/import of single mixes."""
import json
from pathlib import Path

from .config import BAD_FILE, atomic_write
from .model import N_SLOTS, checked_mix

FILE_FORMAT = "openface-mixer-mix"


class PresetBank:
    """8 slots; each is None or {"name": str, "mix": {...}} (mix = model.extract_mix())."""

    def __init__(self, path):
        self.path = Path(path)
        self.slots = [None] * N_SLOTS
        self.load()

    def load(self):
        """Read the slots. A file that can't be read or isn't a preset file gives eight empty
        slots; a single slot whose mix is damaged is left empty."""
        self.slots = [None] * N_SLOTS
        try:
            data = json.loads(self.path.read_text())
        except BAD_FILE:
            return
        slots = data.get("slots") if isinstance(data, dict) else None
        for i, s in enumerate(slots[:N_SLOTS] if isinstance(slots, list) else ()):
            if not isinstance(s, dict):
                continue
            try:
                mix = checked_mix(s.get("mix"))
            except ValueError:
                continue
            name = s.get("name")
            self.slots[i] = {"name": name if name and isinstance(name, str) else f"Mix {i + 1}",
                             "mix": mix}

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
    atomic_write(Path(path), json.dumps({"format": FILE_FORMAT, "version": 1, "name": name,
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
    mix = checked_mix(data.get("mix"))
    if not mix:
        raise ValueError("mix file contains no mixer settings")
    name = data.get("name")
    return name if name and isinstance(name, str) else Path(path).stem, mix
