"""Where things live on disk, and loading/saving the mixer state.

Each device has its own state, matrix and presets: the Digiface's in the config directory itself
(where they always were), other devices' in a subdirectory named after the device key.
"""
import json
import os
import struct
from pathlib import Path

from . import APP_ID
from . import control_room
from .devices import DEFAULT, DEVICES
from .model import compute_matrix, compute_out_gains, upgrade_state

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_ID
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_ID

MATRIX_MAGIC = 0x344D464F                   # "OFM4", see load_matrix() in the engine
DEVICE_MAGIC = 0x3144464F                   # "OFD1": device settings block after the gains
N_DEV_CMD = 512                             # OFM_N_DEV_CMD in engine/shm_layout.h
DEVICE_FILE = CONFIG_DIR / "device"         # key of the device the GUI opened last


def device_dir(dev=DEFAULT):
    return CONFIG_DIR / dev.config_subdir if dev.config_subdir else CONFIG_DIR


def state_file(dev=DEFAULT):
    return device_dir(dev) / "state.json"         # full GUI state (mix + UI bits)


def matrix_file(dev=DEFAULT):
    return device_dir(dev) / "matrix.bin"         # gains, read by the engine at startup


def presets_file(dev=DEFAULT):
    return device_dir(dev) / "presets.json"       # the 8 snapshot slots


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if isinstance(data, str):
        tmp.write_text(data)
    else:
        tmp.write_bytes(data)
    tmp.replace(path)


def load_state(dev=DEFAULT):
    phones = dev.phones_pair(1)
    try:
        st = upgrade_state(json.loads(state_file(dev).read_text()), phones)
    except (OSError, ValueError):
        st = upgrade_state(None, phones)
    control_room.control_room(st, phones)["talkback"] = False   # never survives a restart
    dev.upgrade_settings(st)
    return st


def device_block(st, dev=DEFAULT):
    """The device settings words for the engine (shared memory and matrix.bin): the
    configuration word and N_DEV_CMD command slots (0 = unused). None if the device has none."""
    config, cmds = dev.device_words(st)
    if not config and not cmds:
        return None
    if len(cmds) > N_DEV_CMD:
        raise ValueError(f"{len(cmds)} device commands, the engine takes {N_DEV_CMD}")
    return config, cmds + [0] * (N_DEV_CMD - len(cmds))


def matrix_file_bytes(st, dev=DEFAULT):
    """matrix.bin: magic, gain matrix, output gains (all little-endian), then for devices with
    settings "OFD1", the configuration word and the command slots. Solo is left out, so an
    engine started without the GUI never comes up with channels soloed."""
    gains, out_gains = control_room.apply(st, compute_matrix(st, solo=False),
                                          compute_out_gains(st), talkback=False)
    data = struct.pack("<I", MATRIX_MAGIC) + gains.tobytes() + out_gains.tobytes()
    block = device_block(st, dev)
    if block is not None:
        config, cmds = block
        data += struct.pack(f"<II{N_DEV_CMD}I", DEVICE_MAGIC, config, *cmds)
    return data


def save_state(st, dev=DEFAULT):
    atomic_write(state_file(dev), json.dumps({k: v for k, v in st.items() if k != "solo"}))
    atomic_write(matrix_file(dev), matrix_file_bytes(st, dev))


def load_device_choice():
    try:
        key = DEVICE_FILE.read_text().strip()
    except OSError:
        return None
    return key if key in DEVICES else None


def save_device_choice(dev):
    atomic_write(DEVICE_FILE, dev.key + "\n")
