"""RME HDSPe RayDAT (PCIe): channel layout and the settings TotalMix shows. Pure Python, no Qt.

The kernel's snd-hdspm driver runs the card. It exposes the hardware mixer, which the engine
drives (engine/raydat.c), and the clock settings and input status as ALSA mixer controls, which
the GUI reads and writes with amixer (hardware.Hardware), as for the Digiface. See
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
# The names snd-hdspm gives the RayDAT's controls (snd_hdspm_controls_raydat in the kernel).

CLOCK_MODE = "Clock Mode"                 # Master, AutoSync
PREF_SYNC_REF = "Pref Sync Ref"           # Word Clock, ADAT 1-4, AES, SPDIF, (TCO,) Sync In
INTERNAL_RATE = "Internal Clock"          # 32 kHz ... 192 kHz, used while master
SYSTEM_RATE = "System Sample Rate"        # Hz
TOGGLES = (                               # (control, label)
    ("S/PDIF Out Professional", "S/PDIF out professional"),
    ("Single Speed WordClock Out", "Word clock out always single speed"),
)
STATUS_INPUTS = (                         # (label, sync check control, frequency control)
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


def item(ctrls, name):
    """The current item text of an enumerated control, or None."""
    c = ctrls.get(name) if ctrls else None
    if not c or c["value"] is None or not 0 <= c["value"] < len(c["items"]):
        return None
    return c["items"][c["value"]]


def decode_status(ctrls):
    """The CLOCK and INPUT STATUS boxes from amixer's controls (hardware.parse_amixer_contents):
    {"source", "rate", "inputs": [(label, state, rate text)]}, inputs without the optional TCO
    module left out. None if the card's controls are missing."""
    if not ctrls or CLOCK_MODE not in ctrls:
        return None
    rate = ctrls.get(SYSTEM_RATE, {}).get("value") or 0
    mode = item(ctrls, CLOCK_MODE)
    inputs = []
    for label, sync, freq in STATUS_INPUTS:
        state = item(ctrls, sync)
        if state is None or state == "N/A":
            continue
        f = item(ctrls, freq)
        inputs.append((label, state, f if state in ("Lock", "Sync") and f != "No Lock" else None))
    if mode == "AutoSync":
        source = "AutoSync, prefers " + (item(ctrls, PREF_SYNC_REF) or "?")
    else:
        source = mode or "—"
    return {"source": source, "rate": rate, "inputs": inputs}
