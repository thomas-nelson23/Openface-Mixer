"""Commands for a running window, for desktop shortcuts: `openface-mixer --dim` and the like
send one line of text each over the window's single-instance socket (app.tell_running). No Qt.
"""
from .model import N_SLOTS

OPTIONS = {"--show": "show", "--dim": "dim", "--mute-main": "mute-main"}
SLOTS = [str(n) for n in range(1, N_SLOTS + 1)]
MAX_LINE = 64           # anything longer is not a command


def from_args(args):
    """The lines to send for the commands on the command line, in order. ValueError if
    --recall has no preset number."""
    lines = []
    for i, arg in enumerate(args):
        if arg in OPTIONS:
            lines.append(OPTIONS[arg])
        elif arg == "--recall":
            if i + 1 == len(args) or args[i + 1] not in SLOTS:
                raise ValueError(f"--recall: a preset number from 1 to {N_SLOTS}")
            lines.append(f"recall {args[i + 1]}")
    return lines


def parse(line):
    """(command, preset slot from 0 or None) for one line received, or None if it is not one."""
    if len(line) > MAX_LINE:
        return None
    words = line.decode("ascii", "replace").split()
    if len(words) == 1 and words[0] in OPTIONS.values():
        return words[0], None
    if len(words) == 2 and words[0] == "recall" and words[1] in SLOTS:
        return "recall", int(words[1]) - 1
    return None
