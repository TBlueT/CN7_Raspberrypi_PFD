"""
NDView (MVVM의 View)

B737 ND(항법 화면) 스타일의 오른쪽 패널. 지금은 거리 링과 헤딩만 그립니다.
(카메라 레이더 기능은 삭제됨 — 나중에 ADS-B 항공기 표시 등을 여기에 추가)

정적 배경(반원, 거리 링, 헤딩 박스 테두리)은 한 번만 그려서 QPixmap 으로 캐싱하고,
매 프레임은 헤딩 숫자만 새로 그립니다.
"""

import math

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPainterPath, QPixmap
from PyQt6.QtWidgets import QWidget

import config
from viewmodels.nd_viewmodel import NDViewModel
from views.fail_flag import draw_fail_flag


class NDView(QWidget):
    def __init__(self, viewmodel: NDViewModel, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFixedSize(config.PANEL_WIDTH, config.PANEL_HEIGHT)

        self.vm = viewmodel

        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self.update)
        self._render_timer.start(int(1000 / config.FRAME_RATE_HZ))

        self._font_small = QFont("sans-serif", 8)
        self._font_value = QFont("sans-serif", 12, QFont.Weight.Medium)

        # 절대 안 바뀌는 배경(반원, 거리 링, 라벨, 헤딩박스 테두리+고정 라벨)은
        # 한 번만 그려서 캐싱해두고, 매 프레임은 이걸 그대로 붙여넣기만 함
        self._background_pixmap = self._render_background()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawPixmap(0, 0, self._background_pixmap)
        self._draw_heading_value(painter)
        painter.end()

    def _render_background(self) -> QPixmap:
        """정적 배경 요소를 전부 그려서 QPixmap으로 반환. __init__에서 한 번만 호출됨."""
        pixmap = QPixmap(config.PANEL_WIDTH, config.PANEL_HEIGHT)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(pixmap.rect(), QColor(config.COLOR_BLACK))

        self._draw_range_rings(painter)
        self._draw_heading_box_static(painter)

        painter.end()
        return pixmap

    def _draw_range_rings(self, painter: QPainter):
        cx, cy = config.ND_CENTER
        r = config.ND_RADIUS

        bg_path = QPainterPath()
        bg_rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        bg_path.moveTo(cx + r, cy)
        bg_path.arcTo(bg_rect, 0, 180)
        bg_path.closeSubpath()
        painter.setPen(QPen(QColor(config.COLOR_TAPE_BORDER), 1))
        painter.setBrush(QBrush(QColor(config.COLOR_ND_BG)))
        painter.drawPath(bg_path)

        painter.setPen(QPen(QColor(config.COLOR_ND_RING), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        max_range = config.ND_MAX_RANGE
        painter.setFont(self._font_small)
        step = config.ND_RANGE_RING_STEP
        ring_count = max(1, int(round(max_range / step)))
        for i in range(1, ring_count + 1):
            frac = min(1.0, i * step / max_range)
            ring_r = r * frac
            ring_rect = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            painter.setPen(QPen(QColor(config.COLOR_ND_RING), 1))
            painter.drawArc(ring_rect, 0, 180 * 16)

            theta = math.radians(90)
            label_x = cx + ring_r * math.cos(theta)
            label_y = cy - ring_r * math.sin(theta)
            distance = max_range * frac
            painter.setPen(QPen(QColor(config.COLOR_TAPE_TICK)))
            painter.drawText(QRectF(label_x - 20, label_y - 8, 40, 16),
                              Qt.AlignmentFlag.AlignCenter, f"{distance:.0f}{config.ND_RANGE_UNIT}")

        painter.setPen(QPen(QColor(config.COLOR_ND_RING), 1))
        painter.drawLine(QPointF(cx - r, cy), QPointF(cx - r, cy - 4))
        painter.drawLine(QPointF(cx + r, cy), QPointF(cx + r, cy - 4))

    def _draw_heading_box_static(self, painter: QPainter):
        """헤딩박스에서 안 바뀌는 부분만 (포인터 삼각형, 테두리, 090/270 라벨).
        실제 헤딩 숫자는 매 프레임 _draw_heading_value()가 그 위에 덧그림."""
        cx, cy = config.ND_CENTER
        r = config.ND_RADIUS

        painter.setPen(QPen(QColor(config.COLOR_WHITE), 1))
        painter.setBrush(QBrush(QColor(config.COLOR_WHITE)))
        pointer = QPolygonF([
            QPointF(cx, cy - r - 5),
            QPointF(cx - 7, cy - r - 20),
            QPointF(cx + 7, cy - r - 20),
        ])
        painter.drawPolygon(pointer)

        box_rect = QRectF(cx - 25, cy - r - 43, 50, 18)
        painter.setPen(QPen(QColor(config.COLOR_TAPE_BORDER), 1))
        painter.setBrush(QBrush(QColor(config.COLOR_BLACK)))
        painter.drawRect(box_rect)

        painter.setFont(self._font_small)
        painter.setPen(QPen(QColor(config.COLOR_TAPE_TICK)))
        painter.drawText(QRectF(cx - r - 5, cy - 18, 30, 16),
                          Qt.AlignmentFlag.AlignLeft, "090")
        painter.drawText(QRectF(cx + r - 25, cy - 18, 30, 16),
                          Qt.AlignmentFlag.AlignRight, "270")

    def _draw_heading_value(self, painter: QPainter):
        """헤딩 숫자만 매 프레임 새로 그림 (박스/테두리는 배경 캐시에 이미 있음)."""
        cx, cy = config.ND_CENTER
        r = config.ND_RADIUS
        heading = self.vm.heading_deg

        box_rect = QRectF(cx - 25, cy - r - 43, 50, 18)
        if not self.vm.heading_valid:
            # IMU 헤딩 끊김: 숫자 박스를 덮고 HDG 고장 표시
            painter.fillRect(box_rect.adjusted(-2, -2, 2, 2), QColor(config.COLOR_BLACK))
            draw_fail_flag(painter, box_rect.center(), "HDG", self._font_value)
            return
        painter.setFont(self._font_value)
        painter.setPen(QPen(QColor(config.COLOR_WHITE)))
        painter.drawText(box_rect, Qt.AlignmentFlag.AlignCenter, f"{int(round(heading)):03d}")
