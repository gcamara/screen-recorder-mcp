"""Mission-control design tokens as Python constants plus the QSS builder.

Source of truth: ~/.claude/ui-style/tokens.css. Dark is the default; the ink has a violet
cast and the accent is violet on purpose (Gabriel rejects the slate + stock-blue look).
Widgets read colours from `Palette`/`build_qss`; no hex lives in widget code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FONT_DIR = Path(__file__).parent / "assets" / "fonts"
FONT_FILES: tuple[str, ...] = (
    "Geist-Regular.ttf",
    "Geist-Medium.ttf",
    "Geist-SemiBold.ttf",
    "GeistMono-Regular.ttf",
    "GeistMono-Medium.ttf",
)
SANS_FAMILY = "Geist"
MONO_FAMILY = "Geist Mono"
SANS_FALLBACK = "Segoe UI Variable"  # macOS falls through to the system font (SF Pro)

# 4px spacing scale, radii and type scale (tokens.css).
SPACE = {0: 2, 1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32, 7: 40, 8: 48, 9: 64}
RADIUS = {"xs": 4, "sm": 6, "md": 10, "lg": 14, "xl": 20, "pill": 999}
TEXT_SIZE = {"2xs": 10, "xs": 11, "sm": 12, "md": 14, "lg": 16, "xl": 20, "2xl": 28, "3xl": 40}
DUR_FAST, DUR_BASE, DUR_SLOW = 150, 220, 300  # ms


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface_raised: str
    surface_hover: str
    surface_overlay: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    accent: str
    accent_strong: str
    accent_soft: str
    accent_ink: str
    success: str
    warning: str
    failure: str
    failure_soft: str
    scrim: str


DARK = Palette(
    name="dark", bg="#0b0a10", surface="#121117", surface_raised="#18171f",
    surface_hover="#1e1d27", surface_overlay="#1b1a23", border="#23222c",
    border_strong="#33313f", text="#ecebf2", text_muted="#a3a1b2", text_faint="#9896aa",
    accent="#9d8cff", accent_strong="#b8abff", accent_soft="rgba(157,140,255,0.14)",
    accent_ink="#120d2e", success="#3ddc84", warning="#ffb224", failure="#ff4d9d",
    failure_soft="rgba(255,77,157,0.13)", scrim="rgba(4,3,8,0.6)",
)

LIGHT = Palette(
    name="light", bg="#f6f5fa", surface="#ffffff", surface_raised="#ffffff",
    surface_hover="#efedf6", surface_overlay="#ffffff", border="#e2e0ec",
    border_strong="#c9c6d8", text="#17161c", text_muted="#57556a", text_faint="#5f5d6e",
    accent="#5b45e0", accent_strong="#4630c4", accent_soft="rgba(91,69,224,0.12)",
    accent_ink="#ffffff", success="#0b7240", warning="#8a4e00", failure="#b30f5d",
    failure_soft="rgba(179,15,93,0.12)", scrim="rgba(20,18,30,0.35)",
)


def palette_for(theme: str, system_is_dark: bool = True) -> Palette:
    """Resolve the settings value ("system" | "dark" | "light") to a palette.

    Example: palette_for("system", system_is_dark=False) is LIGHT
    """
    if theme not in ("system", "dark", "light"):
        raise ValueError(f"theme {theme!r} is not one of 'system', 'dark', 'light'")
    if theme == "system":
        return DARK if system_is_dark else LIGHT
    return DARK if theme == "dark" else LIGHT


def _button_rules(p: Palette) -> str:
    return f"""
QPushButton, QToolButton {{ background: {p.surface_raised}; color: {p.text};
  border: 1px solid {p.border}; border-radius: {RADIUS['sm']}px; padding: 6px 12px; }}
QPushButton:hover, QToolButton:hover {{ background: {p.surface_hover}; border-color: {p.border_strong}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {p.surface_overlay}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {p.accent_strong}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {p.text_faint}; background: {p.surface}; }}
QPushButton[role="primary"] {{ background: {p.accent}; color: {p.accent_ink};
  border: 1px solid {p.accent}; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {p.accent_strong}; border-color: {p.accent_strong}; }}
QPushButton[role="danger"] {{ color: {p.failure}; background: {p.failure_soft}; border-color: {p.failure_soft}; }}
QToolButton[flat="true"], QPushButton[flat="true"] {{ background: transparent; border: 1px solid transparent; }}
QToolButton[flat="true"]:hover, QPushButton[flat="true"]:hover {{ background: {p.surface_hover}; }}
QToolButton:checked {{ background: {p.accent_soft}; border-color: {p.accent}; }}
QToolButton::menu-indicator {{ image: none; width: 0px; }}
"""


def _input_rules(p: Palette, glyphs: Path | None) -> str:
    chevron = f"image: url({(glyphs / 'chevron-down.png').as_posix()});" if glyphs else ""
    tick = f"image: url({(glyphs / 'check.png').as_posix()});" if glyphs else ""
    return f"""
QComboBox, QLineEdit, QSpinBox {{ background: {p.surface_raised}; color: {p.text};
  border: 1px solid {p.border}; border-radius: {RADIUS['sm']}px; padding: 5px 10px;
  selection-background-color: {p.accent}; selection-color: {p.accent_ink}; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: {p.border_strong}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {p.accent_strong}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ {chevron} width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {p.surface_overlay}; color: {p.text};
  border: 1px solid {p.border_strong}; selection-background-color: {p.accent_soft};
  selection-color: {p.text}; outline: none; padding: 4px; }}
QCheckBox {{ spacing: 8px; color: {p.text}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: {RADIUS['xs']}px;
  border: 1px solid {p.border_strong}; background: {p.surface_raised}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; {tick} }}
QRadioButton {{ spacing: 8px; color: {p.text}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px;
  border: 1px solid {p.border_strong}; background: {p.surface_raised}; }}
QRadioButton::indicator:checked {{ width: 6px; height: 6px; border: 5px solid {p.accent}; background: {p.accent_ink}; }}
QSlider::groove:horizontal {{ height: 4px; background: {p.border_strong}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px;
  background: {p.accent_strong}; }}
"""


def _container_rules(p: Palette) -> str:
    return f"""
QMenu {{ background: {p.surface_overlay}; color: {p.text}; border: 1px solid {p.border_strong};
  border-radius: {RADIUS['md']}px; padding: 4px; }}
QMenu::item {{ padding: 6px 20px 6px 12px; border-radius: {RADIUS['xs']}px; }}
QMenu::item:selected {{ background: {p.accent_soft}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}
QToolTip {{ background: {p.surface_overlay}; color: {p.text}; border: 1px solid {p.border_strong};
  padding: 4px 8px; }}
QProgressBar {{ background: {p.border}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollArea {{ border: none; background: transparent; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {RADIUS['lg']}px; }}
QListWidget::item:hover {{ background: {p.surface_hover}; border-color: {p.border_strong}; }}
QListWidget::item:selected {{ background: {p.accent_soft}; border-color: {p.accent}; }}
"""


def _label_rules(p: Palette) -> str:
    return f"""
QLabel[role="muted"] {{ color: {p.text_muted}; }}
QLabel[role="eyebrow"] {{ color: {p.text_faint}; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
QLabel[role="title"] {{ font-size: 20px; font-weight: 600; }}
QLabel[role="mono"] {{ font-family: "{MONO_FAMILY}"; }}
QLabel[role="badge-ai"] {{ background: {p.accent_soft}; color: {p.accent_strong};
  border-radius: {RADIUS['xs']}px; padding: 1px 6px; font-size: 10px; font-weight: 600; }}
QFrame[role="card"] {{ background: {p.surface}; border: 1px solid {p.border};
  border-radius: {RADIUS['lg']}px; }}
QFrame[role="pill"] {{ background: {p.surface_overlay}; border: 1px solid {p.border_strong};
  border-radius: 22px; }}
"""


def build_qss(p: Palette, glyphs: Path | None = None) -> str:
    """Application-wide stylesheet for one palette.

    `glyphs` is a folder holding chevron-down.png and check.png tinted for this palette.
    Example: app.setStyleSheet(build_qss(DARK))
    """
    base = (
        f'* {{ font-family: "{SANS_FAMILY}", "{SANS_FALLBACK}", "Segoe UI", sans-serif; font-size: 14px; }}\n'
        f"QWidget {{ color: {p.text}; }}\n"
        f"QMainWindow, QDialog, QWidget[surface=\"bg\"] {{ background: {p.bg}; }}\n"
    )
    return base + _button_rules(p) + _input_rules(p, glyphs) + _container_rules(p) + _label_rules(p)
