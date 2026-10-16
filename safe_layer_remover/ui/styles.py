"""Colors, theme detection and small painting helpers shared by the widgets."""

from __future__ import annotations

from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from qgis.PyQt.QtWidgets import QApplication

from ..core.model import Status

STATUS_COLORS = {
    Status.SAFE: "#2e9d57",
    Status.WARN: "#d99a00",
    Status.UNCERTAIN: "#e06f1f",
    Status.BLOCKED: "#d64545",
    Status.LOCKED: "#8a8f98",
    Status.ERROR: "#b3261e",
}

BADGE_COLORS = {
    "purple": "#8e44ad",
    "blue": "#2f6fd6",
    "slate": "#4f6d8a",
    "gray": "#7a7f87",
    "cyan": "#138d90",
    "orange": "#d9731a",
    "brown": "#8d5b2c",
    "teal": "#16a085",
    "indigo": "#4b4fc4",
    "red": "#d64545",
    "amber": "#c98a00",
    "neutral": "#7a7f87",
}


def is_dark() -> bool:
    pal = QApplication.palette()
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def status_color(status: Status) -> QColor:
    return QColor(STATUS_COLORS[status])


def badge_color(key: str) -> QColor:
    return QColor(BADGE_COLORS.get(key, BADGE_COLORS["gray"]))


def fill_color(base: QColor, strong: bool = False) -> QColor:
    """Translucent background derived from a saturated color, tuned for the current theme."""
    c = QColor(base)
    if is_dark():
        c.setAlpha(90 if strong else 65)
    else:
        c.setAlpha(55 if strong else 38)
    return c


def text_color(base: QColor) -> QColor:
    """Readable saturated text color on the translucent fill."""
    c = QColor(base)
    if is_dark():
        return c.lighter(150)
    return c.darker(125)


def row_tint(status: Status) -> QColor:
    c = QColor(STATUS_COLORS[status])
    c.setAlpha(34 if is_dark() else 22)
    return c


def small_font(font: QFont, ratio: float = 0.85) -> QFont:
    f = QFont(font)
    if f.pointSizeF() > 0:
        f.setPointSizeF(max(6.0, f.pointSizeF() * ratio))
    elif f.pixelSize() > 0:
        f.setPixelSize(max(8, int(f.pixelSize() * ratio)))
    return f


def draw_pill(painter: QPainter, rect: QRectF, text: str, color: QColor, font: QFont,
              strong: bool = False, outline: bool = False) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    radius = min(rect.height() / 2.0, 9.0)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, fill_color(color, strong))
    if outline:
        pen = QPen(color)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawPath(path)
    painter.setPen(text_color(color))
    painter.setFont(font)
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
    painter.restore()


def status_icon(status: Status, size: int = 16) -> QIcon:
    """Small vector icon drawn with QPainter: check, exclamation, question, no-entry, lock."""
    ratio = 2
    pix = QPixmap(size * ratio, size * ratio)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(ratio, ratio)
    color = status_color(status)
    s = float(size)
    pen = QPen(color)
    pen.setWidthF(max(1.6, s / 9.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    m = s * 0.12
    circle = QRectF(m, m, s - 2 * m, s - 2 * m)
    if status == Status.SAFE:
        p.drawEllipse(circle)
        path = QPainterPath(QPointF(s * 0.30, s * 0.52))
        path.lineTo(QPointF(s * 0.45, s * 0.66))
        path.lineTo(QPointF(s * 0.71, s * 0.36))
        p.drawPath(path)
    elif status == Status.WARN:
        tri = QPainterPath(QPointF(s / 2, m))
        tri.lineTo(QPointF(s - m, s - m))
        tri.lineTo(QPointF(m, s - m))
        tri.closeSubpath()
        p.drawPath(tri)
        p.drawLine(QPointF(s / 2, s * 0.40), QPointF(s / 2, s * 0.62))
        p.drawPoint(QPointF(s / 2, s * 0.76))
    elif status == Status.UNCERTAIN:
        p.drawEllipse(circle)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(int(s * 0.62))
        p.setFont(f)
        p.drawText(circle, int(Qt.AlignmentFlag.AlignCenter), "?")
    elif status in (Status.BLOCKED, Status.ERROR):
        p.drawEllipse(circle)
        if status == Status.BLOCKED:
            p.drawLine(QPointF(s * 0.30, s / 2), QPointF(s * 0.70, s / 2))
        else:
            p.drawLine(QPointF(s / 2, s * 0.28), QPointF(s / 2, s * 0.56))
            p.drawPoint(QPointF(s / 2, s * 0.72))
    elif status == Status.LOCKED:
        body = QRectF(s * 0.22, s * 0.45, s * 0.56, s * 0.42)
        p.drawRoundedRect(body, 1.5, 1.5)
        shackle = QPainterPath(QPointF(s * 0.34, s * 0.45))
        shackle.lineTo(QPointF(s * 0.34, s * 0.32))
        shackle.arcTo(QRectF(s * 0.34, s * 0.14, s * 0.32, s * 0.32), 180, -180)
        shackle.lineTo(QPointF(s * 0.66, s * 0.45))
        p.drawPath(shackle)
    p.end()
    return QIcon(pix)


def danger_button_style() -> str:
    c = STATUS_COLORS[Status.BLOCKED]
    return (
        "QPushButton {"
        f" background-color: {c}; color: white; border: 1px solid {QColor(c).darker(120).name()};"
        " border-radius: 4px; padding: 4px 12px; font-weight: bold; }"
        f"QPushButton:hover {{ background-color: {QColor(c).lighter(110).name()}; }}"
        f"QPushButton:pressed {{ background-color: {QColor(c).darker(115).name()}; }}"
        "QPushButton:disabled { background-color: palette(mid); color: palette(window); border-color: palette(mid); }"
    )
