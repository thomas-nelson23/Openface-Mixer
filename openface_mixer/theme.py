"""Colours, fonts and the application stylesheet (dark, loosely modelled on Logic Pro's mixer)."""
from PySide6.QtGui import QColor, QFont, QPalette

BG = QColor("#1e1e20")
PANEL = QColor("#2c2c2f")
ACCENT = QColor("#f0a43a")   # selection / submix target
SOLO = QColor("#ffd60a")
TEXT = QColor("#ececef")
DIM = QColor("#8e8e93")
KIND_COLOR = {"in": QColor("#3fae5a"), "play": QColor("#3a86d6"), "out": QColor("#c8873a")}
GROUP_COLOR = {1: QColor("#ff5f57"), 2: QColor("#ffd60a"), 3: QColor("#64d2ff"), 4: QColor("#bf5af2")}
MONO = "Noto Sans Mono, DejaVu Sans Mono, monospace"


def apply_theme(app):
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in [
        (QPalette.Window, BG), (QPalette.WindowText, TEXT),
        (QPalette.Base, QColor("#16181b")), (QPalette.AlternateBase, PANEL),
        (QPalette.Text, TEXT), (QPalette.Button, QColor("#3a3a3e")),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT),
        (QPalette.HighlightedText, QColor("#111")), (QPalette.ToolTipBase, PANEL),
        (QPalette.ToolTipText, TEXT),
    ]:
        pal.setColor(role, col)
    app.setPalette(pal)
    f = QFont("Noto Sans")
    f.setPointSize(9)
    app.setFont(f)
    app.setStyleSheet(f"""
        QMainWindow, QStackedWidget > QWidget {{ background:{BG.name()}; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; }}
        QToolTip {{ background:#2c2c2f; color:{TEXT.name()}; border:1px solid #111; padding:4px;
                   border-radius:4px; }}
        QMenu {{ background:#2c2c2f; border:1px solid #111; border-radius:6px; padding:4px; }}
        QMenu::item {{ padding:4px 18px; border-radius:4px; }}
        QMenu::item:selected {{ background:{ACCENT.name()}; color:#111; }}
        QMenu::separator {{ height:1px; background:#3a3a3e; margin:4px 6px; }}
        QPushButton {{ background:qlineargradient(y1:0,y2:1,stop:0 #48484c,stop:1 #3a3a3e);
                      color:{TEXT.name()}; border:1px solid #141415; border-radius:6px;
                      padding:4px 12px; }}
        QPushButton:hover {{ background:#505055; }}
        QPushButton:pressed {{ background:#2f2f33; }}
        QPushButton:checked {{ background:#5a5a60; }}
        QPushButton::menu-indicator {{ width:0; }}
        QWidget#segment {{ background:#141415; border-radius:7px; }}
        QPushButton#segbtn {{ background:transparent; border:none; border-radius:5px;
                             padding:4px 16px; color:{DIM.name()}; font-weight:600; }}
        QPushButton#segbtn:checked {{ background:qlineargradient(y1:0,y2:1,stop:0 #5c5c62,stop:1 #4a4a4f);
                                     color:white; }}
        QPushButton#slot {{ background:#141415; border:1px solid #0b0b0c; border-radius:5px;
                           padding:0; color:#5a5a60; font-weight:700; min-width:24px; max-width:24px;
                           min-height:22px; max-height:22px; }}
        QPushButton#slot[filled="true"] {{ color:{TEXT.name()}; background:#2e2e32; }}
        QPushButton#slot[active="true"] {{ color:#111; background:{ACCENT.name()}; }}
        QPushButton#slot:hover {{ border-color:{ACCENT.name()}; }}
        QPushButton#store:checked {{ background:#c0262b; color:white; }}
        QPushButton#solomaster {{ color:#5a5a60; font-weight:700; padding:4px 10px; }}
        QPushButton#solomaster[armed="true"] {{ color:{SOLO.name()}; }}
        QPushButton#solomaster[active="true"] {{ background:{SOLO.name()}; color:#111; }}
        QLabel#lcd {{ background:qlineargradient(y1:0,y2:1,stop:0 #101418,stop:1 #171c21);
                     color:#9fd0ff; border:1px solid #070708; border-radius:7px; padding:5px 14px;
                     font-family:{MONO}; font-size:10px; }}
        QLabel#caption {{ color:{DIM.name()}; font-size:9px; font-weight:700; letter-spacing:1px; }}
        QComboBox {{ background:qlineargradient(y1:0,y2:1,stop:0 #48484c,stop:1 #3a3a3e);
                    border:1px solid #141415; border-radius:6px; padding:3px 8px; color:{TEXT.name()}; }}
        QComboBox::drop-down {{ border:none; width:18px; }}
        QComboBox QAbstractItemView {{ background:#2c2c2f; border:1px solid #111;
                                      selection-background-color:{ACCENT.name()}; selection-color:#111; }}
        QGroupBox {{ background:{PANEL.name()}; border:1px solid #161617; border-radius:10px;
                    margin-top:20px; padding:12px 10px 10px 10px; }}
        QGroupBox::title {{ subcontrol-origin:margin; left:6px; top:2px; color:{DIM.name()};
                           font-size:9px; font-weight:700; }}
        QScrollBar:horizontal {{ background:transparent; height:8px; margin:2px 0 0 0; }}
        QScrollBar::handle:horizontal {{ background:#4a4a4f; border-radius:3px; min-width:40px; }}
        QScrollBar::handle:horizontal:hover {{ background:#6a6a70; }}
        QScrollBar:vertical {{ background:transparent; width:8px; }}
        QScrollBar::handle:vertical {{ background:#4a4a4f; border-radius:3px; min-height:40px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background:none; }}
    """)
