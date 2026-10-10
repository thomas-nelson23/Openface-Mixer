"""RME HDSPe RayDAT (PCIe): channel layout and the settings TotalMix shows. Pure Python, no Qt.

Two drivers run the card: the kernel's snd-hdspm (cards before 2022) and the out-of-tree
snd-hdspe (github.com/Schroedingers-Cat/snd-hdspe, needed for cards from 2022 on). Both expose the
hardware mixer, which the engine drives (engine/raydat.c), and the clock settings and input
status as ALSA controls, which the GUI reads and writes with amixer (hardware.Hardware), as for
the Digiface. The two name those controls differently; DRIVERS below has both. See
docs/HARDWARE.md.

Channel layout (the card's own numbering, for inputs, playback, outputs, mixer and meters):
  0/1 AES, 2/3 S/PDIF, then ADAT 1-4: 8 channels per port at single speed (4-35), 4 at double
  speed (4-19) and 2 at quad speed (4-11). The channel numbers above 3 move with the speed mode,
  as on the Digiface.
"""

CARD_NAME = "RME RayDAT"          # ALSA short name, followed by the serial number
N_ADAT_PORTS = 4
FIRST_ADAT = 4


def n_channels(mode):
    """Channels per direction at speed mode 1/2/4: 36, 20 or 12."""
    return FIRST_ADAT + N_ADAT_PORTS * 8 // mode


def channels(kind, mode):
    return list(range(n_channels(mode)))


def chan_label(kind, c, mode):
    if c < 2:
        return "AES " + "LR"[c]
    if c < FIRST_ADAT:
        return "SPDIF " + "LR"[c - 2]
    per = 8 // mode
    a = c - FIRST_ADAT
    return f"A{a // per + 1} {a % per + 1}"


def pair_label(kind, c, mode):
    a = chan_label(kind, c, mode)
    if c < FIRST_ADAT:
        return a.split()[0]
    return f"{a}/{chan_label(kind, c + 1, mode).split()[1]}"


# ------------------------------------------------------------------ settings (ALSA controls)
HDSPM, HDSPE = "HDSPM", "HDSPe"       # ALSA driver names of snd-hdspm and snd-hdspe
VENDOR_RME = 0x1d18                   # PCI vendor of the 2022-and-later cards (older: Xilinx)

# Control names per driver, by role. snd-hdspm: snd_hdspm_controls_raydat in the kernel.
# snd-hdspe: hdspe_control.c (its RayDAT page in hdspeconf shows the same settings).
DRIVERS = {
    HDSPM: {
        "clock": "Clock Mode",                    # Master, AutoSync
        "pref": "Pref Sync Ref",                  # Word Clock, ADAT 1-4, AES, SPDIF, Sync In
        "internal": "Internal Clock",             # 32 kHz ... 192 kHz, used while master
    },
    HDSPE: {
        "clock": "Clock Mode",                    # AutoSync, Master
        "pref": "Preferred AutoSync Reference",   # WordClk, AES, S/PDIF, ADAT1-4, (TCO,) SyncIn
        "internal": "Internal Frequency",         # 32 KHz ... 192 KHz
        "spdif_in": "S/PDIF In",                  # Optical, Coaxial, Internal
    },
}
COMBOS = ("clock", "pref", "internal", "spdif_in")
TOGGLES = (                               # (control, label); each shows when the driver has it
    ("S/PDIF Out Optical", "S/PDIF out optical (on ADAT 4)"),
    ("S/PDIF Out Professional", "S/PDIF out professional"),
    ("Single Speed WordClock Out", "Word clock out always single speed"),     # snd-hdspm
    ("Single Speed WordClk Out", "Word clock out always single speed"),       # snd-hdspe
    ("Clear TMS", "Clear track marker (TMS) bits"),
    ("ADAT1 Internal", "ADAT 1 in from internal AEB/TEB board"),
    ("ADAT2 Internal", "ADAT 2 in from internal AEB/TEB board"),
)
STATUS_INPUTS = (                         # (label, snd-hdspm sync check, frequency control)
    ("Word Clock", "WC SyncCheck", "WC Frequency"),
    ("AES", "AES SyncCheck", "AES Frequency"),
    ("S/PDIF", "SPDIF SyncCheck", "SPDIF Frequency"),
    ("ADAT 1", "ADAT1 SyncCheck", "ADAT1 Frequency"),
    ("ADAT 2", "ADAT2 SyncCheck", "ADAT2 Frequency"),
    ("ADAT 3", "ADAT3 SyncCheck", "ADAT3 Frequency"),
    ("ADAT 4", "ADAT4 SyncCheck", "ADAT4 Frequency"),
    ("TCO", "TCO SyncCheck", "TCO Frequency"),
    ("Sync In", "SYNC IN SyncCheck", "SYNC IN Frequency"),
)
# snd-hdspe reports all inputs in two multi-value controls, one value per item of the
# Preferred AutoSync Reference control; these are its item names.
HDSPE_STATUS = "AutoSync Status"
HDSPE_FREQ = "AutoSync Frequency"
HDSPE_CURRENT = "Current AutoSync Reference"   # the pref items, then "Intern"
HDSPE_LABELS = {"WordClk": "Word Clock", "ADAT1": "ADAT 1", "ADAT2": "ADAT 2", "ADAT3": "ADAT 3",
                "ADAT4": "ADAT 4", "SyncIn": "Sync In", "Intern": "Internal"}


HDSPM_TOGGLES = ("S/PDIF Out Professional", "Single Speed WordClock Out")


def display_item(text):
    """An enumerated control's item as the panel shows it: "ADAT 1", "48 kHz"."""
    return _khz(_label(text))


def driver_usable(driver, pci_vendor):
    """False for a card the driver can't run: a 2022-and-later card under snd-hdspm (the engine
    applies the same rule, raydat_driver_usable)."""
    return not (driver == HDSPM and pci_vendor == VENDOR_RME)


def profile(ctrls):
    """Which driver's controls these are: HDSPE, HDSPM, or None if neither."""
    if not ctrls:
        return None
    if DRIVERS[HDSPE]["pref"] in ctrls:
        return HDSPE
    if DRIVERS[HDSPM]["pref"] in ctrls or DRIVERS[HDSPM]["clock"] in ctrls:
        return HDSPM
    return None


def control(ctrls, role):
    """The control playing role ("clock", "pref", ...) under the driver of ctrls, or None."""
    drv = profile(ctrls)
    name = DRIVERS[drv].get(role) if drv else None
    return (name, ctrls[name]) if name in ctrls else (None, None)


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
    return HDSPE_LABELS.get(text, text)


def _hdspe_inputs(ctrls):
    pref = ctrls[DRIVERS[HDSPE]["pref"]]["items"]
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


def decode_status(ctrls):
    """The CLOCK and INPUT STATUS boxes from amixer's controls (hardware.parse_amixer_contents):
    {"driver", "source", "rate", "inputs": [(label, state, rate text)], "running",
    "firmware"}, inputs without the optional TCO module left out. None if the card's controls
    are missing."""
    drv = profile(ctrls)
    if drv is None:
        return None
    names = DRIVERS[drv]
    mode = item(ctrls, names["clock"])
    if drv == HDSPE:
        inputs = _hdspe_inputs(ctrls)
        rate = _hdspe_rate(ctrls)
        current = _label(item(ctrls, HDSPE_CURRENT))
    else:
        inputs = []
        for label, sync, freq in STATUS_INPUTS:
            state = item(ctrls, sync)
            if state is None or state == "N/A":
                continue
            f = item(ctrls, freq)
            inputs.append((label, state,
                           f if state in ("Lock", "Sync") and f != "No Lock" else None))
        rate = ctrls.get("System Sample Rate", {}).get("value") or 0
        current = None
    if mode == "AutoSync":
        source = "AutoSync, prefers " + (_label(item(ctrls, names["pref"])) or "?")
        if current:
            source = f"AutoSync, on {current}"
    else:
        source = mode or "—"
    running = (ctrls.get("Running") or {}).get("value")
    return {"driver": drv, "source": source, "rate": rate, "inputs": inputs,
            "running": bool(running), "firmware": (ctrls.get("Firmware Build") or {}).get("value")}
