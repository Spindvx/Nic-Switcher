"""Design system — tokens + stylesheet.

Aesthetic: iOS dark mode + glass. Translucent layered surfaces over Mica
backdrop, generous corner radius, vibrant accent, near-borderless cards
separated by tone rather than lines.
"""

# ---------------------------------------------------------------------------
# Design tokens — iOS dark + glass
# ---------------------------------------------------------------------------

# -- Surfaces (layered darks, more translucent for the glass feel) --
BG_DEEP        = "rgba(8, 9, 14, 220)"      # popup base — deepest layer
BG_ROOT        = "rgba(20, 22, 28, 200)"    # primary surface (root container)
BG_CARD        = "rgba(255, 255, 255, 6)"   # iOS-style "fill on glass" — very subtle white over the popup tint
BG_CARD_HOVER  = "rgba(255, 255, 255, 12)"
BG_CARD_ACTIVE = "rgba(255, 255, 255, 18)"
BG_CHIP        = "rgba(255, 255, 255, 10)"

# -- Borders — minimal; surfaces separate via tone, not lines --
BORDER         = "rgba(255, 255, 255, 14)"
BORDER_STRONG  = "rgba(255, 255, 255, 30)"
BORDER_SUBTLE  = "rgba(255, 255, 255, 6)"

# -- Accent (iOS-blue-leaning cyan) --
ACCENT         = "#5bd7ff"
ACCENT_HOVER   = "#7de3ff"
ACCENT_PRESS   = "#3fc2ed"
ACCENT_DIM     = "#3a7e9a"
ACCENT_INK     = "#051820"

# -- Semantic --
SUCCESS        = "#6de3a4"
WARNING        = "#ffc266"
DANGER         = "#ff7a85"

# -- Text — slightly cooler greys, iOS-ish --
TEXT_PRIMARY   = "#f6f7fa"
TEXT_BODY      = "#dadde4"
TEXT_SECOND    = "#a3a7b3"
TEXT_MUTED     = "#777b87"
TEXT_DIM       = "#54575f"
TEXT_INK       = "#0a0b10"

# -- Radii --
RADIUS_LG      = 16
RADIUS_MD      = 12
RADIUS_SM      = 9
RADIUS_PILL    = 999

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
    font-family: "Segoe UI Variable Text", "Segoe UI", -apple-system, system-ui;
    font-size: 13px;
}}

#root {{
    background: {BG_ROOT};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: {RADIUS_LG}px;
}}

/* --------- typography --------- */
QLabel#title {{
    color: {TEXT_PRIMARY};
    font-size: 17px;
    font-weight: 600;
    letter-spacing: -0.3px;
}}
QLabel#subtitle {{
    color: {TEXT_SECOND};
    font-size: 12px;
    letter-spacing: -0.1px;
}}
QLabel#section {{
    color: {TEXT_MUTED};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.6px;
    padding-top: 2px;
}}
QLabel#subtle {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}
QLabel#mono {{
    font-family: "SF Mono", "Cascadia Mono", "Consolas", "Menlo", monospace;
    font-size: 12px;
    color: {TEXT_BODY};
    letter-spacing: -0.2px;
}}
QLabel#statusOk   {{ color: {SUCCESS};  font-size: 11px; font-weight: 600; }}
QLabel#statusWarn {{ color: {WARNING};  font-size: 11px; font-weight: 600; }}
QLabel#statusErr  {{ color: {DANGER};   font-size: 11px; font-weight: 600; }}

QLabel#divider {{
    background: {BORDER_SUBTLE};
    max-height: 1px;
    min-height: 1px;
    margin: 4px 0 4px 0;
}}

/* --------- inputs (iOS-style fill-on-glass) --------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {BG_CARD};
    border: 1px solid transparent;
    border-radius: {RADIUS_SM}px;
    padding: 8px 11px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
    background: {BG_CARD_HOVER};
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
    background: #15171f;
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
    padding: 6px;
    outline: 0;
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}
QComboBox QAbstractItemView::item {{
    padding: 8px 12px;
    border-radius: 7px;
    color: {TEXT_BODY};
    min-height: 22px;
}}

/* --------- buttons (iOS pill-ish) --------- */
QPushButton {{
    background: {BG_CARD};
    border: 1px solid transparent;
    border-radius: {RADIUS_SM}px;
    padding: 8px 16px;
    color: {TEXT_PRIMARY};
    font-weight: 500;
}}
QPushButton:hover {{
    background: {BG_CARD_HOVER};
}}
QPushButton:pressed {{
    background: {BG_CARD_ACTIVE};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    background: rgba(255,255,255,3);
}}

QPushButton#accent {{
    background: {ACCENT};
    color: {ACCENT_INK};
    border: 1px solid {ACCENT};
    border-radius: {RADIUS_SM}px;
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
    padding: 7px 12px;
    border-radius: {RADIUS_SM}px;
    font-weight: 500;
}}
QPushButton#ghost:hover {{
    background: {BG_CARD};
    color: {TEXT_PRIMARY};
}}
QPushButton#ghost:pressed {{
    background: {BG_CARD_HOVER};
}}

QPushButton#icon {{
    background: transparent;
    border: 1px solid transparent;
    padding: 4px;
    color: {TEXT_SECOND};
    border-radius: 8px;
}}
QPushButton#icon:hover {{
    background: {BG_CARD};
    color: {TEXT_PRIMARY};
}}
QPushButton#icon:pressed {{ background: {BG_CARD_HOVER}; }}
QPushButton#icon:checked {{
    background: rgba(91, 215, 255, 30);
    color: {ACCENT};
}}

QPushButton#danger-ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {DANGER};
    padding: 4px;
    border-radius: 8px;
}}
QPushButton#danger-ghost:hover {{
    background: rgba(255, 122, 133, 30);
}}

/* --------- preset / device cards (borderless, tone-only separation) --------- */
QFrame#presetCard, QFrame#deviceCard {{
    background: {BG_CARD};
    border: 1px solid transparent;
    border-radius: {RADIUS_MD}px;
}}
QFrame#presetCard:hover, QFrame#deviceCard:hover {{
    background: {BG_CARD_HOVER};
}}
QFrame#presetCardActive {{
    background: rgba(91, 215, 255, 28);
    border: 1px solid {ACCENT};
    border-radius: {RADIUS_MD}px;
}}

/* --------- pills / chips (iOS-rounded) --------- */
QLabel#pill {{
    background: {BG_CHIP};
    color: {TEXT_BODY};
    border: 1px solid transparent;
    border-radius: 11px;
    padding: 2px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}

/* --------- spinbox steppers --------- */
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; height: 0; border: none; }}

/* --------- checkbox --------- */
QCheckBox {{ color: {TEXT_BODY}; spacing: 8px; }}
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

/* --------- scrollbars (very subtle, iOS overlay style) --------- */
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 4px 2px 4px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,30);
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,70); }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* --------- tooltip --------- */
QToolTip {{
    background: rgba(10, 11, 16, 230);
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 10px;
    font-size: 11px;
}}

/* --------- dialogs --------- */
QDialog {{ background: #14161e; }}
QDialog QLabel {{ color: {TEXT_BODY}; }}

QDialogButtonBox QPushButton {{ min-width: 88px; }}

QMenu {{
    background: #15171f;
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
    padding: 6px;
    color: {TEXT_PRIMARY};
}}
QMenu::item {{
    padding: 8px 14px;
    border-radius: 7px;
    min-width: 160px;
}}
QMenu::item:selected {{
    background: {ACCENT};
    color: {ACCENT_INK};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER_SUBTLE};
    margin: 6px 4px;
}}
"""
