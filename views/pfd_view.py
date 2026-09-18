"""
PFDView (MVVM의 View)

이전 PFDWidget과 렌더링 로직은 동일하지만, 상태를 직접 들고 있지 않고
viewmodels.pfd_viewmodel.PFDViewModel의 model(attitude/vehicle)만 읽어서
그립니다. set_* 메서드가 전혀 없고, ViewModel은 신호를 보내지 않습니다. 대신
이 View가 config.FRAME_RATE_HZ 고정 주기 타이머로 스스로 최신 상태를 읽어가서
다시 그립니다(폴링). IMU 등 센서가 아무리 빨리 갱신돼도 화면 갱신 속도는
이 주기로 제한됩니다.

- 지평선(하늘/땅)이 화면 전체가 아니라 둥근 사각형 ADI 볼(config.ADI_BALL_RECT)
  안에만 보이도록 클리핑됨 (실제 737처럼)
- 속도/고도 테이프가 그 볼 양옆에 밀착 배치
- 이 위젯 자체는 400x480 크기의 독립 패널이며, 화면 전체(800x480)의 왼쪽
  절반을 차지. 오른쪽 절반은 views.nd_view.NDView가 담당 (main.py에서 나란히 배치)
- 상단 FMA(비행모드 어나운시에이터) 스타일 모드바
- 속도 테이프에 트렌드 화살표 추가

고도는 아직 센서가 없으므로 항상 0 고정 표시
"""

import math

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPainterPath
from PyQt6.QtWidgets import QWidget

import config
from viewmodels.pfd_viewmodel import PFDViewModel


class PFDView(QWidget):
    def __init__(self, viewmodel: PFDViewModel, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFixedSize(config.PANEL_WIDTH, config.PANEL_HEIGHT)

        self.vm = viewmodel

        # ViewModel은 신호를 보내지 않고 최신값만 들고 있음(폴링 대상).
        # View가 FRAME_RATE_HZ 고정 주기로 스스로 다시 그려서, 센서 데이터가
        # 아무리 빨리 들어와도(IMU 수십~수백Hz 등) 화면 갱신은 이 주기로 제한됨.
        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self.update)
        self._render_timer.start(int(1000 / config.FRAME_RATE_HZ))

        self._font_small = QFont("sans-serif", 10)
        self._font_medium = QFont("sans-serif", 10)
        self._font_value = QFont("sans-serif", 15, QFont.Weight.Medium)

    # ---------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(config.COLOR_BLACK))

        self._draw_fma_bar(painter)
        self._draw_adi_ball(painter)
        self._draw_aircraft_symbol(painter)
        self._draw_bank_indicator(painter)
        self._draw_speed_tape(painter)
        self._draw_altitude_tape(painter)
        self._draw_heading_arc(painter)

        painter.end()

    # ---------------------------------------------------------------
    def _draw_fma_bar(self, painter: QPainter):
        vehicle = self.vm.vehicle
        x, y, w, h = config.FMA_BAR_RECT
        rect = QRectF(x, y, w, h)
        painter.setPen(QPen(QColor(config.COLOR_TAPE_BORDER), 1))
        painter.setBrush(QBrush(QColor(config.COLOR_BLACK)))
        painter.drawRect(rect)

        for div_x in config.FMA_DIVIDER_X:
            painter.drawLine(QPointF(div_x, y), QPointF(div_x, y + h))

        painter.setFont(self._font_small)

        if vehicle.scc_active:
            spd_text = f"SPD {int(round(vehicle.scc_set_speed))}"
            spd_color = config.COLOR_MODE_TEXT
        elif vehicle.scc_main_on:
            spd_text = "SPD"
            spd_color = config.COLOR_WHITE
        else:
            spd_text = "SPD"
            spd_color = config.COLOR_MODE_TEXT_OFF

        if vehicle.hda_curve_active:
            hdg_text = "HDA"
            hdg_color = config.COLOR_MODE_TEXT
        else:
            hdg_text = "LFA" if vehicle.lfa_active else "HDG SEL"
            hdg_color = config.COLOR_MODE_TEXT if vehicle.lfa_active else config.COLOR_MODE_TEXT_OFF

        alt_text = "ALT"
        alt_color = (config.COLOR_FEATURE_OK if self.vm.attitude.imu_feature_confirmed
                     else config.COLOR_FEATURE_MISSING)

        col_bounds = [x, config.FMA_DIVIDER_X[0], config.FMA_DIVIDER_X[1], x + w]
        labels = [(spd_text, spd_color), (hdg_text, hdg_color), (alt_text, alt_color)]
        for i, (text, color) in enumerate(labels):
            col_rect = QRectF(col_bounds[i], y, col_bounds[i + 1] - col_bounds[i], h)
            painter.setPen(QPen(QColor(color)))
            painter.drawText(col_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_adi_ball(self, painter: QPainter):
        attitude = self.vm.attitude
        bx, by, bw, bh = config.ADI_BALL_RECT
        ball_rect = QRectF(bx, by, bw, bh)

        clip_path = QPainterPath()
        clip_path.addRoundedRect(ball_rect, 10, 10)

        painter.save()
        painter.setClipPath(clip_path)

        cx, cy = config.ADI_CENTER
        painter.translate(cx, cy)
        painter.rotate(-attitude.roll_deg)
        painter.translate(0, attitude.pitch_deg * config.PIXELS_PER_DEGREE_PITCH)

        big = 500
        painter.fillRect(QRectF(-big, -big, big * 2, big), QColor(config.COLOR_SKY))
        painter.fillRect(QRectF(-big, 0, big * 2, big), QColor(config.COLOR_GROUND))
        painter.setPen(QPen(QColor(config.COLOR_HORIZON_LINE), 1.5))
        painter.drawLine(QPointF(-big, 0), QPointF(big, 0))

        self._draw_pitch_ladder(painter)

        painter.restore()

        painter.setPen(QPen(QColor(config.COLOR_TAPE_BORDER), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(ball_rect, 10, 10)

    def _draw_pitch_ladder(self, painter: QPainter):
        painter.setFont(self._font_small)

        for deg in range(-30, 31):
            if deg == 0:
                continue
            y = -deg * config.PIXELS_PER_DEGREE_PITCH

            if deg % 10 == 0:
                half_w = 25
                pen = QPen(QColor(config.COLOR_WHITE), 1.3)
                painter.setPen(pen)
                painter.drawLine(QPointF(-half_w, y), QPointF(half_w, y))
                painter.drawText(QRectF(-half_w - 20, y - 8, 18, 16),
                                  Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                  str(abs(deg)))
                painter.drawText(QRectF(half_w + 2, y - 8, 18, 16),
                                  Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                  str(abs(deg)))
            elif deg % 5 == 0:
                half_w = 15
                pen = QPen(QColor(config.COLOR_WHITE), 1)
                painter.setPen(pen)
                painter.drawLine(QPointF(-half_w, y), QPointF(half_w, y))

    def _draw_aircraft_symbol(self, painter: QPainter):
        cx, cy = config.ADI_CENTER
        pen = QPen(QColor(config.COLOR_WHITE), 3)
        painter.setPen(pen)
        painter.drawLine(QPointF(cx - 38, cy), QPointF(cx - 15, cy))
        painter.drawLine(QPointF(cx + 15, cy), QPointF(cx + 38, cy))

        painter.setPen(QPen(QColor(config.COLOR_WHITE), 1))
        painter.setBrush(QBrush(QColor(config.COLOR_WHITE)))
        triangle = QPolygonF([
            QPointF(cx, cy - 5),
            QPointF(cx - 9, cy + 5),
            QPointF(cx + 9, cy + 5),
        ])
        painter.drawPolygon(triangle)

    def _draw_bank_indicator(self, painter: QPainter):
        attitude = self.vm.attitude
        cx, cy = config.ADI_CENTER
        r = config.BANK_ARC_RADIUS

        pen = QPen(QColor(config.COLOR_WHITE), 1.5)
        painter.setPen(pen)
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        painter.drawArc(rect, 40 * 16, 100 * 16)

        for tick_deg in (-30, 0, 30):
            theta = math.radians(90 - tick_deg)
            x1 = cx + r * math.cos(theta)
            y1 = cy - r * math.sin(theta)
            x2 = cx + (r - 8) * math.cos(theta)
            y2 = cy - (r - 8) * math.sin(theta)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        painter.setBrush(QBrush(QColor(config.COLOR_WHITE)))
        fixed_triangle = QPolygonF([
            QPointF(cx, cy - r - 5),
            QPointF(cx - 6, cy - r + 6),
            QPointF(cx + 6, cy - r + 6),
        ])
        painter.drawPolygon(fixed_triangle)

        painter.save()
        painter.translate(cx, cy)
        painter.rotate(-attitude.roll_deg)
        pointer = QPolygonF([
            QPointF(0, -r + 2),
            QPointF(-6, -r + 14),
            QPointF(6, -r + 14),
        ])
        painter.setBrush(QBrush(QColor(config.COLOR_WHITE)))
        painter.drawPolygon(pointer)
        painter.restore()

    def _draw_vertical_tape(self, painter: QPainter, rect_tuple, value: float,
                             pixels_per_unit: float, tick_step: int,
                             ticks_on_right: bool, value_format="{:.0f}",
                             slide_value_box: bool = False):
        x, y, w, h = rect_tuple
        rect = QRectF(x, y, w, h)
        painter.setPen(QPen(QColor(config.COLOR_TAPE_BORDER), 2))
        painter.setBrush(QBrush(QColor(config.COLOR_BLACK)))
        painter.drawRect(rect)

        mid_y = y + h / 2
        painter.setFont(self._font_small)

        tick_pixel_step = tick_step * pixels_per_unit
        base_value = round(value / tick_step) * tick_step
        start_offset = (value - base_value) * pixels_per_unit

        tick_x1 = x + w - 12
        tick_x2 = x + w
        text_x = x + 4
        text_w = w - 18
        align = Qt.AlignmentFlag.AlignRight
        if ticks_on_right:
            tick_x1 = x
            tick_x2 = x + 12
            text_x = x + 14
            align = Qt.AlignmentFlag.AlignLeft

        pen_tick = QPen(QColor(config.COLOR_TAPE_TICK), 1)
        painter.setPen(pen_tick)
        for i in range(-15, 16):
            ty = mid_y + start_offset - i * tick_pixel_step
            if y < ty < y + h:
                painter.drawLine(QPointF(tick_x1, ty), QPointF(tick_x2, ty))
                tick_value = base_value + i * tick_step
                painter.setPen(QPen(QColor(config.COLOR_WHITE)))
                painter.drawText(QRectF(text_x, ty - 8, text_w, 16),
                                  align | Qt.AlignmentFlag.AlignVCenter,
                                  value_format.format(tick_value))
                painter.setPen(pen_tick)

        box_h = 20
        box_rect = QRectF(x + 4, mid_y - box_h / 2, w - 8, box_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(config.COLOR_CURRENT_VALUE_BG)))
        painter.drawRect(box_rect)

        painter.setFont(self._font_value)
        if slide_value_box:
            self._draw_sliding_value(painter, box_rect, value, value_format)
        else:
            painter.setPen(QPen(QColor(config.COLOR_BLACK)))
            painter.drawText(box_rect, Qt.AlignmentFlag.AlignCenter, value_format.format(value))

    def _draw_sliding_value(self, painter: QPainter, box_rect: QRectF,
                             value: float, value_format: str):
        """오도미터처럼, 정수값이 바뀔 때 위로 슬라이드하며 전환되는 효과.
        예: 72 -> 73으로 바뀔 때 72가 위로 빠지면서 73이 아래에서 올라옴."""
        base = math.floor(value)
        frac = value - base   # 0.0 ~ 1.0, 다음 정수로의 진행률
        box_h = box_rect.height()

        painter.save()
        painter.setClipRect(box_rect)
        painter.setPen(QPen(QColor(config.COLOR_BLACK)))

        offset_base = -frac * box_h
        offset_next = box_h - frac * box_h

        current_rect = QRectF(box_rect.x(), box_rect.y() + offset_base,
                               box_rect.width(), box_h)
        next_rect = QRectF(box_rect.x(), box_rect.y() + offset_next,
                            box_rect.width(), box_h)

        painter.drawText(current_rect, Qt.AlignmentFlag.AlignCenter,
                          value_format.format(base))
        painter.drawText(next_rect, Qt.AlignmentFlag.AlignCenter,
                          value_format.format(base + 1))

        painter.restore()

    def _draw_speed_tape(self, painter: QPainter):
        vehicle = self.vm.vehicle
        self._draw_vertical_tape(
            painter, config.SPEED_TAPE_RECT, vehicle.speed_kph,
            config.SPEED_PIXELS_PER_UNIT, 5, ticks_on_right=False,
            slide_value_box=True)

        trend = vehicle.speed_kph - vehicle.prev_speed_kph
        if abs(trend) > 0.05:
            x, y, w, h = config.SPEED_TAPE_RECT
            mid_y = y + h / 2
            tip_x = x + w - 2
            length = max(6, min(40, abs(trend) * 20))
            tip_y = mid_y - length if trend > 0 else mid_y + length
            pen = QPen(QColor(config.COLOR_TREND), 2)
            painter.setPen(pen)
            painter.drawLine(QPointF(tip_x, mid_y), QPointF(tip_x, tip_y))
            arrow_dir = -1 if trend > 0 else 1
            painter.drawLine(QPointF(tip_x, tip_y), QPointF(tip_x - 4, tip_y - arrow_dir * 6))

    def _draw_altitude_tape(self, painter: QPainter):
        self._draw_vertical_tape(
            painter, config.ALT_TAPE_RECT, self.vm.vehicle.altitude_ft,
            config.ALT_PIXELS_PER_UNIT, 100, ticks_on_right=True)

    def _draw_heading_arc(self, painter: QPainter):
        yaw = self.vm.attitude.yaw_deg
        cx, cy = config.HEADING_ARC_CENTER
        r = config.HEADING_ARC_RADIUS
        half_span_heading = config.HEADING_ARC_SPAN_DEG / 2
        half_span_visual = 40

        r_bg = r + 12
        bg_rect = QRectF(cx - r_bg, cy - r_bg, r_bg * 2, r_bg * 2)
        bg_path = QPainterPath()
        bg_path.moveTo(cx + r_bg, cy)
        bg_path.arcTo(bg_rect, 0, 180)
        bg_path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(config.COLOR_HEADING_BG)))
        painter.drawPath(bg_path)

        pen = QPen(QColor(config.COLOR_TAPE_BORDER), 1.5)
        painter.setPen(pen)
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        painter.drawArc(rect, 0, 180 * 16)

        painter.setFont(self._font_small)
        for delta in range(int(-half_span_heading), int(half_span_heading) + 1, 10):
            theta_deg = 90 - (delta / half_span_heading) * half_span_visual
            theta = math.radians(theta_deg)
            x1 = cx + r * math.cos(theta)
            y1 = cy - r * math.sin(theta)
            x2 = cx + (r - 10) * math.cos(theta)
            y2 = cy - (r - 10) * math.sin(theta)

            painter.setPen(QPen(QColor(config.COLOR_WHITE), 1.2))
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            heading_val = int(round((yaw + delta) % 360))
            label_x = cx + (r - 24) * math.cos(theta)
            label_y = cy - (r - 24) * math.sin(theta)
            painter.drawText(QRectF(label_x - 13, label_y - 8, 26, 16),
                              Qt.AlignmentFlag.AlignCenter, f"{heading_val:02d}")

        painter.setBrush(QBrush(QColor(config.COLOR_WHITE)))
        painter.setPen(Qt.PenStyle.NoPen)
        pointer_top_y = cy - r - 10
        pointer = QPolygonF([
            QPointF(cx, pointer_top_y + 10),
            QPointF(cx - 6, pointer_top_y),
            QPointF(cx + 6, pointer_top_y),
        ])
        painter.drawPolygon(pointer)

        box_rect = QRectF(cx - 26, pointer_top_y - 20, 52, 20)
        painter.setPen(QPen(QColor(config.COLOR_WHITE), 1))
        painter.setBrush(QBrush(QColor(config.COLOR_BLACK)))
        painter.drawRect(box_rect)
        painter.setFont(self._font_value)
        painter.setPen(QPen(QColor(config.COLOR_WHITE)))
        painter.drawText(box_rect, Qt.AlignmentFlag.AlignCenter, f"{int(round(yaw)):03d}")
