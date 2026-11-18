"""Device settings held in the kernel's ALSA mixer controls, PipeWire card info, and PipeWire's
sample rate and buffer.

The snd-usb-audio Digiface quirk exposes clock source, per-port output format and per-port
input status as ALSA controls, and snd-hdspm or snd-hdspe the RayDAT's clock settings and input
status; we drive them with amixer. See docs/HARDWARE.md.
"""
import os
import re
import subprocess
from pathlib import Path


DIGIFACE_CARD = "Digiface USB"


def run(cmd, timeout=2):
    """Runs amixer, pactl or pw-metadata without ever hanging the window. Returns (its output,
    None), or (None, why it failed): "missing" (no such program), "timeout", or its own
    complaint (the last line, in English: "amixer: Control hw:1 element write error: Device or
    resource busy")."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, LC_ALL="C"))
    except FileNotFoundError:
        return None, "missing"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as e:
        return None, str(e)
    if p.returncode:
        return None, (p.stderr.strip().splitlines() or [f"{cmd[0]} failed"])[-1]
    return p.stdout, None


class Hardware:
    """The ALSA card whose short name contains card_name."""

    def __init__(self, card_name=DIGIFACE_CARD):
        self.card_name = card_name
        self.exact = None       # the full name of the card to use when several match
        self.card = None
        self.ctrls = {}
        self.error = None       # why read() could not read a card that is there (see run())

    @staticmethod
    def find_cards(card_name):
        """[(card number, short name)] of the ALSA cards whose name has card_name in it."""
        if not card_name:
            return []
        try:
            text = Path("/proc/asound/cards").read_text()
        except OSError:
            return []
        # a name ending in a letter or digit must end there ("RME AIO" isn't "RME AIO Pro"),
        # as the engine's ofm_card_match
        pat = re.escape(card_name) + (r"(?! ?[A-Za-z0-9])" if card_name[-1].isalnum() else "")
        return [(int(m.group(1)), m.group(2).strip())
                for m in re.finditer(r"^\s*(\d+)\s+\[.*?\]:.*?-\s*(.*)$", text, re.M)
                if re.search(pat, m.group(2))]

    @staticmethod
    def find_card(card_name=DIGIFACE_CARD, exact=None):
        """The number of the card named exact, else of the first that matches, else None."""
        cards = Hardware.find_cards(card_name)
        return next((n for n, name in cards if name == exact), cards[0][0] if cards else None)

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
        self.card = self.find_card(self.card_name, self.exact)
        self.error = None
        if self.card is None:
            return None
        out, self.error = run(["amixer", "-c", str(self.card), "contents"])
        if out is None:
            return None
        self.ctrls = parse_amixer_contents(out)
        return self.ctrls

    def write(self, name, value):
        """Sets a control by name. Controls seen by the last read() are addressed by numid, so
        it works for any interface (snd-hdspe's settings are CARD controls, not MIXER).
        Returns None, or why the control was not set (see run())."""
        if self.card is None:
            return None
        c = self.ctrls.get(name)
        ident = f"numid={c['numid']}" if c else f"name={name}"
        return run(["amixer", "-q", "-c", str(self.card), "cset", ident, str(value)])[1]


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
    """Returns None, or why the profile was not set (see run())."""
    return run(["pactl", "set-card-profile", card_name, profile], timeout=3)[1]


# PipeWire's sample rates and buffer sizes (quantum, in frames) the Clock section offers. Every
# supported interface runs at all of these rates.
PW_RATES = (44100, 48000, 88200, 96000, 176400, 192000)
PW_QUANTA = (32, 64, 128, 256, 512, 1024, 2048)
FORCE_RATE, FORCE_QUANTUM = "clock.force-rate", "clock.force-quantum"


def pw_clock():
    """PipeWire's settings metadata as {key: whole number}: clock.force-rate and
    clock.force-quantum (0 or left out = not forced), clock.min-quantum and clock.max-quantum.
    None if it can't be read (PipeWire not running, no pw-metadata)."""
    out, _why = run(["pw-metadata", "-n", "settings", "0"])
    if out is None:
        return None
    found = {}
    for m in re.finditer(r"key:'(clock\.[\w.-]+)' value:'(\d+)'", out):
        found[m.group(1)] = int(m.group(2))
    return found or None     # clock.rate is always there


def set_pw_clock(key, value):
    """Sets clock.force-rate or clock.force-quantum (0: back to what PipeWire chooses). These
    are PipeWire's, for every program and device, until it restarts. Returns None, or why the
    setting was not made (see run())."""
    return run(["pw-metadata", "-n", "settings", "0", key, str(value)])[1]
