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
from views.fail_flag import draw_fail_flag


class PFDView(QWidget):
    # 속도/고도 현재값 박스 크기 (두 박스 공통, 포인터 제외)
    READOUT_BOX_W = 58
    READOUT_DRUM_W = 24

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
        self._font_flag_big = QFont("sans-serif", 18, QFont.Weight.Bold)
        self._font_flag = QFont("sans-serif", 14, QFont.Weight.Bold)
        self._font_tape = QFont("sans-serif", 11, QFont.Weight.Bold)
        self._font_tape_unit = QFont("sans-serif", 9, QFont.Weight.Bold)
        self._font_readout = QFont("sans-serif", 16, QFont.Weight.Bold)
        self._font_readout_small = QFont("sans-serif", 12, QFont.Weight.Bold)
        self._font_selected = QFont("sans-serif", 14, QFont.Weight.Bold)
        self._font_compass = QFont("sans-serif", 11, QFont.Weight.Bold)
        self._font_compass_big = QFont("sans-serif", 13, QFont.Weight.Bold)
        self._font_readout_heading = QFont("sans-serif", 15, QFont.Weight.Bold)

    # ---------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(config.COLOR_BLACK))

        self._draw_fma_bar(painter)
        if self.vm.attitude_valid:
            self._draw_adi_ball(painter)
            self._draw_aircraft_symbol(painter)
            self._draw_bank_indicator(painter)
        else:
            self._draw_attitude_fail(painter)
        if self.vm.speed_valid:
            self._draw_speed_tape(painter)
        else:
            self._draw_speed_fail(painter)
        self._draw_altitude_tape(painter)
        if self.vm.heading_valid:
            self._draw_heading_arc(painter)
        else:
            self._draw_heading_fail(painter)

        painter.end()

    # ---------------------------------------------------------------
    # 데이터 끊김 고장 표시 (IMU -> ATT/HDG, OBD -> SPD)
    # ---------------------------------------------------------------
    def _draw_attitude_fail(self, painter: QPainter):
        """자세 데이터 없음: ADI 볼을 비우고(지평선/피치/뱅크 포인터 제거) 가운데 ATT."""
        ball_rect = QRectF(*config.ADI_BALL_RECT)
        painter.setPen(QPen(QColor(config.COLOR_TAPE_BORDER), 2))
        painter.setBrush(QBrush(QColor(config.COLOR_BLACK)))
        painter.drawRoundedRect(ball_rect, 10, 10)
        draw_fail_flag(painter, QPointF(*config.ADI_CENTER), "ATT", self._font_flag_big)

    def _draw_speed_fail(self, painter: QPainter):
        """속도 데이터 없음: 테이프 눈금/현재값을 지우고 가운데 SPD."""
        x, y, w, h = config.SPEED_TAPE_RECT
        rect = QRectF(x, y, w, h)
        painter.setPen(QPen(QColor(config.COLOR_TAPE_BORDER), 2))
        painter.setBrush(QBrush(QColor(config.COLOR_BLACK)))
        painter.drawRect(rect)
        draw_fail_flag(painter, rect.center(), "SPD", self._font_flag)

    def _draw_heading_fail(self, painter: QPainter):
        """헤딩 데이터 없음: 나침반 원판만 남기고 눈금/숫자 대신 HDG."""
        self._draw_compass_disk(painter)
        cx, cy = config.HEADING_ARC_CENTER
        r = config.HEADING_ARC_RADIUS
        # 평소 헤딩 숫자 박스 자리에 표시
        draw_fail_flag(painter, QPointF(cx, cy - r - 14), "HDG", self._font_flag)

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

    # ---------------------------------------------------------------
    # 속도 / 고도 테이프 (B737 NG 스타일)
    #   회색 테이프, 높은 값이 위. 현재값 박스는 검정 바탕 + 흰 테두리 +
    #   ADI 쪽 포인터, 끝자리는 드럼처럼 세로로 굴러감(테이프와 같은 방향).
    # ---------------------------------------------------------------
    @staticmethod
    def _tape_layout(rect_tuple):
        """테이프 전체 영역 -> (위 띠, 본체, 아래 띠)."""
        x, y, w, h = rect_tuple
        top_h = config.TAPE_TOP_STRIP_PX
        bot_h = config.TAPE_BOTTOM_STRIP_PX
        top = QRectF(x, y, w, top_h - 2)
        body = QRectF(x, y + top_h, w, h - top_h - bot_h)
        bottom = QRectF(x, y + h - bot_h + 2, w, bot_h - 2)
        return top, body, bottom

    def _draw_tape_scale(self, painter: QPainter, body: QRectF, value: float, ppu: float,
                         tick_step: int, label_step: int, ticks_left: bool, min_value=None):
        """눈금(tick_step 마다)과 숫자(label_step 마다). 눈금은 ADI 쪽 가장자리."""
        mid = body.center().y()
        half_range = body.height() / 2 / ppu
        lo = int(math.floor((value - half_range) / tick_step) * tick_step)
        hi = int(math.ceil((value + half_range) / tick_step) * tick_step)
        tick_len = 9
        painter.setFont(self._font_tape)
        painter.setPen(QPen(QColor(config.COLOR_WHITE), 1.5))
        for s in range(lo, hi + 1, tick_step):
            if min_value is not None and s < min_value:
                continue
            ty = mid - (s - value) * ppu
            if ticks_left:
                x1, x2 = body.left(), body.left() + tick_len
                text_rect = QRectF(x2 + 2, ty - 9, body.width() - tick_len - 4, 18)
                align = Qt.AlignmentFlag.AlignLeft
            else:
                x1, x2 = body.right() - tick_len, body.right()
                text_rect = QRectF(body.left() + 2, ty - 9, body.width() - tick_len - 4, 18)
                align = Qt.AlignmentFlag.AlignRight
            painter.drawLine(QPointF(x1, ty), QPointF(x2, ty))
            if s % label_step == 0:
                painter.drawText(text_rect, align | Qt.AlignmentFlag.AlignVCenter, str(s))

    def _draw_speed_tape(self, painter: QPainter):
        vehicle = self.vm.vehicle
        v = max(0.0, vehicle.speed_kph)
        ppu = config.SPEED_PIXELS_PER_UNIT
        top, body, bottom = self._tape_layout(config.SPEED_TAPE_RECT)
        scc_set = (vehicle.scc_set_speed
                   if vehicle.scc_active and vehicle.scc_set_speed > 0 else None)

        painter.fillRect(body, QColor(config.COLOR_TAPE_BG))
        painter.save()
        painter.setClipRect(body)
        self._draw_barber_pole(painter, body, v, ppu)
        self._draw_tape_scale(painter, body, v, ppu, config.SPEED_TICK_STEP,
                              config.SPEED_LABEL_STEP, ticks_left=False, min_value=0)
        self._draw_speed_trend(painter, body, vehicle.speed_accel_kph_s, ppu)
        painter.restore()

        if scc_set is not None:
            self._draw_speed_bug(painter, body, v, scc_set, ppu)
        self._draw_speed_readout(painter, body, v)

        # 위 띠: SCC 설정속도 (실기의 MCP 선택 속도 자리, 마젠타)
        painter.fillRect(top, QColor(config.COLOR_BLACK))
        if scc_set is not None:
            painter.setFont(self._font_selected)
            painter.setPen(QPen(QColor(config.COLOR_SELECTED)))
            painter.drawText(top, Qt.AlignmentFlag.AlignCenter, f"{int(round(scc_set))}")
        # 아래 띠: 단위 (실기의 마하수 자리)
        painter.setFont(self._font_tape_unit)
        painter.setPen(QPen(QColor(config.COLOR_WHITE)))
        painter.drawText(bottom, Qt.AlignmentFlag.AlignCenter, "KM/H")

    def _draw_barber_pole(self, painter: QPainter, body: QRectF, v: float, ppu: float):
        """SPEED_BARBER_POLE_KPH 이상 구간에 빨강/검정 줄무늬 (실기의 최대속도 표시)."""
        limit = getattr(config, "SPEED_BARBER_POLE_KPH", None)
        if limit is None:
            return
        limit_y = body.center().y() - (limit - v) * ppu
        if limit_y <= body.top():
            return
        strip = QRectF(body.right() - 8, body.top(), 8, min(limit_y, body.bottom()) - body.top())
        painter.fillRect(strip, QColor(config.COLOR_BLACK))
        painter.save()
        painter.setClipRect(strip)
        painter.setPen(QPen(QColor(config.COLOR_BARBER_POLE), 4))
        yy = strip.top() - 12
        while yy < strip.bottom() + 12:
            painter.drawLine(QPointF(strip.left(), yy + 8), QPointF(strip.right(), yy))
            yy += 12
        painter.restore()

    def _draw_speed_trend(self, painter: QPainter, body: QRectF, accel: float, ppu: float):
        """SPEED_TREND_SEC 뒤 예상 속도까지 초록 화살표 (테이프 끝에서 멈춤)."""
        delta = accel * config.SPEED_TREND_SEC
        if abs(delta) < 1.0:
            return
        mid = body.center().y()
        tip_y = min(max(mid - delta * ppu, body.top() + 4), body.bottom() - 4)
        tx = body.right() - 3
        painter.setPen(QPen(QColor(config.COLOR_SPEED_TREND), 2))
        painter.drawLine(QPointF(tx, mid), QPointF(tx, tip_y))
        d = -1 if delta > 0 else 1
        painter.drawLine(QPointF(tx, tip_y), QPointF(tx - 4, tip_y - d * 7))
        painter.drawLine(QPointF(tx, tip_y), QPointF(tx + 4, tip_y - d * 7))

    def _draw_speed_bug(self, painter: QPainter, body: QRectF, v: float, target: float, ppu: float):
        """SCC 설정속도 버그 (마젠타). 범위 밖이면 테이프 끝에 걸쳐 둠."""
        by = min(max(body.center().y() - (target - v) * ppu, body.top() + 2), body.bottom() - 2)
        left, right = body.right() - 7, body.right() + 5
        painter.setPen(QPen(QColor(config.COLOR_SELECTED), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF([
            QPointF(left, by - 8), QPointF(right, by - 8), QPointF(right, by + 8),
            QPointF(left, by + 8), QPointF(left + 5, by),
        ]))

    def _draw_speed_readout(self, painter: QPainter, body: QRectF, v: float):
        """현재 속도 박스: 오른쪽(ADI 쪽) 포인터, 일의 자리 드럼, 십의 자리는 9->0 때만 굴러감."""
        mid = body.center().y()
        # 박스 크기는 고도 박스와 동일 (READOUT_BOX_W / READOUT_DRUM_W)
        bl = body.left() + 2
        br = bl + self.READOUT_BOX_W
        dl = br - self.READOUT_DRUM_W   # 드럼 창 왼쪽
        outline = QPolygonF([
            QPointF(bl, mid - 13), QPointF(dl, mid - 13), QPointF(dl, mid - 21),
            QPointF(br, mid - 21), QPointF(br, mid - 6), QPointF(br + 9, mid),
            QPointF(br, mid + 6), QPointF(br, mid + 21), QPointF(dl, mid + 21),
            QPointF(dl, mid + 13), QPointF(bl, mid + 13),
        ])
        painter.setPen(QPen(QColor(config.COLOR_WHITE), 1.5))
        painter.setBrush(QBrush(QColor(config.COLOR_BLACK)))
        painter.drawPolygon(outline)

        base = math.floor(v)
        frac = v - base
        units = base % 10
        painter.setFont(self._font_readout_small)   # 고도 박스 숫자와 같은 크기
        painter.setPen(QPen(QColor(config.COLOR_WHITE)))

        def upper(n):
            return str(n // 10) if n >= 10 else ""

        upper_rect = QRectF(bl + 1, mid - 12, dl - bl - 2, 24)
        align_r = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        painter.save()
        painter.setClipRect(upper_rect)
        if units == 9:
            sp = 17
            painter.drawText(upper_rect.translated(0, frac * sp), align_r, upper(base))
            painter.drawText(upper_rect.translated(0, -(1 - frac) * sp), align_r, upper(base + 1))
        else:
            painter.drawText(upper_rect, align_r, upper(base))
        painter.restore()

        # 드럼 숫자는 고도 박스 드럼과 같은 크기/간격 (12pt, 17px)
        drum = QRectF(dl + 1, mid - 20, br - dl - 2, 40)
        sp = 17
        painter.save()
        painter.setClipRect(drum)
        for k in (-2, -1, 0, 1, 2):
            n = base + k
            if n < 0:
                continue
            cy = mid - (k - frac) * sp
            painter.drawText(QRectF(dl, cy - 10, br - dl, 20), Qt.AlignmentFlag.AlignCenter, str(n % 10))
        painter.restore()

    def _draw_altitude_tape(self, painter: QPainter):
        value = self.vm.vehicle.altitude_ft
        ppu = config.ALT_PIXELS_PER_UNIT
        top, body, bottom = self._tape_layout(config.ALT_TAPE_RECT)

        painter.fillRect(body, QColor(config.COLOR_TAPE_BG))
        painter.save()
        painter.setClipRect(body)
        self._draw_tape_scale(painter, body, value, ppu, config.ALT_TICK_STEP,
                              config.ALT_LABEL_STEP, ticks_left=True)
        painter.restore()
        self._draw_altitude_readout(painter, body, value)

        painter.fillRect(top, QColor(config.COLOR_BLACK))   # 실기의 선택 고도 자리 (차량은 없음)
        painter.setFont(self._font_tape_unit)
        painter.setPen(QPen(QColor(config.COLOR_WHITE)))
        painter.drawText(bottom, Qt.AlignmentFlag.AlignCenter, "FT")

    def _draw_altitude_readout(self, painter: QPainter, body: QRectF, value: float):
        """현재 고도 박스: 왼쪽(ADI 쪽) 포인터, 끝 두 자리는 20 단위 드럼(실기와 동일)."""
        mid = body.center().y()
        br = body.right() - 2
        bl = br - self.READOUT_BOX_W
        dl = br - self.READOUT_DRUM_W
        outline = QPolygonF([
            QPointF(bl, mid - 6), QPointF(bl - 9, mid), QPointF(bl, mid + 6),
            QPointF(bl, mid + 13), QPointF(dl, mid + 13), QPointF(dl, mid + 21),
            QPointF(br, mid + 21), QPointF(br, mid - 21), QPointF(dl, mid - 21),
            QPointF(dl, mid - 13), QPointF(bl, mid - 13),
        ])
        painter.setPen(QPen(QColor(config.COLOR_WHITE), 1.5))
        painter.setBrush(QBrush(QColor(config.COLOR_BLACK)))
        painter.drawPolygon(outline)

        step = 20
        pos = value / step
        base_i = math.floor(pos)
        frac = pos - base_i
        snapped = (base_i + (1 if frac >= 0.5 else 0)) * step
        hundreds = abs(int(snapped)) // 100
        sign = "-" if snapped < 0 else ""
        upper_text = f"{sign}{hundreds}" if hundreds else sign

        painter.setFont(self._font_readout)
        painter.setPen(QPen(QColor(config.COLOR_WHITE)))
        painter.drawText(QRectF(bl + 1, mid - 12, dl - bl - 2, 24),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, upper_text)

        drum = QRectF(dl + 1, mid - 20, br - dl - 2, 40)
        sp = 17
        painter.setFont(self._font_readout_small)
        painter.save()
        painter.setClipRect(drum)
        for k in (-2, -1, 0, 1, 2):
            n = (base_i + k) * step
            cy = mid - (k - frac) * sp
            painter.drawText(QRectF(dl, cy - 10, br - dl, 20), Qt.AlignmentFlag.AlignCenter,
                             f"{abs(n) % 100:02d}")
        painter.restore()

    def _draw_compass_disk(self, painter: QPainter):
        """나침반 진회색 원판 + 바깥 원 (화면 아래로 잘려서 윗부분만 보임)."""
        cx, cy = config.HEADING_ARC_CENTER
        r = config.HEADING_ARC_RADIUS
        center = QPointF(cx, cy)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(config.COLOR_HEADING_BG)))
        painter.drawEllipse(center, r + 12, r + 12)
        painter.setPen(QPen(QColor(config.COLOR_TAPE_TICK), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, r, r)

    def _draw_heading_arc(self, painter: QPainter):
        """B737 NG 스타일 헤딩: 나침반 원판이 헤딩만큼 1:1로 회전, 위쪽 고정 포인터.
        눈금 5°(짧게)/10°(길게), 숫자는 10° 마다 두 자리(24=240°),
        0/90/180/270 은 N/E/S/W, 30° 단위는 조금 크게. 숫자는 원을 따라 기울임."""
        yaw = self.vm.attitude.yaw_deg
        cx, cy = config.HEADING_ARC_CENTER
        r = config.HEADING_ARC_RADIUS
        self._draw_compass_disk(painter)

        white = QColor(config.COLOR_WHITE)
        tick_pen = QPen(white, 1.5)
        label_r = r - 26
        for d in range(0, 360, 5):
            rel = ((d - yaw + 180) % 360) - 180          # 현재 헤딩 기준 상대각 (+ = 오른쪽)
            if abs(rel) > config.HEADING_VISIBLE_HALF_DEG:
                continue
            theta = math.radians(90 - rel)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            tick = 12 if d % 10 == 0 else 6
            painter.setPen(tick_pen)
            painter.drawLine(QPointF(cx + r * cos_t, cy - r * sin_t),
                             QPointF(cx + (r - tick) * cos_t, cy - (r - tick) * sin_t))
            if d % 10 == 0:
                label = {0: "N", 90: "E", 180: "S", 270: "W"}.get(d, str(d // 10))
                painter.save()
                painter.translate(cx + label_r * cos_t, cy - label_r * sin_t)
                painter.rotate(rel)
                painter.setFont(self._font_compass_big if d % 30 == 0 else self._font_compass)
                painter.setPen(QPen(white))
                painter.drawText(QRectF(-14, -9, 28, 18), Qt.AlignmentFlag.AlignCenter, label)
                painter.restore()

        # 고정 포인터 (흰 삼각형) + 현재 헤딩 박스
        top = cy - r
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(white))
        painter.drawPolygon(QPolygonF([
            QPointF(cx, top + 11), QPointF(cx - 7, top - 1), QPointF(cx + 7, top - 1),
        ]))
        box_rect = QRectF(cx - 27, top - 24, 54, 21)
        painter.setPen(QPen(white, 1.5))
        painter.setBrush(QBrush(QColor(config.COLOR_BLACK)))
        painter.drawRect(box_rect)
        painter.setFont(self._font_readout_heading)
        painter.setPen(QPen(white))
        painter.drawText(box_rect, Qt.AlignmentFlag.AlignCenter, f"{int(round(yaw)) % 360:03d}")
