"""Colours, fonts and the application stylesheet: the "Deep Ocean" look
(a deep ocean-blue field, near-black panels with hairline edges, cyan for signal, red for small
accents, Sora / JetBrains Mono / Michroma)."""
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
KIND_COLOR = {"in": QColor("#2ad4ff"), "play": QColor("#a77bff"), "out": QColor("#ff2a4f")}
GROUP_COLOR = {1: QColor("#ff2a4f"), 2: QColor("#ffb020"), 3: QColor("#2ad4ff"), 4: QColor("#ff7ad9")}
SANS = "Sora"
MONO = "JetBrains Mono, Noto Sans Mono, DejaVu Sans Mono, monospace"
DISPLAY = "Michroma"


def caption_html(text, color=None):
    """Wide-tracked small caps label, as the dashboard's panel titles."""
    return (f"<span style='color:{(color or DIM).name()};font-size:8px;font-weight:600;"
            f"letter-spacing:2.4px'>{text.upper()}</span>")


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
        lay.setContentsMargins(10, 2, 10, 8)
        if isinstance(lay, QGridLayout):
            lay.setHorizontalSpacing(8)
            lay.setVerticalSpacing(4)
        else:
            lay.setSpacing(4)


def apply_theme(app):
    load_fonts()
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in [
        (QPalette.Window, BG), (QPalette.WindowText, TEXT),
        (QPalette.Base, QColor("#030a10")), (QPalette.AlternateBase, PANEL),
        (QPalette.Text, TEXT), (QPalette.Button, PANEL),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, CYAN),
        (QPalette.HighlightedText, QColor("#021012")), (QPalette.ToolTipBase, PANEL),
        (QPalette.ToolTipText, TEXT),
    ]:
        pal.setColor(role, col)
    app.setPalette(pal)
    f = QFont(SANS)
    f.setPointSize(9)
    app.setFont(f)
    c, r, t, d, s = CYAN.name(), RED.name(), TEXT.name(), DIM.name(), SOFT.name()
    app.setStyleSheet(f"""
        QMainWindow, QStackedWidget > QWidget {{ background:{BG.name()}; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; }}
        QFrame#edge {{ background:qlineargradient(x1:0,x2:1,stop:0 transparent,stop:0.25 {c},
                      stop:0.75 {r},stop:1 transparent); max-height:1px; min-height:1px; }}
        QToolTip {{ background:#040e16; color:{t}; border:1px solid {HAIR_STRONG}; padding:5px 8px;
                   border-radius:6px; }}
        QMenu {{ background:rgba(4,12,18,0.97); border:1px solid {HAIR_STRONG}; border-radius:8px;
                padding:6px; }}
        QMenu::item {{ padding:5px 18px; border-radius:4px; color:{s}; }}
        QMenu::item:selected {{ background:rgba(42,212,255,0.12); color:white; }}
        QMenu::separator {{ height:1px; background:{HAIR}; margin:4px 6px; }}
        QPushButton {{ background:transparent; color:{t}; border:1px solid {HAIR_STRONG};
                      border-radius:12px; padding:4px 14px; }}
        QPushButton:hover {{ border-color:rgba(42,212,255,0.5); color:white; }}
        QPushButton:pressed {{ background:rgba(42,212,255,0.08); }}
        QPushButton:checked {{ background:white; color:#0b0f1c; border-color:white; font-weight:600; }}
        QPushButton:checked:hover {{ background:{c}; border-color:{c}; color:#0b0f1c; }}
        QPushButton::menu-indicator {{ width:0; }}
        QWidget#segment {{ background:rgba(4,12,18,0.7); border:1px solid {HAIR}; border-radius:13px; }}
        QPushButton#segbtn {{ background:transparent; border:none; border-radius:10px;
                             padding:3px 16px; color:{d}; font-weight:500; }}
        QPushButton#segbtn:hover {{ color:white; }}
        QPushButton#segbtn:checked {{ background:white; color:#0b0f1c; font-weight:600; }}
        QPushButton#slot {{ background:transparent; border:1px solid {HAIR}; border-radius:11px;
                           padding:0; color:#3c5560; font-family:{MONO}; font-size:9px;
                           min-width:22px; max-width:22px; min-height:22px; max-height:22px; }}
        QPushButton#slot[filled="true"] {{ color:{t}; border-color:{HAIR_STRONG}; }}
        QPushButton#slot[active="true"] {{ color:{r}; border-color:{r}; font-weight:700; }}
        QPushButton#slot:hover {{ border-color:{c}; }}
        QPushButton#store:checked {{ background:{r}; border-color:{r}; color:white; }}
        QPushButton#solomaster {{ color:#3c5560; font-weight:700; padding:4px 12px;
                                 letter-spacing:2px; }}
        QPushButton#solomaster[armed="true"] {{ color:{SOLO.name()}; border-color:{SOLO.name()}; }}
        QPushButton#solomaster[active="true"] {{ background:{SOLO.name()}; color:#0b0f1c; }}
        QPushButton#namesbtn {{ color:#3c5560; font-weight:700; padding:4px 12px;
                               letter-spacing:2px; }}
        QPushButton#namesbtn:checked {{ background:white; color:#0b0f1c; border-color:white; }}
        QLabel#lcd {{ background:rgba(4,12,18,0.6); color:{s}; border:1px solid {HAIR};
                     border-radius:13px; padding:5px 16px; font-family:{MONO}; font-size:10px; }}
        QLabel#caption {{ color:{d}; font-size:8px; font-weight:600; }}
        QComboBox {{ background:#030a10; border:1px solid {HAIR}; border-radius:6px; padding:2px 8px;
                    color:{t}; }}
        QComboBox:hover, QComboBox:focus {{ border-color:{c}; }}
        QComboBox::drop-down {{ border:none; width:18px; }}
        QComboBox QAbstractItemView {{ background:#040e16; border:1px solid {HAIR_STRONG};
                                      selection-background-color:rgba(42,212,255,0.15);
                                      selection-color:white; outline:0; }}
        QGroupBox {{ background:qlineargradient(y1:0,y2:1,stop:0 {INK},stop:0.7 {INK_DEEP});
                    border:1px solid {HAIR}; border-radius:8px;
                    margin-top:0; padding-top:20px; }}
        QGroupBox::title {{ subcontrol-origin:padding; subcontrol-position:top left; left:10px;
                           top:7px; color:{d}; font-size:8px; font-weight:600; }}
        QGroupBox QPushButton {{ padding:3px 6px; }}
        QGroupBox QComboBox {{ padding:2px 4px 2px 8px; }}
        QGroupBox QComboBox::drop-down {{ width:12px; }}
        QCheckBox::indicator {{ width:13px; height:13px; border:1px solid {HAIR_STRONG};
                               border-radius:7px; background:transparent; }}
        QCheckBox::indicator:checked {{ background:{c}; border-color:{c}; }}
        QScrollBar:horizontal {{ background:transparent; height:6px; margin:2px 0 0 0; }}
        QScrollBar::handle:horizontal {{ background:rgba(42,212,255,0.25); border-radius:3px;
                                        min-width:40px; }}
        QScrollBar::handle:horizontal:hover {{ background:{c}; }}
        QScrollBar:vertical {{ background:transparent; width:6px; }}
        QScrollBar::handle:vertical {{ background:rgba(42,212,255,0.25); border-radius:3px;
                                      min-height:40px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background:none; }}
    """)
