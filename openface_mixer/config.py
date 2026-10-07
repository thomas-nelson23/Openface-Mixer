"""Where things live on disk, and loading/saving the mixer state."""
import json
import os
import struct
from pathlib import Path

from . import APP_ID
from . import control_room
from .model import compute_matrix, compute_out_gains, upgrade_state

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_ID
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_ID

STATE_FILE = CONFIG_DIR / "state.json"      # full GUI state (mix + UI bits)
MATRIX_FILE = CONFIG_DIR / "matrix.bin"     # gains, read by the engine at startup
MATRIX_MAGIC = 0x334D464F                   # "OFM3", see load_matrix() in the engine
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
        st = upgrade_state(json.loads(STATE_FILE.read_text()))
    except (OSError, ValueError):
        st = upgrade_state(None)
    control_room.control_room(st)["talkback"] = False   # talkback never survives a restart
    return st


def matrix_file_bytes(st):
    """matrix.bin: magic, gain matrix, output gains (all little-endian). Solo is left out, so
    an engine started without the GUI never comes up with channels soloed."""
    gains, out_gains = control_room.apply(st, compute_matrix(st, solo=False),
                                          compute_out_gains(st), talkback=False)
    return struct.pack("<I", MATRIX_MAGIC) + gains.tobytes() + out_gains.tobytes()


def save_state(st):
    atomic_write(STATE_FILE, json.dumps({k: v for k, v in st.items() if k != "solo"}))
    atomic_write(MATRIX_FILE, matrix_file_bytes(st))
