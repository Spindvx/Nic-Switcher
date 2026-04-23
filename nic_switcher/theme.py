"""Design system — tokens + stylesheet.

Aesthetic reference: Raycast / Linear / Stream Deck. Deep layered surfaces,
restrained accent, precise typography, tight hover/focus states.
"""

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

# -- Surfaces (layered darks) --
BG_DEEP       = "rgba(10, 11, 15, 240)"    # popup base
BG_ROOT       = "rgba(22, 24, 32, 232)"    # primary surface
BG_CARD       = "rgba(38, 41, 52, 200)"    # card / input surface
BG_CARD_HOVER = "rgba(52, 56, 70, 220)"
BG_CARD_ACTIVE = "rgba(64, 70, 88, 235)"
BG_CHIP       = "rgba(60, 66, 84, 180)"

# -- Borders --
BORDER        = "rgba(255, 255, 255, 20)"
BORDER_STRONG = "rgba(255, 255, 255, 40)"
BORDER_SUBTLE = "rgba(255, 255, 255, 10)"

# -- Accent (premium cyan/azure) --
ACCENT        = "#5bd7ff"
ACCENT_HOVER  = "#7de3ff"
ACCENT_PRESS  = "#3fc2ed"
ACCENT_DIM    = "#3a7e9a"
ACCENT_INK    = "#051820"  # text on accent bg

# -- Semantic --
SUCCESS       = "#6de3a4"
WARNING       = "#ffc266"
DANGER        = "#ff7a85"

# -- Text --
TEXT_PRIMARY  = "#f2f4f9"
TEXT_BODY     = "#d6d9e1"
TEXT_SECOND   = "#a7abb8"
TEXT_MUTED    = "#7f8392"
TEXT_DIM      = "#5a5d69"
TEXT_INK      = "#0b0c12"

# -- Kind accent colors for device pills --
KIND_COLORS = {
    "qsys":     "#7bd88f",
    "crestron": "#5bd7ff",
    "biamp":    "#d9a2ff",
    "dante":    "#ffd479",
    "extron":   "#ffaf5b",
    "amx":      "#c792ea",
    "shure":    "#ff9499",
    "clearone": "#ff9499",
    "lutron":   "#ffc266",
    "solstice": "#7de3ff",
    "livewire": "#ffd479",
    "videoconf":"#f67ac5",
    "display":  "#ffae6a",
    "camera":   "#f67ac5",
    "yamaha":   "#ffd479",
    "switch":   "#a0b0ff",
    "gateway":  "#ffd479",
    "apple":    "#d0d4dc",
    "host":     "#8a8d9a",
    "printer":  "#9aa0ae",
    "chromecast":"#ff9499",
    "ssh-host": "#a0b0ff",
}


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------
STYLE = f"""
/* --------- base --------- */
QWidget {{
    color: {TEXT_BODY};
    font-family: "Segoe UI Variable Text", "Segoe UI", system-ui;
    font-size: 13px;
}}

#root {{
    background: {BG_ROOT};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}

/* --------- typography --------- */
QLabel#title {{
    color: {TEXT_PRIMARY};
    font-size: 17px;
    font-weight: 600;
    letter-spacing: -0.2px;
}}
QLabel#subtitle {{
    color: {TEXT_SECOND};
    font-size: 12px;
}}
QLabel#section {{
    color: {TEXT_MUTED};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.4px;
    padding-top: 2px;
}}
QLabel#subtle {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}
QLabel#mono {{
    font-family: "Cascadia Mono", "Consolas", "Menlo", monospace;
    font-size: 12px;
    color: {TEXT_BODY};
}}
QLabel#statusOk   {{ color: {SUCCESS};  font-size: 11px; font-weight: 600; }}
QLabel#statusWarn {{ color: {WARNING};  font-size: 11px; font-weight: 600; }}
QLabel#statusErr  {{ color: {DANGER};   font-size: 11px; font-weight: 600; }}

QLabel#divider {{
    background: {BORDER_SUBTLE};
    max-height: 1px;
    min-height: 1px;
    margin: 2px 0 2px 0;
}}

/* --------- inputs --------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
    background: {BG_CARD_HOVER};
    border-color: {BORDER_STRONG};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {ACCENT};
    background: {BG_CARD_ACTIVE};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {TEXT_DIM};
}}
QLineEdit {{ min-height: 18px; }}

QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
    background: #1b1d26;
    border: 1px solid {BORDER_STRONG};
    border-radius: 10px;
    padding: 6px;
    outline: 0;
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}
QComboBox QAbstractItemView::item {{
    padding: 7px 10px;
    border-radius: 6px;
    color: {TEXT_BODY};
    min-height: 20px;
}}

/* --------- buttons --------- */
QPushButton {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 14px;
    color: {TEXT_PRIMARY};
    font-weight: 500;
}}
QPushButton:hover {{
    background: {BG_CARD_HOVER};
    border-color: {BORDER_STRONG};
}}
QPushButton:pressed {{
    background: {BG_CARD_ACTIVE};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    background: rgba(38, 41, 52, 120);
}}

QPushButton#accent {{
    background: {ACCENT};
    color: {ACCENT_INK};
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton#accent:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton#accent:pressed {{
    background: {ACCENT_PRESS};
    border-color: {ACCENT_PRESS};
}}
QPushButton#accent:disabled {{
    background: {ACCENT_DIM};
    color: rgba(5, 24, 32, 160);
    border-color: {ACCENT_DIM};
}}

QPushButton#ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {TEXT_SECOND};
    padding: 6px 10px;
    font-weight: 500;
}}
QPushButton#ghost:hover {{
    background: {BG_CARD};
    color: {TEXT_PRIMARY};
    border-color: {BORDER};
}}
QPushButton#ghost:pressed {{
    background: {BG_CARD_HOVER};
}}

QPushButton#icon {{
    background: transparent;
    border: 1px solid transparent;
    padding: 4px;
    color: {TEXT_SECOND};
    border-radius: 7px;
}}
QPushButton#icon:hover {{
    background: {BG_CARD};
    color: {TEXT_PRIMARY};
    border-color: {BORDER};
}}
QPushButton#icon:pressed {{ background: {BG_CARD_HOVER}; }}

QPushButton#danger-ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {DANGER};
    padding: 4px;
    border-radius: 7px;
}}
QPushButton#danger-ghost:hover {{
    background: rgba(255, 122, 133, 30);
    border-color: rgba(255, 122, 133, 70);
}}

/* --------- preset / device cards --------- */
QFrame#presetCard, QFrame#deviceCard {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#presetCard:hover, QFrame#deviceCard:hover {{
    background: {BG_CARD_HOVER};
    border-color: {BORDER_STRONG};
}}
QFrame#presetCardActive {{
    background: rgba(91, 215, 255, 22);
    border: 1px solid {ACCENT};
    border-radius: 10px;
}}

/* --------- pills / chips --------- */
QLabel#pill {{
    background: {BG_CHIP};
    color: {TEXT_BODY};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}

/* --------- spinbox steppers --------- */
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; height: 0; border: none; }}

/* --------- checkbox --------- */
QCheckBox {{ color: {TEXT_BODY}; }}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 5px;
    background: {BG_CARD};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

/* --------- scrollbars --------- */
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,40);
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,80); }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* --------- tooltip --------- */
QToolTip {{
    background: #15161d;
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 9px;
    font-size: 11px;
}}

/* --------- dialogs --------- */
QDialog {{ background: #14161e; }}
QDialog QLabel {{ color: {TEXT_BODY}; }}

QDialogButtonBox QPushButton {{ min-width: 88px; }}

QMenu {{
    background: #1b1d26;
    border: 1px solid {BORDER_STRONG};
    border-radius: 10px;
    padding: 6px;
    color: {TEXT_PRIMARY};
}}
QMenu::item {{
    padding: 7px 14px;
    border-radius: 6px;
    min-width: 150px;
}}
QMenu::item:selected {{
    background: {ACCENT};
    color: {ACCENT_INK};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 6px 4px;
}}
"""
