"""Device settings held in the kernel's ALSA mixer controls, and PipeWire card info.

The snd-usb-audio Digiface quirk exposes clock source, per-port output format and per-port
input status as ALSA controls, and snd-hdspm the RayDAT's clock settings and input status; we
drive them with amixer. See docs/HARDWARE.md.
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

    def read(self):
        """{control name: {"value": int, "items": [str], "rw": bool}} or None if absent.
        Switches read as 1 (on) or 0 (off)."""
        self.card = self.find_card(self.card_name)
        if self.card is None:
            return None
        try:
            out = subprocess.run(["amixer", "-c", str(self.card), "contents"],
                                 capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        return parse_amixer_contents(out)

    def write(self, name, value):
        if self.card is None:
            return
        subprocess.run(["amixer", "-q", "-c", str(self.card), "cset", f"name={name}", str(value)],
                       capture_output=True, timeout=2)


def parse_amixer_contents(out):
    ctrls, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"numid=\d+,iface=\w+,name='(.*)'", line)
        if m:
            cur = {"name": m.group(1), "items": [], "value": None, "rw": False}
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
        m = re.match(r"\s*: values=(-?\d+|on|off)", line)
        if m:
            v = m.group(1)
            cur["value"] = 1 if v == "on" else 0 if v == "off" else int(v)
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
