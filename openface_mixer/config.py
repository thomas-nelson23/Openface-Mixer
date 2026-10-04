"""Where things live on disk, and loading/saving the mixer state."""
import json
import os
from pathlib import Path

from . import APP_ID
from .model import compute_matrix, upgrade_state

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_ID
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_ID

STATE_FILE = CONFIG_DIR / "state.json"      # full GUI state (mix + UI bits)
MATRIX_FILE = CONFIG_DIR / "matrix.bin"     # flattened gains, read by the engine at startup
PRESETS_FILE = CONFIG_DIR / "presets.json"  # the 8 snapshot slots


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if isinstance(data, str):
        tmp.write_text(data)
    else:
        tmp.write_bytes(data)
    tmp.replace(path)


def load_state():
    try:
        return upgrade_state(json.loads(STATE_FILE.read_text()))
    except (OSError, ValueError):
        return upgrade_state(None)


def save_state(st):
    atomic_write(STATE_FILE, json.dumps(st))
    atomic_write(MATRIX_FILE, compute_matrix(st).tobytes())
