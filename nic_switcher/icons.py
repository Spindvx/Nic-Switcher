"""Vector icons drawn with QPainter — crisp, consistent, themeable.

All icons are single-color stroked paths. Call with any color to match the
context (muted for idle, primary on hover, accent for active).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)


def _stroked(paint_fn: Callable[[QPainter, int, float], None],
             size: int, color: str, width: float = 1.5) -> QIcon:
    pix = QPixmap(size, size)
    pix.setDevicePixelRatio(1.0)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    paint_fn(p, size, width)
    p.end()
    return QIcon(pix)


def _filled(paint_fn: Callable[[QPainter, int], None], size: int, color: str) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(Qt.PenStyle.NoPen)
    paint_fn(p, size)
    p.end()
    return QIcon(pix)


# --- individual icons --------------------------------------------------------

def edit(size: int = 16, color: str = "#d6d9e1") -> QIcon:
    def draw(p: QPainter, s: int, w: float):
        pad = s * 0.16
        # pencil body: diagonal line from upper-right down to lower-left
        p.drawLine(QPointF(s - pad, pad + 1.2),
                   QPointF(pad + 1.2, s - pad))
        # nib tick
        p.drawLine(QPointF(pad + 1.2, s - pad),
                   QPointF(pad - 0.2, s - pad + 1.5))
        # tail cap
        p.drawLine(QPointF(s - pad - 2.2, pad + 0.2),
                   QPointF(s - pad + 0.6, pad + 3.0))
    return _stroked(draw, size, color)


def close(size: int = 16, color: str = "#d6d9e1") -> QIcon:
    def draw(p: QPainter, s: int, w: float):
        pad = s * 0.26
        p.drawLine(QPointF(pad, pad), QPointF(s - pad, s - pad))
        p.drawLine(QPointF(s - pad, pad), QPointF(pad, s - pad))
    return _stroked(draw, size, color, 1.7)


def plus(size: int = 16, color: str = "#d6d9e1") -> QIcon:
    def draw(p: QPainter, s: int, w: float):
        pad = s * 0.24
        mid = s / 2
        p.drawLine(QPointF(pad, mid), QPointF(s - pad, mid))
        p.drawLine(QPointF(mid, pad), QPointF(mid, s - pad))
    return _stroked(draw, size, color, 1.6)


def refresh(size: int = 16, color: str = "#d6d9e1") -> QIcon:
    def draw(p: QPainter, s: int, w: float):
        pad = s * 0.2
        rect = QRectF(pad, pad, s - 2 * pad, s - 2 * pad)
        # 3/4 arc
        p.drawArc(rect, 40 * 16, 280 * 16)
        # arrowhead at end
        head = QPointF(s * 0.72, s * 0.18)
        p.drawLine(head, QPointF(head.x() - 2.4, head.y() + 2.2))
        p.drawLine(head, QPointF(head.x() + 2.2, head.y() + 2.4))
    return _stroked(draw, size, color)


def search(size: int = 16, color: str = "#d6d9e1") -> QIcon:
    def draw(p: QPainter, s: int, w: float):
        r = s * 0.32
        cx, cy = s * 0.42, s * 0.42
        p.drawEllipse(QPointF(cx, cy), r, r)
        # handle
        p.drawLine(QPointF(cx + r * 0.72, cy + r * 0.72),
                   QPointF(s * 0.82, s * 0.82))
    return _stroked(draw, size, color, 1.5)


def gear(size: int = 16, color: str = "#d6d9e1") -> QIcon:
    def draw(p: QPainter, s: int, w: float):
        import math
        cx, cy = s / 2, s / 2
        outer = s * 0.36
        inner = s * 0.22
        path = QPainterPath()
        teeth = 8
        for i in range(teeth * 2):
            angle = (i / (teeth * 2)) * 2 * math.pi
            r = outer if i % 2 == 0 else outer - s * 0.08
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        p.drawPath(path)
        p.drawEllipse(QPointF(cx, cy), inner * 0.5, inner * 0.5)
    return _stroked(draw, size, color, 1.3)


def trash(size: int = 16, color: str = "#d6d9e1") -> QIcon:
    def draw(p: QPainter, s: int, w: float):
        pad_x = s * 0.22
        top = s * 0.28
        bottom = s * 0.82
        # lid
        p.drawLine(QPointF(pad_x - 1.2, top), QPointF(s - pad_x + 1.2, top))
        # handle
        p.drawLine(QPointF(s * 0.38, top), QPointF(s * 0.38, s * 0.19))
        p.drawLine(QPointF(s * 0.38, s * 0.19), QPointF(s * 0.62, s * 0.19))
        p.drawLine(QPointF(s * 0.62, s * 0.19), QPointF(s * 0.62, top))
        # body
        p.drawLine(QPointF(pad_x, top), QPointF(pad_x + s * 0.06, bottom))
        p.drawLine(QPointF(s - pad_x, top), QPointF(s - pad_x - s * 0.06, bottom))
        p.drawLine(QPointF(pad_x + s * 0.06, bottom), QPointF(s - pad_x - s * 0.06, bottom))
        # inner ribs
        p.drawLine(QPointF(s * 0.42, top + 2), QPointF(s * 0.42, bottom - 2))
        p.drawLine(QPointF(s * 0.58, top + 2), QPointF(s * 0.58, bottom - 2))
    return _stroked(draw, size, color, 1.4)


def chevron_down(size: int = 12, color: str = "#a7abb8") -> QIcon:
    def draw(p: QPainter, s: int, w: float):
        pad = s * 0.28
        p.drawLine(QPointF(pad, s * 0.40), QPointF(s / 2, s - pad))
        p.drawLine(QPointF(s / 2, s - pad), QPointF(s - pad, s * 0.40))
    return _stroked(draw, size, color, 1.6)


def network(size: int = 16, color: str = "#d6d9e1") -> QIcon:
    def draw(p: QPainter, s: int, w: float):
        cx, cy = s / 2, s / 2
        r = s * 0.10
        # center node
        p.drawEllipse(QPointF(cx, cy), r, r)
        # three satellites
        import math
        for ang in (math.radians(-90), math.radians(30), math.radians(150)):
            x = cx + (s * 0.34) * math.cos(ang)
            y = cy + (s * 0.34) * math.sin(ang)
            p.drawLine(QPointF(cx, cy), QPointF(x, y))
            p.drawEllipse(QPointF(x, y), r * 0.9, r * 0.9)
    return _stroked(draw, size, color, 1.4)


def shield(size: int = 16, color: str = "#d6d9e1") -> QIcon:
    def draw(p: QPainter, s: int, w: float):
        path = QPainterPath()
        path.moveTo(s / 2, s * 0.14)
        path.lineTo(s * 0.82, s * 0.26)
        path.lineTo(s * 0.82, s * 0.52)
        path.cubicTo(
            QPointF(s * 0.82, s * 0.78),
            QPointF(s * 0.64, s * 0.88),
            QPointF(s / 2, s * 0.90),
        )
        path.cubicTo(
            QPointF(s * 0.36, s * 0.88),
            QPointF(s * 0.18, s * 0.78),
            QPointF(s * 0.18, s * 0.52),
        )
        path.lineTo(s * 0.18, s * 0.26)
        path.closeSubpath()
        p.drawPath(path)
    return _stroked(draw, size, color, 1.5)


def dot(size: int = 10, color: str = "#6de3a4") -> QIcon:
    def draw(p: QPainter, s: int):
        # soft glow halo
        halo = QColor(color)
        halo.setAlpha(70)
        p.setBrush(QBrush(halo))
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.46, s * 0.46)
        # core
        p.setBrush(QBrush(QColor(color)))
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.26, s * 0.26)
    return _filled(draw, size, color)


# -----------------------------------------------------------------------------
# Brand logo — loads from resources/spindux-logo.png with a text
# fallback if the file isn't present. Drop your PNG at that path to override.
# -----------------------------------------------------------------------------

def _brand_logo_paths() -> list[Path]:
    paths: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / "resources" / "spindux-logo.png")
    here = Path(__file__).resolve().parent.parent
    paths.append(here / "resources" / "spindux-logo.png")
    return paths


def brand_logo(height: int = 22) -> QPixmap:
    """Return the Spindux logo scaled to `height`. Falls back to a text render."""
    for p in _brand_logo_paths():
        if p.is_file():
            pm = QPixmap(str(p))
            if not pm.isNull():
                return pm.scaledToHeight(
                    height,
                    Qt.TransformationMode.SmoothTransformation,
                )
    # Fallback: "SPINDUX" wordmark
    aspect_width = int(height * 4.6)
    pix = QPixmap(aspect_width, height)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = QFont("Segoe UI", int(height * 0.58))
    font.setWeight(QFont.Weight.Black)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
    p.setFont(font)
    p.setPen(QColor("#d6d9e1"))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "SPINDUX")
    p.end()
    return pix


# -----------------------------------------------------------------------------
# Brand mark used for the tray icon — distinctive, scalable.
# -----------------------------------------------------------------------------

def brand_tray_icon(size: int = 64) -> QIcon:
    """Rounded tile with gradient and a stylised 'N' network node mark."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # gradient background tile
    pad = max(2, size // 16)
    rect = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
    path = QPainterPath()
    radius = size * 0.22
    path.addRoundedRect(rect, radius, radius)

    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, QColor("#6ee2ff"))
    grad.setColorAt(0.6, QColor("#5bd7ff"))
    grad.setColorAt(1.0, QColor("#9c7bff"))
    p.fillPath(path, QBrush(grad))

    # soft inner highlight
    hi = QColor(255, 255, 255, 50)
    hi_path = QPainterPath()
    hi_path.addRoundedRect(
        QRectF(rect.left() + 1, rect.top() + 1,
               rect.width() - 2, rect.height() / 2 - 2),
        radius - 1, radius - 1,
    )
    p.fillPath(hi_path, QBrush(hi))

    # mark: two nodes with connecting line (stylized 'N' for Network/NIC)
    p.setPen(QPen(QColor(255, 255, 255, 240), max(2.2, size / 22),
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)

    # Two diagonal lines forming an 'N'
    x1 = size * 0.32
    x2 = size * 0.68
    y_top = size * 0.34
    y_bot = size * 0.68
    # left vertical
    p.drawLine(QPointF(x1, y_bot), QPointF(x1, y_top))
    # diagonal
    p.drawLine(QPointF(x1, y_top), QPointF(x2, y_bot))
    # right vertical
    p.drawLine(QPointF(x2, y_bot), QPointF(x2, y_top))

    # node dots at corners
    p.setBrush(QBrush(QColor(255, 255, 255)))
    p.setPen(Qt.PenStyle.NoPen)
    node_r = max(2.0, size / 22)
    for pt in ((x1, y_top), (x1, y_bot), (x2, y_top), (x2, y_bot)):
        p.drawEllipse(QPointF(*pt), node_r, node_r)

    p.end()
    return QIcon(pix)
