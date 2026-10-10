"""Device settings held in the kernel's ALSA mixer controls, and PipeWire card info.

The snd-usb-audio Digiface quirk exposes clock source, per-port output format and per-port
input status as ALSA controls, and snd-hdspm or snd-hdspe the RayDAT's clock settings and input
status; we drive them with amixer. See docs/HARDWARE.md.
"""
import re
import subprocess
from pathlib import Path


DIGIFACE_CARD = "Digiface USB"


class Hardware:
    """The ALSA card whose short name contains card_name."""

    def __init__(self, card_name=DIGIFACE_CARD):
        self.card_name = card_name
        self.card = None
        self.ctrls = {}

    @staticmethod
    def find_card(card_name=DIGIFACE_CARD):
        if not card_name:
            return None
        try:
            text = Path("/proc/asound/cards").read_text()
        except OSError:
            return None
        for m in re.finditer(r"^\s*(\d+)\s+\[.*?\]:.*?-\s*(.*)$", text, re.M):
            if card_name in m.group(2):
                return int(m.group(1))
        return None

    @staticmethod
    def card_driver(card):
        """The ALSA driver name of card number card (e.g. "HDSPM", "HDSPe"), or None."""
        try:
            text = Path("/proc/asound/cards").read_text()
        except OSError:
            return None
        m = re.search(rf"^\s*{card}\s+\[.*?\]:\s*(\S+)\s+-", text, re.M)
        return m.group(1) if m else None

    @staticmethod
    def pci_vendor(card):
        """The PCI vendor ID of card number card, or 0 if it isn't a PCI card."""
        try:
            return int(Path(f"/sys/class/sound/card{card}/device/vendor").read_text(), 16)
        except (OSError, ValueError):
            return 0

    def read(self):
        """{control name: {"value": int, "values": [int], "items": [str], "rw": bool, "numid":
        int}} or None if absent. "value" is the first of "values"; switches read as 1 (on) or 0
        (off)."""
        self.card = self.find_card(self.card_name)
        if self.card is None:
            return None
        try:
            out = subprocess.run(["amixer", "-c", str(self.card), "contents"],
                                 capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        self.ctrls = parse_amixer_contents(out)
        return self.ctrls

    def write(self, name, value):
        """Sets a control by name. Controls seen by the last read() are addressed by numid, so
        it works for any interface (snd-hdspe's settings are CARD controls, not MIXER)."""
        if self.card is None:
            return
        c = self.ctrls.get(name)
        ident = f"numid={c['numid']}" if c else f"name={name}"
        subprocess.run(["amixer", "-q", "-c", str(self.card), "cset", ident, str(value)],
                       capture_output=True, timeout=2)


def parse_amixer_contents(out):
    ctrls, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"numid=(\d+),iface=\w+,name='(.*)'", line)
        if m:
            cur = {"name": m.group(2), "numid": int(m.group(1)), "items": [], "value": None,
                   "values": [], "rw": False}
            ctrls[cur["name"]] = cur
            continue
        if cur is None:
            continue
        m = re.match(r"\s*; type=\w+,access=(\S+)", line)
        if m:
            cur["rw"] = "w" in m.group(1)[:2]
        m = re.match(r"\s*; Item #\d+ '(.*)'", line)
        if m:
            cur["items"].append(m.group(1))
        m = re.match(r"\s*: values=(\S+)", line)
        if m:
            try:
                cur["values"] = [1 if v == "on" else 0 if v == "off" else int(v)
                                 for v in m.group(1).split(",")]
            except ValueError:
                cur["values"] = []
            cur["value"] = cur["values"][0] if cur["values"] else None
    return ctrls


def pw_digiface_card():
    """Returns (pulse card name, active profile, whether an input node exists)."""
    try:
        out = subprocess.run(["pactl", "list", "cards"], capture_output=True, text=True,
                             timeout=3).stdout
        srcs = subprocess.run(["pactl", "list", "sources", "short"], capture_output=True,
                              text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None, None, False
    name = profile = None
    for block in out.split("\nCard #"):
        m = re.search(r"Name: (alsa_card\.usb-RME_Digiface\S*)", block)
        if m:
            name = m.group(1)
            p = re.search(r"Active Profile: (\S+)", block)
            profile = p.group(1) if p else None
            break
    has_in = any("alsa_input.usb-RME_Digiface" in line for line in srcs.splitlines())
    return name, profile, has_in


def set_card_profile(card_name, profile):
    subprocess.run(["pactl", "set-card-profile", card_name, profile])
