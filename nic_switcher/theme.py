"""Design system — tokens + stylesheet.

Aesthetic: black-glass + pastel red. Translucent layered surfaces over Mica
backdrop, generous corner radius, soft pastel-red accent, near-borderless
cards separated by tone rather than lines.

────────────────────────────────────────────────────────────────────────────
QUICK COLOR REFERENCE — change these three lines to retheme everything:

  ACCENT       → main button / focus / link color   (currently pastel red)
  SELECT_GLOW  → "on / selected / pinned" indicator (currently pastel red)
  BG_DEEP      → popup body tint                    (currently pure black)

The popup's paintEvent in popup.py also has a hard-coded RGBA — search for
'QColor(0, 0, 0' to tweak the body opacity directly.
────────────────────────────────────────────────────────────────────────────
"""

# ---------------------------------------------------------------------------
# Design tokens — black glass + pastel red
# ---------------------------------------------------------------------------

# -- Surfaces — solid near-black. We no longer rely on Mica showing
#    through (it's unreliable across Windows builds), and the inner
#    #root paints the body in one pass so there are no grey-ring
#    artifacts from competing paint layers.
BG_DEEP        = "#06080c"                       # popup body — deepest near-black
BG_ROOT        = "#080a10"                       # #root container — main surface
BG_CARD        = "#13161d"                       # cards / inputs — one tone lighter
BG_CARD_HOVER  = "#1a1d25"
BG_CARD_ACTIVE = "#22252e"
BG_CHIP        = "#181b22"

# -- Borders — visible but soft, 1px hairlines for elevation cues --
BORDER         = "rgba(255, 255, 255, 16)"
BORDER_STRONG  = "rgba(255, 255, 255, 36)"
BORDER_SUBTLE  = "rgba(255, 255, 255, 8)"

# -- Accent (deeper muted red) --
# Pulled deeper from the previous dusty-rose toward a richer brick / wine
# tone. Still in the soft-red family but with more saturation and
# visual weight on the near-black surface.
ACCENT         = "#bf5560"   # main button / focus / link color — deep muted red
ACCENT_HOVER   = "#cc6671"
ACCENT_PRESS   = "#a14550"
ACCENT_DIM     = "#5a2c33"
ACCENT_INK     = "#100204"   # very dark text on the deep-red bg

# -- Selected / "on" indicator (a lighter sibling of ACCENT) --
# Used for: pin button when pinned, active preset card. Brighter than
# ACCENT so the "currently active" state pops without competing with
# regular accent buttons.
SELECT_GLOW       = "#d68690"
SELECT_GLOW_RGBA  = "rgba(214, 134, 144, 75)"  # soft halo for stylesheet

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
    border: 1px solid {BORDER};
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

/* --------- inputs --------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 8px 11px;
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

/* --------- buttons --------- */
QPushButton {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 8px 16px;
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
    background: {BG_DEEP};
    border-color: {BORDER_SUBTLE};
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
    /* "On" state — pastel red halo. SELECT_GLOW_RGBA controls the tint. */
    background: {SELECT_GLOW_RGBA};
    color: {SELECT_GLOW};
    border: 1px solid {SELECT_GLOW_RGBA};
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

/* --------- preset / device cards --------- */
QFrame#presetCard, QFrame#deviceCard {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QFrame#presetCard:hover, QFrame#deviceCard:hover {{
    background: {BG_CARD_HOVER};
    border-color: {BORDER_STRONG};
}}
QFrame#presetCardActive {{
    /* Active preset — same card surface as inactive, pastel-red border
       only. The green LED on the left side indicates "live"; the border
       indicates "this preset is currently applied". */
    background: {BG_CARD};
    border: 1px solid {SELECT_GLOW};
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
/* GlassDialog handles its own background via paintEvent — leave QDialog
   transparent so the black-glass tint shines through. */
QDialog {{ background: transparent; }}
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
