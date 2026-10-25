"""Colours, fonts, the UI scale and the application stylesheet: the "Deep Ocean" look
(a deep ocean-blue field, near-black panels with hairline edges, cyan for signal, red for small
accents, Sora / JetBrains Mono / Michroma).

Scaling: every fixed size in the GUI goes through px(), which multiplies by the UI scale
chosen in the settings panel and rounds to whole pixels. The widgets are laid out larger or
smaller instead of being rasterised and stretched (what QT_SCALE_FACTOR does), so text and
lines stay sharp at every scale."""
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

BG = QColor("#021d2e")       # the blue field
PANEL = QColor("#040e16")    # ink panels and strips
INK = "rgba(4,12,18,0.62)"
INK_DEEP = "rgba(4,12,18,0.93)"
HAIR = "rgba(255,255,255,0.07)"
HAIR_STRONG = "rgba(255,255,255,0.16)"
CYAN = QColor("#2ad4ff")
RED = QColor("#ff2a4f")
TEAL = QColor("#0090b0")
WARN = QColor("#ffb020")
ACCENT = CYAN                # selection / submix target
SOLO = WARN
TEXT = QColor("#d9eef2")
SOFT = QColor("#9cc7cf")
DIM = QColor("#7d9299")
MUTED = "#58707a"            # hints and scale marks
KIND_COLOR = {"in": QColor("#2ad4ff"), "play": QColor("#a77bff"), "out": QColor("#ff2a4f")}
GROUP_COLOR = {1: QColor("#ff2a4f"), 2: QColor("#ffb020"), 3: QColor("#2ad4ff"), 4: QColor("#ff7ad9")}
# the channel colour palette offered in the strip menu (TotalMix has a similar set)
CHANNEL_COLORS = (
    ("Red", "#ff2a4f"), ("Orange", "#ff7a2a"), ("Amber", "#ffb020"), ("Lime", "#b8e03a"),
    ("Green", "#56e39f"), ("Teal", "#2ad4ff"), ("Blue", "#4d8dff"), ("Violet", "#a77bff"),
    ("Pink", "#ff7ad9"), ("Grey", "#9cb0b8"),
)
# channel icons: (file name in icons/, menu label), in menu order
CHANNEL_ICONS = (
    ("guitar", "Guitar"), ("bass", "Bass"), ("drum", "Drums"), ("mic-vocal", "Vocal"),
    ("mic", "Microphone"), ("piano", "Keyboard"), ("keyboard-music", "Synth"),
    ("audio-waveform", "Waveform"), ("laptop", "Computer"), ("monitor", "Desktop"),
    ("headphones", "Headphones"), ("speaker", "Speakers"), ("volume-2", "Monitor"),
    ("megaphone", "Talkback"), ("radio", "Radio"), ("phone", "Phone"), ("tv", "TV"),
    ("video", "Video"), ("film", "Film"), ("gamepad-2", "Game"), ("disc-3", "Disc"),
    ("audio-lines", "Audio"), ("cable", "Line"), ("usb", "USB"), ("zap", "FX"), ("star", "Star"),
)
ICON_DIR = Path(__file__).parent / "icons"
SANS = "Sora"
MONO = "JetBrains Mono"
MONO_STACK = "'JetBrains Mono', 'Noto Sans Mono', 'DejaVu Sans Mono', monospace"
DISPLAY = "Michroma"

SCALE = 1.0


def set_scale(scale):
    global SCALE
    SCALE = float(scale)


def px(n):
    """n logical pixels at 100%, scaled and rounded to whole pixels."""
    return max(1, int(round(n * SCALE))) if n else 0


def caption_html(text, color=None):
    """Wide-tracked small caps label, as the dashboard's panel titles."""
    return (f"<span style='color:{(color or DIM).name()};font-size:{px(8)}px;font-weight:600;"
            f"letter-spacing:{px(2.4)}px'>{text.upper()}</span>")


def font(family=SANS, size=12, weight=QFont.Normal):
    """A font in scaled pixels with light hinting: outlines stay true to the design instead of
    being snapped to the pixel grid, which made the small labels look jagged."""
    f = QFont(family)
    f.setPixelSize(px(size))
    f.setWeight(weight)
    f.setHintingPreference(QFont.PreferVerticalHinting)
    f.setStyleStrategy(QFont.PreferAntialias)
    return f


def key_label(text):
    """A dim 'key' label for a settings row, the value beside it in the normal text colour."""
    from PySide6.QtWidgets import QLabel
    lbl = QLabel(text)
    lbl.setObjectName("key")
    return lbl


from PySide6.QtWidgets import QGroupBox  # noqa: E402


class Section(QGroupBox):
    """A sidebar section: a box whose title is painted as a wide-tracked cyan caption, so it
    reads as a heading over the section's grey labels (QSS can't track letters)."""

    def __init__(self, title="", parent=None):
        super().__init__("", parent)
        self._title = title.upper()

    def title_text(self):
        return self._title

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._title:
            return
        from PySide6.QtGui import QPainter
        from PySide6.QtCore import QRectF, Qt
        p = QPainter(self)
        f = font(SANS, 9, QFont.Bold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, px(2.2))
        p.setFont(f)
        p.setPen(CYAN)
        p.drawText(QRectF(px(14), px(9), self.width() - px(28), px(14)),
                   Qt.AlignLeft | Qt.AlignVCenter, self._title)


_icon_cache = {}


def icon_pixmap(name, color, size):
    """The channel icon `name` (see CHANNEL_ICONS) drawn in `color` at size x size pixels, or
    None if it is unknown or SVG support is missing (python3-pyside6.qtsvg on Debian)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter, QPixmap
    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return None
    color = QColor(color).name()
    key = (name, color, size)
    if key in _icon_cache:
        return _icon_cache[key]
    try:
        svg = (ICON_DIR / f"{name}.svg").read_text()
    except OSError:
        return None
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(svg.replace("currentColor", color).encode()).render(p)
    p.end()
    _icon_cache[key] = pm
    return pm


FONT_DIR = Path(__file__).parent / "fonts"


def load_fonts():
    """Registers the bundled fonts (SIL Open Font License, see fonts/OFL-*.txt)."""
    for f in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))


def compact_boxes(panel):
    """Tight margins in the settings panel's boxes (titles sit inside them, see QGroupBox)."""
    from PySide6.QtWidgets import QGridLayout, QGroupBox
    for box in panel.findChildren(QGroupBox):
        lay = box.layout()
        if lay is None:
            continue
        lay.setContentsMargins(px(14), px(2), px(14), px(14))
        if isinstance(lay, QGridLayout):
            lay.setHorizontalSpacing(px(6))
            lay.setVerticalSpacing(px(4))
        else:
            lay.setSpacing(px(4))


def apply_theme(app, scale=1.0):
    set_scale(scale)
    load_fonts()
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in [
        (QPalette.Window, BG), (QPalette.WindowText, TEXT),
        (QPalette.Base, QColor("#030a10")), (QPalette.AlternateBase, PANEL),
        (QPalette.Text, TEXT), (QPalette.Button, PANEL),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, CYAN),
        (QPalette.HighlightedText, QColor("#021012")), (QPalette.ToolTipBase, PANEL),
        (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, DIM),
    ]:
        pal.setColor(role, col)
    app.setPalette(pal)
    app.setFont(font(SANS, 12))
    app.setStyleSheet(stylesheet())


def stylesheet():
    c, r, t, d, s = CYAN.name(), RED.name(), TEXT.name(), DIM.name(), SOFT.name()
    p = px
    return f"""
        QMainWindow, QStackedWidget > QWidget {{ background:{BG.name()}; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; }}
        QFrame#edge {{ background:qlineargradient(x1:0,x2:1,stop:0 transparent,stop:0.25 {c},
                      stop:0.75 {r},stop:1 transparent); max-height:1px; min-height:1px; }}
        QToolTip {{ background:#040e16; color:{t}; border:1px solid {HAIR_STRONG};
                   padding:{p(5)}px {p(8)}px; border-radius:{p(6)}px; }}
        QMenu {{ background:rgba(4,12,18,0.97); border:1px solid {HAIR_STRONG};
                border-radius:{p(8)}px; padding:{p(6)}px; }}
        QMenu::item {{ padding:{p(5)}px {p(18)}px; border-radius:{p(4)}px; color:{s}; }}
        QMenu::item:selected {{ background:rgba(42,212,255,0.12); color:white; }}
        QMenu::separator {{ height:1px; background:{HAIR}; margin:{p(4)}px {p(6)}px; }}
        QMenu::icon {{ margin-left:{p(6)}px; }}
        QPushButton {{ background:transparent; color:{t}; border:1px solid {HAIR_STRONG};
                      border-radius:{p(12)}px; padding:{p(4)}px {p(14)}px; }}
        QPushButton:hover {{ border-color:rgba(42,212,255,0.5); color:white; }}
        QPushButton:pressed {{ background:rgba(42,212,255,0.08); }}
        QPushButton:disabled {{ color:{MUTED}; border-color:{HAIR}; }}
        QPushButton:checked {{ background:white; color:#0b0f1c; border-color:white; font-weight:600; }}
        QPushButton:checked:hover {{ background:{c}; border-color:{c}; color:#0b0f1c; }}
        QPushButton::menu-indicator {{ width:0; }}
        QWidget#segment {{ background:rgba(4,12,18,0.7); border:1px solid {HAIR};
                          border-radius:{p(13)}px; }}
        QPushButton#segbtn {{ background:transparent; border:none; border-radius:{p(10)}px;
                             padding:{p(3)}px {p(16)}px; color:{d}; font-weight:500; }}
        QPushButton#segbtn:hover {{ color:white; }}
        QPushButton#segbtn:checked {{ background:white; color:#0b0f1c; font-weight:600; }}
        QPushButton#slot {{ background:transparent; border:1px solid {HAIR}; border-radius:{p(11)}px;
                           padding:0; color:#3c5560; font-family:{MONO_STACK}; font-size:{p(9)}px;
                           min-width:{p(22)}px; max-width:{p(22)}px; min-height:{p(22)}px;
                           max-height:{p(22)}px; }}
        QPushButton#slot[filled="true"] {{ color:{t}; border-color:{HAIR_STRONG}; }}
        QPushButton#slot[active="true"] {{ color:{r}; border-color:{r}; font-weight:700; }}
        QPushButton#slot:hover {{ border-color:{c}; }}
        QPushButton#store:checked {{ background:{r}; border-color:{r}; color:white; }}
        QPushButton#solomaster, QPushButton#namesbtn {{ color:#3c5560; font-weight:700;
                                 padding:{p(4)}px {p(12)}px; letter-spacing:{p(2)}px; }}
        QPushButton#solomaster[armed="true"] {{ color:{SOLO.name()}; border-color:{SOLO.name()}; }}
        QPushButton#solomaster[active="true"] {{ background:{SOLO.name()}; color:#0b0f1c; }}
        QPushButton#namesbtn:checked {{ background:white; color:#0b0f1c; border-color:white; }}
        QLabel#lcd {{ background:rgba(4,12,18,0.6); color:{s}; border:1px solid {HAIR};
                     border-radius:{p(13)}px; padding:{p(5)}px {p(16)}px; font-family:{MONO_STACK};
                     font-size:{p(10)}px; }}
        QLabel#caption {{ color:{d}; font-size:{p(8)}px; font-weight:600; }}
        QLabel#key {{ color:{d}; }}
        QLabel#hint {{ color:{MUTED}; font-size:{p(10)}px; }}
        QComboBox {{ background:rgba(255,255,255,0.035); border:1px solid {HAIR};
                    border-radius:{p(6)}px; padding:{p(3)}px {p(8)}px; color:{t}; }}
        QComboBox:hover, QComboBox:focus {{ border-color:rgba(42,212,255,0.5); }}
        QComboBox:disabled {{ color:{MUTED}; }}
        QComboBox::drop-down {{ border:none; width:{p(18)}px; }}
        QComboBox QAbstractItemView {{ background:#040e16; border:1px solid {HAIR_STRONG};
                                      selection-background-color:rgba(42,212,255,0.15);
                                      selection-color:white; outline:0; padding:{p(4)}px; }}
        QFrame#sidebar {{ background:#040c13; border-left:1px solid {HAIR_STRONG}; }}
        QToolButton#sidebarclose {{ background:transparent; border:none; color:{MUTED};
                                   font-size:{p(16)}px; padding:0 {p(6)}px; }}
        QToolButton#sidebarclose:hover {{ color:white; }}
        QGroupBox {{ background:transparent; border:none; border-top:1px solid {HAIR};
                    border-radius:0; margin-top:0; padding-top:{p(28)}px; }}
        QGroupBox::title {{ height:0; }}
        QFrame#sidebar QLabel {{ font-size:{p(11)}px; }}
        QFrame#sidebar QComboBox, QFrame#sidebar QCheckBox, QFrame#sidebar QPushButton {{
            font-size:{p(11)}px; }}
        QFrame#sidebar QLabel#head {{ color:white; }}
        QGroupBox QPushButton {{ padding:{p(3)}px {p(10)}px; border-radius:{p(6)}px; }}
        QGroupBox QPushButton:checked {{ background:{c}; border-color:{c}; color:#021012; }}
        QGroupBox QComboBox {{ padding:{p(2)}px {p(2)}px {p(2)}px {p(7)}px; }}
        QGroupBox QComboBox::drop-down {{ width:{p(12)}px; }}
        QCheckBox {{ spacing:{p(6)}px; }}
        QCheckBox::indicator {{ width:{p(12)}px; height:{p(12)}px; border:1px solid {HAIR_STRONG};
                               border-radius:{p(3)}px; background:rgba(255,255,255,0.035); }}
        QCheckBox::indicator:hover {{ border-color:rgba(42,212,255,0.5); }}
        QCheckBox::indicator:checked {{ background:{c}; border-color:{c}; }}
        QCheckBox::indicator:disabled {{ border-color:{HAIR}; background:transparent; }}
        QDoubleSpinBox, QLineEdit {{ background:rgba(255,255,255,0.035); border:1px solid {HAIR};
                      border-radius:{p(6)}px; padding:{p(2)}px {p(6)}px; color:{t}; }}
        QDoubleSpinBox:focus, QLineEdit:focus {{ border-color:rgba(42,212,255,0.5); }}
        QScrollBar:horizontal {{ background:transparent; height:{p(6)}px; margin:{p(2)}px 0 0 0; }}
        QScrollBar::handle:horizontal {{ background:rgba(42,212,255,0.25); border-radius:{p(3)}px;
                                        min-width:{p(40)}px; }}
        QScrollBar::handle:horizontal:hover {{ background:{c}; }}
        QScrollBar:vertical {{ background:transparent; width:{p(6)}px; }}
        QScrollBar::handle:vertical {{ background:rgba(42,212,255,0.25); border-radius:{p(3)}px;
                                      min-height:{p(40)}px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background:none; }}
    """
