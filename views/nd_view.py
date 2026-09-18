"""
NDView (MVVM의 View)

이전 NDWidget과 렌더링 로직은 동일하지만, 상태를 직접 들고 있지 않고
viewmodels.nd_viewmodel.NDViewModel만 읽어서 그립니다.

라이다가 차량 자외선차단 필름을 통과 못해 카메라+YOLO26-Depth로 교체.
점 구름 대신 실제 B737 TCAS(공중충돌방지) 화면처럼 물체 하나당 마름모
하나 + 거리 숫자로 표시합니다. 카메라 시야각(config.CAMERA_HORIZONTAL_FOV_DEG)
이 라이다 때(180도)보다 훨씬 좁아서, 그 범위 밖은 점선으로 "커버리지 경계"를
표시합니다.
"""

import math

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPainterPath, QPixmap
from PyQt6.QtWidgets import QWidget

import config
from viewmodels.nd_viewmodel import NDViewModel


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

        # 절대 안 바뀌는 배경(반원, 거리 링, 라벨, 시야각 점선, 헤딩박스
        # 테두리+고정 라벨)은 한 번만 그려서 캐싱해두고, 매 프레임은 이걸
        # 그대로 붙여넣기만 함 (도형/삼각함수 재계산 없음)
        self._background_pixmap = self._render_background()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        painter.drawPixmap(0, 0, self._background_pixmap)

        self._draw_heading_value(painter)
        if self.vm.scan.points:
            self._draw_objects(painter)
        else:
            self._draw_scan_placeholder(painter)
        self._draw_label(painter)

        painter.end()

    def _render_background(self) -> QPixmap:
        """정적 배경 요소를 전부 그려서 QPixmap으로 반환. __init__에서 한 번만 호출됨."""
        pixmap = QPixmap(config.PANEL_WIDTH, config.PANEL_HEIGHT)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(pixmap.rect(), QColor(config.COLOR_BLACK))

        self._draw_range_rings(painter)
        self._draw_fov_boundary(painter)
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

        max_range = config.CAMERA_DEPTH_MAX_RANGE_M
        painter.setFont(self._font_small)
        for frac in (1 / 3, 2 / 3, 1.0):
            ring_r = r * frac
            ring_rect = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            painter.setPen(QPen(QColor(config.COLOR_ND_RING), 1))
            painter.drawArc(ring_rect, 0, 180 * 16)

            theta = math.radians(90)
            label_x = cx + ring_r * math.cos(theta)
            label_y = cy - ring_r * math.sin(theta)
            distance_m = max_range * frac
            painter.setPen(QPen(QColor(config.COLOR_TAPE_TICK)))
            painter.drawText(QRectF(label_x - 16, label_y - 8, 32, 16),
                              Qt.AlignmentFlag.AlignCenter, f"{distance_m:.0f}m")

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
        painter.setFont(self._font_value)
        painter.setPen(QPen(QColor(config.COLOR_WHITE)))
        painter.drawText(box_rect, Qt.AlignmentFlag.AlignCenter, f"{int(round(heading)):03d}")

    def _draw_fov_boundary(self, painter: QPainter):
        """카메라 시야각이 ND 반원(180도) 전체보다 좁으므로, 실제로 감지 가능한
        범위의 경계를 점선으로 표시해서 그 밖은 "커버리지 밖"임을 알려준다."""
        cx, cy = config.ND_CENTER
        r = config.ND_RADIUS
        half_fov = config.CAMERA_HORIZONTAL_FOV_DEG / 2

        pen = QPen(QColor(config.COLOR_TAPE_TICK), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        for sign in (-1, 1):
            theta = math.radians(90 - sign * half_fov)
            x = cx + r * math.cos(theta)
            y = cy - r * math.sin(theta)
            painter.drawLine(QPointF(cx, cy), QPointF(x, y))

    def _draw_objects(self, painter: QPainter):
        """TCAS 스타일: 물체 하나당 마름모 하나 + 거리 숫자.
        정면 -90도~+90도 범위만 표시. 각도 0도 = 차량 정면(화면 위쪽) 가정."""
        cx, cy = config.ND_CENTER
        r = config.ND_RADIUS
        max_range = config.CAMERA_DEPTH_MAX_RANGE_M

        painter.setFont(self._font_small)
        for angle_deg, distance_m in self.vm.scan.points:
            normalized = ((angle_deg + 180) % 360) - 180
            if not (-90 <= normalized <= 90):
                continue
            if distance_m <= 0 or distance_m > max_range:
                continue

            theta = math.radians(90 - normalized)
            radius_px = (distance_m / max_range) * r
            x = cx - radius_px * math.cos(theta)  # 좌우 반전 (화면 기준)
            y = cy - radius_px * math.sin(theta)

            self._draw_diamond(painter, x, y, distance_m)

        painter.setPen(QPen(QColor(config.COLOR_WHITE)))
        painter.setBrush(QBrush(QColor(config.COLOR_WHITE)))
        painter.drawEllipse(QPointF(cx, cy), 3, 3)

    def _draw_diamond(self, painter: QPainter, x: float, y: float, distance_m: float):
        size = 7
        diamond = QPolygonF([
            QPointF(x, y - size),
            QPointF(x + size, y),
            QPointF(x, y + size),
            QPointF(x - size, y),
        ])
        painter.setPen(QPen(QColor(config.COLOR_WHITE), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(diamond)

        painter.setPen(QPen(QColor(config.COLOR_WHITE)))
        painter.drawText(QRectF(x - 20, y + size + 2, 40, 14),
                          Qt.AlignmentFlag.AlignCenter, f"{distance_m:.0f}m")

    def _draw_scan_placeholder(self, painter: QPainter):
        """라이다 미연동 상태의 장식용 스캔 섹터."""
        cx, cy = config.ND_CENTER
        r = config.ND_RADIUS

        painter.setPen(QPen(QColor("#1D9E75"), 2))
        painter.drawLine(QPointF(cx, cy), QPointF(cx, cy - r * 0.85))

        sector = _PlaceholderSector(cx, cy, r)
        painter.setBrush(QBrush(QColor(30, 158, 117, 30)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(sector.polygon())

        painter.setPen(QPen(QColor(config.COLOR_WHITE)))
        painter.setBrush(QBrush(QColor(config.COLOR_WHITE)))
        painter.drawEllipse(QPointF(cx, cy), 3, 3)

    def _draw_label(self, painter: QPainter):
        points = self.vm.scan.points
        rect = QRectF(8, 8, config.PANEL_WIDTH - 16, 18)
        painter.setPen(QPen(QColor(config.COLOR_TAPE_BORDER), 1))
        painter.setBrush(QBrush(QColor(config.COLOR_BLACK)))
        painter.drawRect(rect)
        painter.setFont(self._font_small)
        if points:
            text = f"CAMERA DEPTH ({len(points)} obj)"
            color = "#9fe1cb"
        else:
            text = "CAMERA DEPTH (미연동)"
            color = config.COLOR_WARN_TEXT
        painter.setPen(QPen(QColor(color)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


class _PlaceholderSector:
    """장식용 부채꼴 섹터 좌표 계산 헬퍼 (라이다 연동 전까지만 사용)."""

    def __init__(self, cx, cy, r, half_angle_deg=30):
        self._cx, self._cy, self._r, self._half = cx, cy, r * 0.85, half_angle_deg

    def polygon(self) -> QPolygonF:
        points = [QPointF(self._cx, self._cy)]
        for deg in range(-self._half, self._half + 1, 5):
            theta = math.radians(90 - deg)
            x = self._cx + self._r * math.cos(theta)
            y = self._cy - self._r * math.sin(theta)
            points.append(QPointF(x, y))
        return QPolygonF(points)
