"""Tray icon — delegates to icons.brand_tray_icon for the premium mark."""
from __future__ import annotations

from PyQt6.QtGui import QIcon

from .icons import brand_tray_icon


def make_tray_icon(size: int = 64) -> QIcon:
    return brand_tray_icon(size)
