"""
데이터 끊김 고장 표시 (B737 PFD/ND 의 ATT, SPD, HDG 플래그 스타일)

보잉 계기 관례대로 주황(앰버) 테두리 박스 + 같은 색 글자로 그린다.
"""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen

import config


def draw_fail_flag(painter: QPainter, center: QPointF, text: str, font: QFont):
    painter.save()
    painter.setFont(font)
    fm = painter.fontMetrics()
    w = fm.horizontalAdvance(text) + 12
    h = fm.height() + 4
    rect = QRectF(center.x() - w / 2, center.y() - h / 2, w, h)
    color = QColor(config.COLOR_FAIL_FLAG)
    painter.setPen(QPen(color, 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()
