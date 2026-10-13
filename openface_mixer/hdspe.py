"""RME HDSPe PCIe cards (RayDAT, AIO, AIO Pro, AES): channel layouts and the settings TotalMix
shows. Pure Python, no Qt.

Two drivers run these cards: the kernel's snd-hdspm (cards before 2022) and the out-of-tree
snd-hdspe (github.com/Schroedingers-Cat/snd-hdspe, needed for cards from 2022 on). Both expose the
hardware mixer, which the engine drives (engine/hdspe.c), and the clock settings and input
status as ALSA controls, which the GUI reads and writes with amixer (hardware.Hardware), as for
the Digiface. The two drivers, and the cards, name those controls differently; DRIVERS below has
them all. See docs/HARDWARE.md.

Channel numbers are the card's own (for inputs, playback, outputs, mixer and meters):
  RayDAT   0/1 AES, 2/3 S/PDIF, then ADAT 1-4: 8 channels per port at single speed (4-35), 4 at
           double speed (4-19) and 2 at quad speed (4-11).
  AIO, AIO Pro
           0/1 analog, 6/7 phones (outputs only), 8/9 AES, 10/11 S/PDIF, ADAT from 12 (8, 4 or 2
           channels). 2-5 belong to the optional AI4S/AO4S extension boards, left out for now.
  AES      AES 1-16 at every speed.
The MADI cards are left out: their 64 channels don't fit the 36-channel mix model.
"""
import re

HDSPM, HDSPE = "HDSPM", "HDSPe"       # ALSA driver names of snd-hdspm and snd-hdspe
VENDOR_RME = 0x1d18                   # PCI vendor of the 2022-and-later cards (older: Xilinx)


class Model:
    key = ""
    card = ""                 # ALSA card short name, before the serial ("_1a2b3c") if any
    sink_channels = ""        # what playback channels 1/2 are called
    phones_pairs = ()
    # (label, snd-hdspm sync check, frequency control) for the INPUT STATUS box; snd-hdspe
    # reports the same inputs, named after its Preferred AutoSync Reference items
    status_inputs = ()

    def channels(self, kind, mode):
        raise NotImplementedError

    def chan_label(self, kind, c, mode):
        raise NotImplementedError

    def pair_label(self, kind, c, mode):
        a = self.chan_label(kind, c, mode)
        b = self.chan_label(kind, c + 1, mode)
        head, _, last = a.rpartition(" ")
        if last in ("L", "R"):
            return head
        return f"{a}/{b.rpartition(' ')[2]}"


def _status(*labels, check="SyncCheck"):
    out = []
    for label in labels:
        hw = {"Word Clock": "WC", "S/PDIF": "SPDIF", "Sync In": "SYNC IN"}.get(
            label, label.replace(" ", ""))
        out.append((label, f"{hw} {check}", f"{hw} Frequency"))
    return tuple(out)


class RayDAT(Model):
    key = "raydat"
    card = "RME RayDAT"
    sink_channels = "A1 1/2"          # the driver's PCM channels start with ADAT 1
    first_adat = 4
    status_inputs = _status("Word Clock", "AES", "S/PDIF", "ADAT 1", "ADAT 2", "ADAT 3",
                            "ADAT 4", "TCO", "Sync In")

    def n_channels(self, mode):
        """Channels per direction at speed mode 1/2/4: 36, 20 or 12."""
        return self.first_adat + 4 * 8 // mode

    def channels(self, kind, mode):
        return list(range(self.n_channels(mode)))

    def chan_label(self, kind, c, mode):
        if c < 2:
            return "AES " + "LR"[c]
        if c < self.first_adat:
            return "SPDIF " + "LR"[c - 2]
        per = 8 // mode
        a = c - self.first_adat
        return f"A{a // per + 1} {a % per + 1}"


class AIO(Model):
    key = "aio"
    card = "RME AIO"
    sink_channels = "AN 1/2"
    phones_pairs = (3,)               # PH L/R, channels 6/7
    first_adat = 12
    status_inputs = _status("Word Clock", "AES", "S/PDIF", "ADAT", "TCO", "Sync In")
    NAMES = {0: "AN 1", 1: "AN 2", 6: "PH L", 7: "PH R", 8: "AES L", 9: "AES R",
             10: "SPDIF L", 11: "SPDIF R"}

    def channels(self, kind, mode):
        fixed = [0, 1] + ([6, 7] if kind != "in" else []) + [8, 9, 10, 11]
        return fixed + list(range(self.first_adat, self.first_adat + 8 // mode))

    def chan_label(self, kind, c, mode):
        if c >= self.first_adat:
            return f"A {c - self.first_adat + 1}"
        return self.NAMES.get(c, str(c + 1))

    def pair_label(self, kind, c, mode):
        if c == 0:
            return "AN 1/2"
        return super().pair_label(kind, c, mode)


class AIOPro(AIO):
    key = "aiopro"
    card = "RME AIO Pro"


class AES(Model):
    key = "aes"
    card = "RME AES32"
    sink_channels = "AES 1/2"
    status_inputs = _status("Word Clock", *(f"AES {n}" for n in range(1, 9)), "TCO", "Sync In",
                            check="Sync Check")

    def channels(self, kind, mode):
        return list(range(16))

    def chan_label(self, kind, c, mode):
        return f"AES {c + 1}"


MODELS = {m.key: m for m in (RayDAT(), AIO(), AIOPro(), AES())}


# ------------------------------------------------------------------ settings (ALSA controls)
# Control names per driver, by role; the first that the card has is used. snd-hdspm:
# snd_hdspm_controls_{raydat,aio,aes32} in the kernel. snd-hdspe: hdspe_control.c (hdspeconf
# shows the same settings).
DRIVERS = {
    HDSPM: {
        "clock": ("Clock Mode", "System Clock Mode"),               # Master, AutoSync
        "pref": ("Pref Sync Ref", "Preferred Sync Reference"),      # Word Clock, ADAT 1, ...
        "internal": ("Internal Clock",),                            # 32 kHz ... 192 kHz
        "spdif_in": ("S/PDIF Input",),
        "in_level": ("Input Level",),
        "out_level": ("Output Level",),
        "phones_level": ("Phones Level",),
        "ds_mode": ("Double Speed Wire Mode",),
        "qs_mode": ("Quad Speed Wire Mode",),
    },
    HDSPE: {
        "clock": ("Clock Mode",),                                   # AutoSync, Master
        "pref": ("Preferred AutoSync Reference",),                  # WordClk, AES, ...
        "internal": ("Internal Frequency",),                        # 32 KHz ... 192 KHz
        "spdif_in": ("S/PDIF In",),                                 # Optical, Coaxial, Internal
        "in_level": ("Input Level",),
        "out_level": ("Output Level",),
        "phones_level": ("Phones Level",),
        "ds_mode": ("Double Speed Mode",),
        "qs_mode": ("Quad Speed Mode",),
    },
}
CLOCK_COMBOS = ("clock", "pref", "internal")
OPTION_COMBOS = (                         # (role, label); each shows when the card has it
    ("spdif_in", "S/PDIF in"),
    ("in_level", "Input level"),
    ("out_level", "Output level"),
    ("phones_level", "Phones level"),
    ("ds_mode", "Double speed"),
    ("qs_mode", "Quad speed"),
)
COMBOS = CLOCK_COMBOS + tuple(role for role, _label in OPTION_COMBOS)
TOGGLES = (                               # (control, label); each shows when the card has it
    ("S/PDIF Out Optical", "S/PDIF out optical"),
    ("S/PDIF Out Professional", "S/PDIF out professional"),
    ("Professional", "AES out professional"),
    ("Emphasis", "AES out emphasis"),
    ("Non Audio", "AES out non-audio"),
    ("XLR Breakout Cable", "XLR breakout cable"),
    ("Single Speed WordClock Out", "Word clock out always single speed"),     # snd-hdspm
    ("Single Speed WordClk Out", "Word clock out always single speed"),       # snd-hdspe
    ("Clear TMS", "Clear track marker (TMS) bits"),
    ("Clear Track Marker", "Clear track marker (TMS) bits"),
    ("ADAT1 Internal", "ADAT 1 in from internal AEB/TEB board"),
    ("ADAT2 Internal", "ADAT 2 in from internal AEB/TEB board"),
    ("ADAT Internal", "ADAT in from internal AEB/TEB board"),
    ("ADAT internal (AEB/TEB)", "ADAT in from internal AEB/TEB board"),
)
# snd-hdspe reports all inputs in two multi-value controls, one value per item of the
# Preferred AutoSync Reference control; these are its item names.
HDSPE_STATUS = "AutoSync Status"
HDSPE_FREQ = "AutoSync Frequency"
HDSPE_CURRENT = "Current AutoSync Reference"   # the pref items, then "Intern"
HDSPE_LABELS = {"WordClk": "Word Clock", "SyncIn": "Sync In", "Intern": "Internal"}


def display_item(text):
    """An enumerated control's item as the panel shows it: "ADAT 1", "48 kHz"."""
    return _khz(_label(text))


def driver_usable(driver, pci_vendor):
    """False for a card the driver can't run: a 2022-and-later card under snd-hdspm (the engine
    applies the same rule, hdspe_driver_usable)."""
    return not (driver == HDSPM and pci_vendor == VENDOR_RME)


def profile(ctrls):
    """Which driver's controls these are: HDSPE, HDSPM, or None if neither."""
    if not ctrls:
        return None
    if any(n in ctrls for n in DRIVERS[HDSPE]["pref"]):
        return HDSPE
    if any(n in ctrls for n in DRIVERS[HDSPM]["pref"] + DRIVERS[HDSPM]["clock"]):
        return HDSPM
    return None


def control(ctrls, role):
    """The control playing role ("clock", "pref", ...) under the driver of ctrls, or
    (None, None)."""
    drv = profile(ctrls)
    for name in DRIVERS[drv].get(role, ()) if drv else ():
        if name in ctrls:
            return name, ctrls[name]
    return None, None


def item(ctrls, name, i=0):
    """The item text of value i of an enumerated control, or None."""
    c = ctrls.get(name) if ctrls else None
    if not c or len(c.get("values") or [c["value"]]) <= i:
        return None
    v = (c.get("values") or [c["value"]])[i]
    if v is None or not 0 <= v < len(c["items"]):
        return None
    return c["items"][v]


def _khz(text):
    return text.replace("KHz", "kHz") if text else text


def _label(text):
    if not text:
        return text
    return HDSPE_LABELS.get(text, re.sub(r"^(ADAT|AES)(\d+)$", r"\1 \2", text))


def _hdspe_inputs(ctrls):
    pref = control(ctrls, "pref")[1]["items"]
    inputs = []
    for i, ref in enumerate(pref):
        state = item(ctrls, HDSPE_STATUS, i)
        if state is None or state == "N/A":
            continue
        f = _khz(item(ctrls, HDSPE_FREQ, i))
        inputs.append((_label(ref), state, f if state in ("Lock", "Sync") and f else None))
    return inputs


def _hdspe_rate(ctrls):
    raw = (ctrls.get("Raw Sample Rate") or {}).get("values") or []
    if len(raw) == 2 and raw[1]:
        return round(raw[0] / raw[1])
    return 0


def decode_status(ctrls, model=MODELS["raydat"]):
    """The CLOCK and INPUT STATUS boxes from amixer's controls (hardware.parse_amixer_contents):
    {"driver", "source", "rate", "inputs": [(label, state, rate text)], "running",
    "firmware"}, inputs without the optional TCO module left out. None if the card's controls
    are missing."""
    drv = profile(ctrls)
    if drv is None:
        return None
    mode = item(ctrls, control(ctrls, "clock")[0])
    if drv == HDSPE:
        inputs = _hdspe_inputs(ctrls)
        rate = _hdspe_rate(ctrls)
        current = _label(item(ctrls, HDSPE_CURRENT))
    else:
        inputs = []
        for label, sync, freq in model.status_inputs:
            state = item(ctrls, sync)
            if state is None or state == "N/A":
                continue
            f = item(ctrls, freq)
            inputs.append((label, state,
                           f if state in ("Lock", "Sync") and f != "No Lock" else None))
        rate = ctrls.get("System Sample Rate", {}).get("value") or 0
        current = None
    if mode == "AutoSync":
        source = "AutoSync, prefers " + (_label(item(ctrls, control(ctrls, "pref")[0])) or "?")
        if current:
            source = f"AutoSync, on {current}"
    else:
        source = mode or "—"
    running = (ctrls.get("Running") or {}).get("value")
    return {"driver": drv, "source": source, "rate": rate, "inputs": inputs,
            "running": bool(running), "firmware": (ctrls.get("Firmware Build") or {}).get("value")}
