"""The interfaces Openface Mixer supports, and finding which ones are connected. No Qt.

Every device uses the same mix model (model.py): up to 32 hardware inputs, 34 playback channels
and 34 outputs. A device says which of those channels exist at each speed mode, what they are
called, and where its engine, config files and PipeWire nodes are.
"""
import re
from pathlib import Path

from . import fireface800, fireface802
from .model import chan_label as digiface_chan_label, pair_label as digiface_pair_label

FIREWIRE_DEVICES = Path("/sys/bus/firewire/devices")
ASOUND_CARDS = Path("/proc/asound/cards")
RME_OUI = 0x000A35


class Device:
    key = ""
    name = ""
    bus = ""                 # "USB" or "FireWire", for messages
    phones_pairs = ()        # output pairs that are headphones
    max_routes = None        # hardware mixer crosspoint limit, or None (full matrix)

    @property
    def shm_name(self):
        return "openface-mixer" if self.key == "digiface" else f"openface-mixer-{self.key}"

    @property
    def service(self):
        return ("openface-mixer-engine.service" if self.key == "digiface"
                else f"openface-mixer-engine@{self.key}.service")

    @property
    def config_subdir(self):
        return "" if self.key == "digiface" else self.key

    def channels(self, kind, mode):
        raise NotImplementedError

    def chan_label(self, kind, c, mode):
        raise NotImplementedError

    def pair_label(self, kind, c, mode):
        raise NotImplementedError

    def phones_pair(self, mode):
        return self.phones_pairs[0]

    def present(self):
        raise NotImplementedError

    def upgrade_settings(self, st):
        """Add or complete the device's hardware settings in st (st["hw"])."""

    def device_words(self, st):
        """(configuration word, [DSP setting commands]) for the engine; (0, []) if none."""
        return 0, []

    def channel_has_settings(self, kind, c):
        """True if hardware channel c has TotalMix-style channel settings (the strip's ⚙)."""
        return False

    def channel_settings_active(self, st, kind, c):
        """True if channel c has a setting worth flagging on its strip."""
        return False


class Digiface(Device):
    key = "digiface"
    name = "RME Digiface USB"
    bus = "USB"
    phones_pairs = (16,)
    max_routes = 2048

    @staticmethod
    def _n_adat(mode):
        return 32 // mode

    def channels(self, kind, mode):
        # the two phones channels follow the ADAT block at every speed
        n = self._n_adat(mode)
        return list(range(n if kind == "in" else n + 2))

    def chan_label(self, kind, c, mode):
        return digiface_chan_label(c, self._n_adat(mode), kind != "in")

    def pair_label(self, kind, c, mode):
        return digiface_pair_label(c, self._n_adat(mode), kind != "in")

    def phones_pair(self, mode):
        return self._n_adat(mode) // 2

    def present(self):
        try:
            return "Digiface USB" in ASOUND_CARDS.read_text()
        except OSError:
            return False


class Fireface802(Device):
    key = "ff802"
    name = "RME Fireface 802"
    bus = "FireWire"
    phones_pairs = fireface802.PHONES_PAIRS
    unit_version = 0x000005      # SND_FF_UNIT_VERSION_802 in the kernel driver

    def channels(self, kind, mode):
        return fireface802.channels(kind, mode)

    def chan_label(self, kind, c, mode):
        return fireface802.chan_label(kind, c)

    def pair_label(self, kind, c, mode):
        return fireface802.pair_label(kind, c)

    def present(self):
        return find_firewire_unit(RME_OUI, self.unit_version) is not None

    def upgrade_settings(self, st):
        st["hw"] = fireface802.upgrade_settings(st.get("hw"))

    def device_words(self, st):
        return fireface802.config_word(st["hw"]), fireface802.commands(st["hw"])

    def channel_has_settings(self, kind, c):
        return kind in ("in", "out")      # phase invert on every hardware channel

    def channel_settings_active(self, st, kind, c):
        return fireface802.channel_settings_active(st["hw"], kind, c)


class Fireface800(Device):
    key = "ff800"
    name = "RME Fireface 800"
    bus = "FireWire"
    phones_pairs = fireface800.PHONES_PAIRS
    unit_version = 0x000001      # SND_FF_UNIT_VERSION_FF800 in the kernel driver

    def channels(self, kind, mode):
        return fireface800.channels(kind, mode)

    def chan_label(self, kind, c, mode):
        return fireface800.chan_label(kind, c)

    def pair_label(self, kind, c, mode):
        return fireface800.pair_label(kind, c)

    def present(self):
        return find_firewire_unit(RME_OUI, self.unit_version) is not None

    def upgrade_settings(self, st):
        st["hw"] = fireface800.upgrade_settings(st.get("hw"))

    def device_words(self, st):
        return fireface800.device_words(st["hw"])

    def channel_has_settings(self, kind, c):
        return fireface800.channel_has_settings(kind, c)

    def channel_settings_active(self, st, kind, c):
        return fireface800.channel_settings_active(st["hw"], kind, c)


DEVICES = {d.key: d for d in (Digiface(), Fireface802(), Fireface800())}
DEFAULT = DEVICES["digiface"]


def _read_hex(path):
    try:
        return int(path.read_text().strip(), 16)
    except (OSError, ValueError):
        return None


def find_firewire_unit(specifier_id, version, root=FIREWIRE_DEVICES):
    """Name of the FireWire node ("fw1") with a unit of this specifier and version, or None."""
    try:
        units = sorted(p for p in root.iterdir() if re.fullmatch(r"fw\d+\.\d+", p.name))
    except OSError:
        return None
    for u in units:
        if _read_hex(u / "specifier_id") == specifier_id and _read_hex(u / "version") == version:
            return u.name.split(".")[0]
    return None


def detect(saved=None):
    """The device to open: the saved choice if it is connected, else the first one connected,
    else the saved one, else the Digiface."""
    present = [d for d in DEVICES.values() if d.present()]
    if saved in DEVICES and DEVICES[saved] in present:
        return DEVICES[saved]
    if present:
        return present[0]
    return DEVICES.get(saved, DEFAULT)
