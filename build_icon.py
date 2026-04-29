# -*- coding: utf-8 -*-
"""Generate resources/nic-switcher.ico from the brand_tray_icon design.

Renders the icon at 16/32/48/64/128/256 px, then packs them into a single
multi-resolution .ico file using Pillow. PyInstaller's --icon flag picks
this up and Windows uses the appropriate size for taskbar / Explorer /
title-bar contexts.

Run once after any change to icons.brand_tray_icon. The .ico is checked
into the repo so the build doesn't require Pillow.

    python build_icon.py
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtWidgets import QApplication

from nic_switcher.icons import brand_tray_icon

try:
    from PIL import Image
except ImportError:
    print("[ERROR] Pillow not installed. Run: pip install Pillow")
    sys.exit(1)


SIZES = [16, 24, 32, 48, 64, 128, 256]
OUT = Path("resources/nic-switcher.ico")


def render_one(size: int) -> Image.Image:
    """Render brand_tray_icon at `size` px and return a PIL Image."""
    icon = brand_tray_icon(size)
    pix = icon.pixmap(size, size)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pix.save(buf, "PNG")
    return Image.open(io.BytesIO(buf.data().data())).convert("RGBA")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images = [render_one(s) for s in SIZES]
    # Save the largest image as the base, embed the others as alternate
    # resolutions. Pillow's ICO writer reads `sizes=` to pack them.
    images[-1].save(
        OUT,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[:-1],
    )
    sz = OUT.stat().st_size
    print(f"[OK] Wrote {OUT} ({sz} bytes, {len(SIZES)} resolutions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
