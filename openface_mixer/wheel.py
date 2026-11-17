"""Mouse wheel and touchpad scrolling over a control (fader, knob, matrix cell): one step per
wheel notch, however the movement arrives. No Qt imports: the event is only asked for its deltas.
"""

NOTCH = 120        # angleDelta units for one notch of a mouse wheel
PIXEL_UNITS = 2    # for devices that only report pixels: 60 px a notch, as Qt converts on macOS


def take_steps(rest, delta):
    """Add delta (angleDelta units) to rest, what is left of earlier small movements. Returns
    (whole notches, new rest). Turning back drops the rest."""
    if rest * delta < 0:
        rest = 0
    rest += delta
    steps = int(rest / NOTCH)      # towards zero
    return steps, rest - steps * NOTCH


class Wheel:
    """One control's wheel: steps(event) gives the whole notches to move by, up positive."""

    def __init__(self):
        self.rest = 0

    def steps(self, e):
        """A wheel event that is not mostly vertical (sideways scrolling, a tilt wheel) is
        ignored, so the row or grid around the control scrolls, and gives 0. The direction is
        taken as the system delivers it, like a scroll area does."""
        d = e.angleDelta()
        dx, dy = d.x(), d.y()
        if not (dx or dy):
            d = e.pixelDelta()
            dx, dy = d.x() * PIXEL_UNITS, d.y() * PIXEL_UNITS
        if abs(dy) <= abs(dx):
            e.ignore()
            return 0
        e.accept()
        steps, self.rest = take_steps(self.rest, dy)
        return steps
