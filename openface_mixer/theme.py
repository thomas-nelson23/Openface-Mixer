"""Colours, fonts, the UI scale and the application stylesheet.

Themes: every colour, the fonts and the main sizes come from a theme file (TOML). The default,
themes/deep-ocean.toml, lists every value with what it is for; a user's theme in
~/.config/openface-mixer/themes/ only lists what it changes, the rest comes from the default.
The theme is read once, when this module is first imported (the app restarts to change it), so
the rest of the code can import the values as plain constants. OPENFACE_THEME=<key or file>
overrides the theme picked in the settings, for trying one out.

Scaling: every fixed size in the GUI goes through px(), which multiplies by the UI scale
chosen in the settings panel and rounds to whole pixels. The widgets are laid out larger or
smaller instead of being rasterised and stretched (what QT_SCALE_FACTOR does), so text and
lines stay sharp at every scale."""
import os
import re
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

try:
    import tomllib
except ImportError:             # Python 3.10: the small reader below is enough for themes
    tomllib = None

BUILTIN_DIR = Path(__file__).parent / "themes"
DEFAULT_THEME = "deep-ocean"


def user_dir():
    """Where users keep their own themes (~/.config/openface-mixer/themes)."""
    from .config import CONFIG_DIR
    return CONFIG_DIR / "themes"


def _parse_toml(text):
    """A reader for the part of TOML a theme uses ([table], [table.sub], key = "string" or
    number, comments), where Python has no tomllib."""
    out = table = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"\[\s*([A-Za-z0-9_.\- ]+?)\s*\](\s*#.*)?", line)
        if m:
            table = out
            for part in m.group(1).split("."):
                table = table.setdefault(part.strip(), {})
            continue
        m = re.fullmatch(r"([A-Za-z0-9_\-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[-+0-9.]+)(\s*#.*)?", line)
        if not m:
            raise ValueError(f"line {n}: {raw.strip()}")
        value = m.group(2)
        if value[0] in "\"'":
            value = value[1:-1]
        else:
            value = float(value) if "." in value else int(value)
        table[m.group(1)] = value
    return out


def read_theme_file(path):
    """The tables of a theme file. Raises OSError or ValueError."""
    text = Path(path).read_text(encoding="utf-8")
    if tomllib is not None:
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(str(e)) from None
    return _parse_toml(text)


def theme_files():
    """{key: path} of every theme there is: the ones that come with the app, then the user's
    (a user theme with the same file name as a built-in one is used instead of it)."""
    found = {p.stem: p for p in sorted(BUILTIN_DIR.glob("*.toml"))}
    try:
        found.update((p.stem, p) for p in sorted(user_dir().glob("*.toml")))
    except OSError:
        pass
    return found


def theme_name(path):
    """The name a theme file gives itself, or its file name."""
    try:
        name = read_theme_file(path).get("name")
    except (OSError, ValueError):
        name = None
    return name if isinstance(name, str) and name.strip() else Path(path).stem


def _merge(base, over, where, problems):
    """base with the values of over put in, kept to the keys and kinds base has."""
    out = dict(base)
    for k, v in over.items():
        k = str(k)
        if k not in base:
            if where == "colors.channels" and isinstance(v, str) and QColor(v).isValid():
                out[k] = v      # a theme may offer its own channel colours
            else:
                problems.append(f"unknown setting {where + '.' if where else ''}{k}")
            continue
        b = base[k]
        if isinstance(b, dict):
            if isinstance(v, dict):
                out[k] = _merge(b, v, f"{where}.{k}" if where else k, problems)
            else:
                problems.append(f"{where}.{k} should be a table")
        elif isinstance(b, str) and where.startswith("colors"):
            if isinstance(v, str) and QColor(v).isValid():
                out[k] = v
            else:
                problems.append(f"{where}.{k}: {v!r} is not a colour")
        elif isinstance(b, (int, float)) and not isinstance(v, bool) and \
                isinstance(v, (int, float)) and 0 <= v <= 1000:
            out[k] = v
        elif isinstance(b, str) and isinstance(v, str):
            out[k] = v
        else:
            problems.append(f"{where}.{k}: {v!r} is not a usable value")
    return out


def load_theme(key=None, path=None):
    """(values, problems): the theme's values over the default theme's, and what in it could
    not be used (shown in the settings; a broken theme falls back to the default's values)."""
    default = read_theme_file(BUILTIN_DIR / f"{DEFAULT_THEME}.toml")
    if path is None:
        path = theme_files().get(key or DEFAULT_THEME)
        if path is None:
            return default, [f"theme {key!r} not found"]
    problems = []
    try:
        values = read_theme_file(path)
    except (OSError, ValueError) as e:
        return default, [f"{Path(path).name}: {getattr(e, 'strerror', None) or e}"]
    if isinstance(values.get("colors", {}).get("channels"), dict):
        # a theme that lists channel colours gives the whole menu, in its order
        default = {**default, "colors": {**default["colors"], "channels": {}}}
    return _merge(default, values, "", problems), problems


def chosen_theme():
    """The key of the theme picked in the settings (ui.json), or OPENFACE_THEME: a key or the
    path of a theme file, for trying one out."""
    env = os.environ.get("OPENFACE_THEME")
    if env:
        return env
    from .config import load_ui
    key = load_ui().get("theme")
    return key if isinstance(key, str) and key else DEFAULT_THEME


def rgba(color, alpha):
    """A QSS colour: color drawn alpha (0..1) see-through, as rgba(r,g,b,a)."""
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha:g})"


def alpha(color, a):
    """A QColor of color with alpha a (0..255), for painting."""
    c = QColor(color)
    c.setAlpha(a)
    return c


def _qcolors(table):
    return {k: QColor(v) for k, v in table.items() if isinstance(v, str)}


THEME_KEY = chosen_theme()
_values, THEME_PROBLEMS = (load_theme(path=THEME_KEY) if THEME_KEY.endswith(".toml")
                           or os.sep in THEME_KEY else load_theme(THEME_KEY))
for _p in THEME_PROBLEMS:
    print(f"openface-mixer: theme: {_p}", file=sys.stderr)
THEME_NAME = _values.get("name") or DEFAULT_THEME
_c = _qcolors(_values["colors"])

# the theme's colours (themes/deep-ocean.toml says what each is for)
BG = _c["background"]
PANEL, INK_COLOR, SIDEBAR, FIELD, WELL = (_c[k] for k in ("panel", "ink", "sidebar", "field",
                                                         "well"))
LINE, SHADOW = _c["line"], _c["shadow"]
ACCENT, ALERT, WARN, OK = _c["accent"], _c["alert"], _c["warn"], _c["ok"]
SOLO = WARN                   # solo and cue
TEXT, BRIGHT, SOFT, DIM, FAINT = (_c[k] for k in ("text", "bright", "soft", "dim", "faint"))
MUTED = _c["muted"].name()    # hints and scale marks (a QSS colour string)
ON_ACCENT, ON_LIGHT = _c["on_accent"], _c["on_light"]
BUTTON, BUTTON_HOVER, BUTTON_PRESSED, BUTTON_TEXT, BUTTON_LIT_PRESSED, BUTTON_SELECTED = (
    _c[k] for k in ("button", "button_hover", "button_pressed", "button_text",
                    "button_lit_pressed", "button_selected"))
LABEL, LABEL_SELECTED, POINTER, SCALE_ZERO = (_c[k] for k in ("label", "label_selected",
                                                              "pointer", "scale_zero"))
FADER_CAP_TOP, FADER_CAP_MIDDLE, FADER_CAP_BOTTOM = (
    _c[k] for k in ("fader_cap_top", "fader_cap_middle", "fader_cap_bottom"))
FADER_CAP_DISABLED_TOP = _c["fader_cap_disabled_top"]
FADER_CAP_DISABLED_BOTTOM = _c["fader_cap_disabled_bottom"]
KNOB = _c["knob"]
MATRIX_CELL, MATRIX_CELL_ALT, MATRIX_CELL_HOVER = (
    _c[k] for k in ("matrix_cell", "matrix_cell_alt", "matrix_cell_hover"))
METER = _qcolors(_values["colors"]["meter"])
_rows = _qcolors(_values["colors"]["rows"])
KIND_COLOR = {"in": _rows["input"], "play": _rows["playback"], "out": _rows["output"]}
GROUP_COLOR = {int(k): v for k, v in _qcolors(_values["colors"]["groups"]).items()}
# the channel colour palette offered in the strip menu (TotalMix has a similar set)
CHANNEL_COLORS = tuple((k, v.name()) for k, v in _qcolors(_values["colors"]["channels"]).items())
INK = rgba(INK_COLOR, 0.62)            # strip and panel backgrounds, top to bottom
INK_DEEP = rgba(INK_COLOR, 0.93)
HAIR = rgba(LINE, 0.07)                # hairline edges
HAIR_STRONG = rgba(LINE, 0.16)

_f = _values["fonts"]
SANS, MONO, DISPLAY = _f["ui"], _f["mono"], _f["display"]
MONO_STACK = f"'{MONO}', {_f['mono_fallback']}"
FONT_SIZE = _f["size"]
SIZES = dict(_values["sizes"])         # pixels at 100%: pass them through px()


# channel icons: (menu section, ((file name in icons/, menu label), ...)), in menu order
CHANNEL_ICONS = (
    ("Instruments", (
        ("guitar", "Guitar"), ("bass", "Bass"), ("upright-bass", "Upright bass"),
        ("ukulele", "Ukulele"), ("violin", "Violin"), ("harp", "Harp"), ("piano", "Keyboard"),
        ("keyboard-music", "Synth"), ("accordion", "Accordion"), ("drum", "Drums"),
        ("bongos", "Percussion"), ("trumpet", "Trumpet"), ("saxophone", "Sax"),
        ("flute", "Flute"), ("harmonica", "Harmonica"), ("mic-vocal", "Vocal"),
        ("mic", "Microphone"), ("music", "Music"),
    )),
    ("Studio", (
        ("circle-dot", "Record"), ("sliders-horizontal", "DAW"), ("amp", "Amp"),
        ("monitor-speaker", "Monitors"), ("headphones", "Headphones"),
        ("speaker", "Speakers"), ("volume-2", "Level"), ("megaphone", "Talkback"),
        ("metronome", "Click"), ("microphone-2", "Podcast"), ("broadcast", "Stream"),
        ("webcam", "Webcam"), ("vinyl", "Turntable"), ("cassette-tape", "Tape"),
        ("audio-waveform", "Waveform"), ("audio-lines", "Audio"), ("cable", "Line"),
        ("usb", "USB"), ("zap", "FX"),
    )),
    ("Devices", (
        ("laptop", "Computer"), ("monitor", "Desktop"), ("phone", "Phone"), ("tv", "TV"),
        ("radio", "Radio"), ("video", "Video"), ("film", "Film"), ("gamepad-2", "Game"),
        ("disc-3", "Disc"), ("star", "Star"),
    )),
)
ICON_DIR = Path(__file__).parent / "icons"

SCALE = 1.0


def set_scale(scale):
    global SCALE
    SCALE = float(scale)


def px(n):
    """n logical pixels at 100%, scaled and rounded to whole pixels."""
    return max(1, int(round(n * SCALE))) if n else 0


def caption_html(text, color=None):
    """Wide-tracked small caps label, as the dashboard's panel titles."""
    return (f"<span style='color:{(color or DIM).name()};font-size:{px(9)}px;font-weight:600;"
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
        p.setPen(ACCENT)
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
            if box.objectName() == "ports":
                lay.setVerticalSpacing(px(2))
            else:
                lay.setVerticalSpacing(px(4))
                lay.setColumnMinimumWidth(0, px(96))   # values line up across the sections
        else:
            lay.setSpacing(px(4))


def apply_theme(app, scale=1.0):
    set_scale(scale)
    load_fonts()
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in [
        (QPalette.Window, BG), (QPalette.WindowText, TEXT),
        (QPalette.Base, FIELD), (QPalette.AlternateBase, PANEL),
        (QPalette.Text, TEXT), (QPalette.Button, PANEL),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT),
        (QPalette.HighlightedText, ON_ACCENT), (QPalette.ToolTipBase, PANEL),
        (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, DIM),
    ]:
        pal.setColor(role, col)
    app.setPalette(pal)
    app.setFont(font(SANS, FONT_SIZE))
    app.setStyleSheet(stylesheet())


def stylesheet():
    c, r, t, d, s = ACCENT.name(), ALERT.name(), TEXT.name(), DIM.name(), SOFT.name()
    w, k, ink, line, acc = BRIGHT.name(), ON_LIGHT.name(), INK_COLOR, LINE, ACCENT
    pill, panel, fld, sm = (px(SIZES[n]) for n in ("pill_radius", "panel_radius",
                                                    "field_radius", "small_radius"))
    p = px
    return f"""
        QMainWindow, QStackedWidget > QWidget {{ background:{BG.name()}; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; }}
        QFrame#edge {{ background:qlineargradient(x1:0,x2:1,stop:0 transparent,stop:0.25 {c},
                      stop:0.75 {r},stop:1 transparent); max-height:1px; min-height:1px; }}
        QToolTip {{ background:{PANEL.name()}; color:{t}; border:1px solid {HAIR_STRONG};
                   padding:{p(5)}px {p(8)}px; border-radius:{fld}px; }}
        QMenu {{ background:{rgba(ink, 0.97)}; border:1px solid {HAIR_STRONG};
                border-radius:{panel}px; padding:{p(6)}px; }}
        QMenu::item {{ padding:{p(5)}px {p(18)}px; border-radius:{p(4)}px; color:{s}; }}
        QMenu::item:selected {{ background:{rgba(acc, 0.12)}; color:{w}; }}
        QMenu::item:disabled {{ background:transparent; color:{MUTED}; }}
        QMenu::separator {{ height:1px; background:{HAIR}; margin:{p(4)}px {p(6)}px; }}
        QMenu::icon {{ margin-left:{p(6)}px; }}
        QPushButton {{ background:transparent; color:{t}; border:1px solid {HAIR_STRONG};
                      border-radius:{pill}px; padding:{p(3)}px {p(12)}px; }}
        QPushButton:hover {{ border-color:{rgba(acc, 0.5)}; color:{w}; }}
        QPushButton:pressed {{ background:{rgba(acc, 0.08)}; }}
        /* keyboard focus (buttons only take focus from Tab, see MainWindow.tab_focus_only) */
        QPushButton:focus, QToolButton:focus {{ border-color:{rgba(acc, 0.8)}; }}
        QPushButton:disabled {{ color:{MUTED}; border-color:{HAIR}; }}
        QPushButton:checked {{ background:{w}; color:{k}; border-color:{w}; font-weight:600; }}
        QPushButton:checked:hover {{ background:{c}; border-color:{c}; color:{k}; }}
        QPushButton::menu-indicator {{ width:0; }}
        /* the top bar: every control 11 px and the same height, quiet selected states */
        QWidget#topbar QPushButton, QWidget#topbar QComboBox {{ font-size:{p(11)}px; }}
        QWidget#topbar QComboBox {{ padding:{p(3)}px {p(8)}px; border-radius:{pill}px;
                                   background:{rgba(ink, 0.6)}; }}
        QWidget#topbar QComboBox::drop-down {{ width:{p(16)}px; }}
        QPushButton#gear {{ padding:{p(3)}px {p(6)}px; }}
        QPushButton#gear:checked {{ background:{rgba(acc, 0.12)}; border-color:{rgba(acc, 0.45)}; }}
        QPushButton#gear:checked:hover {{ background:{rgba(acc, 0.2)}; }}
        QWidget#segment {{ background:{rgba(ink, 0.6)}; border:1px solid {HAIR};
                          border-radius:{pill}px; }}
        QPushButton#segbtn {{ background:transparent; border:none; border-radius:{p(SIZES['pill_radius'] - 2)}px;
                             padding:{p(2)}px {p(14)}px; color:{d}; font-weight:500; }}
        QPushButton#segbtn:hover {{ color:{w}; }}
        QPushButton#segbtn:checked {{ background:{rgba(line, 0.09)}; color:{w};
                                     font-weight:500; }}
        QPushButton#segbtn:checked:hover {{ background:{rgba(line, 0.12)}; color:{w}; }}
        QPushButton#segbtn:pressed {{ background:{rgba(line, 0.14)}; }}
        QPushButton#slot {{ background:transparent; border:1px solid {HAIR}; border-radius:{pill}px;
                           padding:0; color:{MUTED}; font-family:{MONO_STACK}; font-size:{p(9)}px;
                           min-width:{p(22)}px; max-width:{p(22)}px; min-height:{p(22)}px;
                           max-height:{p(22)}px; }}
        QPushButton#slot[filled="true"] {{ color:{t}; border-color:{HAIR_STRONG}; }}
        QPushButton#slot[active="true"] {{ color:{r}; border-color:{r}; font-weight:700; }}
        QPushButton#slot:hover {{ border-color:{c}; }}
        QPushButton#slot:pressed {{ background:{rgba(acc, 0.16)}; }}
        QPushButton#store:checked {{ background:{r}; border-color:{r}; color:{w}; }}
        QLabel#lcd {{ background:{rgba(ink, 0.6)}; color:{s}; border:1px solid {HAIR};
                     border-radius:{pill}px; padding:{p(4)}px {p(14)}px; font-family:{MONO_STACK};
                     font-size:{p(10)}px; }}
        QLabel#caption {{ color:{d}; font-size:{p(9)}px; font-weight:600; }}
        QLabel#toast {{ background:{rgba(PANEL, 0.96)}; color:{t}; border:1px solid {HAIR_STRONG};
                       border-radius:{p(14)}px; padding:{p(6)}px {p(16)}px; font-size:{p(12)}px; }}
        QLabel#empty {{ color:{d}; font-size:{p(12)}px; }}
        QLabel#key {{ color:{d}; }}
        QLabel#hint {{ color:{MUTED}; font-size:{p(10)}px; }}
        QLabel#note {{ color:{s}; background:{rgba(ALERT, 0.07)}; border-left:{p(2)}px solid {r};
                      border-radius:{sm}px; padding:{p(6)}px {p(8)}px; }}
        QComboBox {{ background:{rgba(line, 0.035)}; border:1px solid {HAIR};
                    border-radius:{fld}px; padding:{p(3)}px {p(8)}px; color:{t}; }}
        QComboBox:hover, QComboBox:focus {{ border-color:{rgba(acc, 0.5)}; }}
        QComboBox:disabled {{ color:{MUTED}; }}
        QComboBox::drop-down {{ border:none; width:{p(18)}px; }}
        QComboBox QAbstractItemView {{ background:{PANEL.name()}; border:1px solid {HAIR_STRONG};
                                      selection-background-color:{rgba(acc, 0.15)};
                                      selection-color:{w}; outline:0; padding:{p(4)}px; }}
        QFrame#sidebar {{ background:{SIDEBAR.name()}; border-left:1px solid {HAIR_STRONG}; }}
        QToolButton#sidebarclose {{ background:transparent; border:none; color:{d};
                                   font-size:{p(16)}px; padding:0; border-radius:{p(12)}px;
                                   min-width:{p(24)}px; min-height:{p(24)}px; }}
        QToolButton#sidebarclose:pressed {{ background:{rgba(line, 0.08)}; }}
        QToolButton#sidebarclose:hover {{ color:{w}; }}
        QGroupBox {{ background:transparent; border:none; border-top:1px solid {HAIR};
                    border-radius:0; margin-top:0; padding-top:{p(28)}px; }}
        QGroupBox::title {{ height:0; }}
        QFrame#sidebar QLabel {{ font-size:{p(11)}px; }}
        QFrame#sidebar QComboBox, QFrame#sidebar QCheckBox, QFrame#sidebar QPushButton,
        QFrame#sidebar QDoubleSpinBox {{ font-size:{p(11)}px; }}
        QFrame#sidebar QDoubleSpinBox {{ padding:{p(1)}px 0; }}
        QFrame#sidebar QDoubleSpinBox::up-button, QFrame#sidebar QDoubleSpinBox::down-button {{
            width:0; border:none; }}
        QFrame#sidebar QLabel#head {{ color:{w}; }}
        QGroupBox QPushButton {{ padding:{p(3)}px {p(10)}px; border-radius:{fld}px; }}
        QGroupBox QPushButton:checked {{ background:{rgba(acc, 0.16)}; border-color:{c};
                                         color:{c}; font-weight:500; }}
        QGroupBox QPushButton:checked:hover {{ background:{rgba(acc, 0.26)}; color:{w}; }}
        QGroupBox QComboBox {{ padding:{p(2)}px {p(2)}px {p(2)}px {p(7)}px; }}
        QGroupBox QComboBox::drop-down {{ width:{p(12)}px; }}
        QGroupBox#ports QLabel, QGroupBox#ports QComboBox {{ font-size:{p(10)}px; }}
        QGroupBox#ports QComboBox {{ padding:{p(1)}px {p(1)}px {p(1)}px {p(6)}px; }}
        QCheckBox {{ spacing:{p(6)}px; }}
        QCheckBox::indicator {{ width:{p(12)}px; height:{p(12)}px; border:1px solid {HAIR_STRONG};
                               border-radius:{sm}px; background:{rgba(line, 0.035)}; }}
        QCheckBox::indicator:hover {{ border-color:{rgba(acc, 0.5)}; }}
        QCheckBox::indicator:checked {{ background:{c}; border-color:{c}; }}
        QCheckBox::indicator:disabled {{ border-color:{HAIR}; background:transparent; }}
        QDoubleSpinBox, QLineEdit {{ background:{rgba(line, 0.035)}; border:1px solid {HAIR};
                      border-radius:{fld}px; padding:{p(2)}px {p(6)}px; color:{t}; }}
        QDoubleSpinBox:focus, QLineEdit:focus {{ border-color:{rgba(acc, 0.5)}; }}
        QScrollBar:horizontal {{ background:transparent; height:{p(6)}px; margin:{p(2)}px 0 0 0; }}
        QScrollBar::handle:horizontal {{ background:{rgba(acc, 0.25)}; border-radius:{p(3)}px;
                                        min-width:{p(40)}px; }}
        QScrollBar::handle:horizontal:hover {{ background:{c}; }}
        QScrollBar:vertical {{ background:transparent; width:{p(6)}px; }}
        QScrollBar::handle:vertical {{ background:{rgba(acc, 0.25)}; border-radius:{p(3)}px;
                                      min-height:{p(40)}px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background:none; }}
    """
