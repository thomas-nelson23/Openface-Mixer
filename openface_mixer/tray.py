"""What the system tray icon shows: when it is there, its menu, its state and its tooltip. No Qt:
tray_icon.py makes the icon and MainWindow.command carries out what is chosen in it.
"""
from collections import namedtuple

from . import APP_NAME

# The "Tray icon" setting (ui.json, shared by all devices): (value, text in the settings)
MODES = (("background", "In background mode"), ("always", "Always"), ("never", "Never"))

# A menu entry. kind: "action", "check" (ticked when on), "radio" (one of a group), "menu" (its
# entries in children) or "separator". command: (name, preset slot or None) for
# MainWindow.command.
Item = namedtuple("Item", "kind command text checked enabled children",
                  defaults=(False, True, ()))
SEPARATOR = Item("separator", None, "")

# What the icon shows, as the status lights do: the app icon (NORMAL), a cyan dot while Dim or
# the main out mute is on (ON), greyed while the engine connects (OFF), greyed with a red dot
# while the engine is stopped or the interface can't be used (FAULT).
NORMAL, ON, OFF, FAULT = "normal", "on", "off", "fault"


def mode(value):
    """The Tray icon setting from ui.json; anything unknown is the default."""
    return value if isinstance(value, str) and value in dict(MODES) else MODES[0][0]


def wanted(mode_value, background):
    """Whether the icon is shown: always, never, or only for a mixer started without a window
    (--background, Start at login)."""
    m = mode(mode_value)
    return m == "always" or (m == "background" and bool(background))


def menu(window_shown, dim, main_muted, slot_names, active_slot):
    """The menu, top to bottom. slot_names: a name, or None for an empty slot, per preset slot;
    only the filled ones are listed, the lit one ticked."""
    presets = tuple(Item("radio", ("recall", i), f"{i + 1}  {name}", checked=i == active_slot)
                    for i, name in enumerate(slot_names) if name is not None)
    return (
        Item("action", ("hide", None) if window_shown else ("show", None),
             "Hide mixer" if window_shown else "Show mixer"),
        SEPARATOR,
        Item("check", ("dim", None), "Dim", checked=bool(dim)),
        Item("check", ("mute-main", None), "Mute main out", checked=bool(main_muted)),
        Item("menu", None, "Presets", enabled=bool(presets), children=presets),
        SEPARATOR,
        Item("action", ("quit", None), f"Quit {APP_NAME}"),
    )


def icon_state(status, dim, main_muted):
    """status: Engine.status(), or None while the engine is stopped."""
    if status is None or not status["processing"] or status["hw"] not in ("active", "starting"):
        return FAULT
    if status["hw"] == "starting":
        return OFF
    return ON if dim or main_muted else NORMAL


def tooltip(dev_name, status, hw_text, dim, main_muted):
    """Device, then the engine or hardware mixer state (hw_text: the Status section's words
    for it), then Dim and the main out mute: the icon's state in words."""
    if status is None:
        line = "Engine stopped"
    elif not status["processing"]:
        line = "Engine not responding"
    else:
        line = f"Hardware mixer: {hw_text}"
        if status["hw"] == "active" and status["rate"]:
            line = f"{status['rate'] / 1000:g} kHz · {line}"
    lines = [f"{APP_NAME} — {dev_name.replace('RME ', '')}", line]
    on = [text for text, is_on in (("Main out muted", main_muted), ("Dim on", dim)) if is_on]
    if on:
        lines.append(", ".join(on))
    return "\n".join(lines)
