"""Undo and redo for the mix (no Qt).

The history keeps snapshots of the parts of the state a person edits: routing, levels, mutes,
groups, names, colours, icons and which channels are shown. Changes are committed in steps: the
main window commits shortly after the last edit, so one fader drag is one undo step, not one per
pixel. Solo, the selected submix and the device settings (st["hw"]) are not part of it.
"""
import copy

from .model import MIX_KEYS

UNDO_KEYS = MIX_KEYS + ("names", "colors", "icons", "custom_names", "hidden", "hidden_strips",
                        "active_slot")

# what an undo step is called when nothing more specific was given (first changed key wins)
LABELS = (("sends", "Level change"), ("out", "Output level"), ("mute", "Mute"),
          ("stereo", "Stereo link"), ("groups", "Fader group"), ("names", "Rename"),
          ("colors", "Color"), ("icons", "Icon"), ("custom_names", "Channel names"),
          ("hidden", "Show channels"), ("hidden_strips", "Hide channel"),
          ("active_slot", "Preset"))


def snapshot(st):
    return copy.deepcopy({k: st[k] for k in UNDO_KEYS if k in st})


def describe(before, after):
    for key, label in LABELS:
        if before.get(key) != after.get(key):
            return label
    return "Change"


class History:
    def __init__(self, st, limit=100):
        self.st = st
        self.limit = limit
        self.undo_stack = []      # [(snapshot before the step, label)]
        self.redo_stack = []      # [(snapshot after the step, label)]
        self.base = snapshot(st)  # the state as of the last commit

    def commit(self, label=None):
        """Record the edits since the last commit as one step. Returns True if anything changed."""
        now = snapshot(self.st)
        if now == self.base:
            return False
        self.undo_stack.append((self.base, label or describe(self.base, now)))
        del self.undo_stack[:-self.limit]
        self.redo_stack.clear()
        self.base = now
        return True

    def can_undo(self):
        return bool(self.undo_stack) or snapshot(self.st) != self.base

    def can_redo(self):
        return bool(self.redo_stack)

    def undo(self):
        """Go back one step. Returns its label, or None if there is nothing to undo."""
        self.commit()
        if not self.undo_stack:
            return None
        before, label = self.undo_stack.pop()
        self.redo_stack.append((self.base, label))
        self._restore(before)
        return label

    def redo(self):
        self.commit()             # an edit since the last undo ends the redo chain
        if not self.redo_stack:
            return None
        after, label = self.redo_stack.pop()
        self.undo_stack.append((self.base, label))
        self._restore(after)
        return label

    def _restore(self, snap):
        for k, v in snap.items():
            self.st[k] = copy.deepcopy(v)
        self.base = copy.deepcopy(snap)
