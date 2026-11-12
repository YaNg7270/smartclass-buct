"""纸张主题的文字、图标与工作台插画。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from random import Random

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QWidget

ASSETS = Path(__file__).parent
INK = "#34362e"
CLAY = "#a65036"


@lru_cache(maxsize=64)
def icon(name: str, color: str = INK) -> QIcon:
    data = (ASSETS / "icons" / f"{name}.svg").read_bytes()
    renderer = QSvgRenderer(data.replace(b"currentColor", color.encode("ascii")))
    result = QIcon()
    for size in (24, 48, 96):
        canvas = QPixmap(size, size)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        renderer.render(painter)
        painter.end()
        result.addPixmap(canvas)
    return result


def label(text: str, role: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(role)
    widget.setWordWrap(True)
    return widget


def button(text: str, callback, symbol: str = "", role: str = "") -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(role)
    if symbol:
        widget.setIcon(icon(symbol, "#fff8e9" if role == "primary" else INK))
        widget.setIconSize(QSize(20, 20))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


def scroll_page(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(widget)
    return area


class LecturePlate(QWidget):
    """投影仪手绘图置于两张讲义上，按可用空间等比绘制。"""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(150, 145)
        self.setAccessibleName("手绘课堂投影仪")
        self.drawing = QSvgRenderer((ASSETS / "icons/projector.svg").read_bytes()
                                    .replace(b"currentColor", b"#424838"))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width() / 300, self.height() / 185)
        p.translate((self.width() - 300 * scale) / 2, (self.height() - 185 * scale) / 2)
        p.scale(scale, scale)
        p.translate(149, 94)
        for angle, color, border in ((7, "#d5c9b2", "#ad9e85"), (-5, "#f2ebd7", "#b3aa95")):
            p.save()
            p.rotate(angle)
            p.setPen(QPen(QColor(border), 1.25))
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(-106, -68, 212, 136), 3, 3)
            p.restore()
        p.rotate(-5)
        p.setPen(QPen(QColor("#dfd6bf"), .8))
        for i in range(-96, 102, 12):
            p.drawLine(i, -64, i, 64)
        for j in range(-55, 62, 12):
            p.drawLine(-102, j, 102, j)
        rng = Random(729)
        p.setPen(QColor("#cfc2a5"))
        for _ in range(105):
            p.drawPoint(rng.randrange(-101, 101), rng.randrange(-60, 62))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#d58a61"))
        p.drawEllipse(QRectF(33, -48, 49, 49))
        self.drawing.render(p, QRectF(-80, -57, 155, 113))
        p.end()
