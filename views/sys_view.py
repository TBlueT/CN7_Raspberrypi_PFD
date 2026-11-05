"""
SysView (MVVM의 View) - 오른쪽 차량 시스템 화면 (B737/787 EICAS 시놉틱 스타일)

  위:   SAT(외기 온도)                         BATT(배터리 전압)
        엔진 원형 계기 3개: LOAD % / COOLANT °C / OIL T °C
  가운데: 차 평면도 - 타이어 공기압(psi)/온도, 앞문 잠금(자물쇠), 기어
  아래:  BRAKE(막대) / STEER(조향각 눈금) / FUEL(막대)

색: 이름 = 시안, 값 = 흰색, 주의 = 주황, 경고 = 빨강, 데이터 없음 = 회색 "--".
경고 기준은 config.py 의 SYS_* 값.

움직이지 않는 부분(이름, 눈금, 차 외곽선, 계기 호)은 한 번만 그려서 캐싱하고
매 프레임은 값만 그립니다.
"""

import math

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath, QPixmap, QPolygonF
from PyQt6.QtWidgets import QWidget

import config
from viewmodels.sys_viewmodel import SysViewModel

_AC = Qt.AlignmentFlag.AlignCenter
_AL = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_AR = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

# ---- 배치 (400x480 패널 기준) ----
_DIALS = (   # (키, 중심 x, 이름, 범위 config 또는 (lo, hi), 주황 config, 빨강 config)
    ("load_pct", 66, "LOAD %", (0, 100), "SYS_LOAD_AMBER_PCT", None),
    ("coolant_c", 196, "COOLANT °C", "SYS_COOLANT_RANGE_C", "SYS_COOLANT_AMBER_C", "SYS_COOLANT_RED_C"),
    ("oil_c", 326, "OIL T °C", "SYS_OIL_RANGE_C", "SYS_OIL_AMBER_C", "SYS_OIL_RED_C"),
)
_DIAL_CY, _DIAL_R = 74, 28
_DIAL_A0, _DIAL_SPAN = 210.0, 240.0          # 호 시작 각도(왼쪽 아래), 시계방향으로 240도

_CAR_CX, _CAR_TOP, _CAR_W, _CAR_H = 200, 128, 100, 206
_FRONT_DOOR = (_CAR_TOP + 64, _CAR_TOP + 110)
_REAR_DOOR = (_CAR_TOP + 114, _CAR_TOP + 154)
_TIRE_Y = {"FL": _CAR_TOP + 30, "FR": _CAR_TOP + 30, "RL": _CAR_TOP + 160, "RR": _CAR_TOP + 160}
_GEAR_BOX = QRectF(_CAR_CX - 20, _CAR_TOP + 88, 40, 34)

_BOTTOM_Y = 360
_BAR_H = 70
_BRAKE_BAR = QRectF(46, _BOTTOM_Y + 10, 16, _BAR_H)
_FUEL_BAR = QRectF(356, _BOTTOM_Y + 10, 16, _BAR_H)
_STEER_X0, _STEER_X1, _STEER_Y = 104, 296, _BOTTOM_Y + 66


def _cfg(name):
    return getattr(config, name) if isinstance(name, str) else name


class SysView(QWidget):
    def __init__(self, viewmodel: SysViewModel, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFixedSize(config.PANEL_WIDTH, config.PANEL_HEIGHT)
        self.vm = viewmodel

        self._white = QColor(config.COLOR_WHITE)
        self._label = QColor(config.COLOR_SYS_LABEL)
        self._amber = QColor(config.COLOR_SYS_AMBER)
        self._red = QColor(config.COLOR_SYS_RED)
        self._pointer = QColor(config.COLOR_SYS_POINTER)
        self._nodata = QColor(config.COLOR_SYS_NODATA)
        self._gray = QColor("#5a5a5a")
        self._dark = QColor("#2a2a2a")
        self._black = QColor(config.COLOR_BLACK)

        bold = QFont.Weight.Bold
        self._f7 = QFont("sans-serif", 7, bold)
        self._f7n = QFont("sans-serif", 7)
        self._f8 = QFont("sans-serif", 8, bold)
        self._f8n = QFont("sans-serif", 8)
        self._f9 = QFont("sans-serif", 9, bold)
        self._f10 = QFont("sans-serif", 10, bold)
        self._f11 = QFont("sans-serif", 11, bold)
        self._f13 = QFont("sans-serif", 13, bold)
        self._f16 = QFont("sans-serif", 16, bold)
        self._f20 = QFont("sans-serif", 20, bold)

        self._background = self._render_background()

        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self.update)
        self._render_timer.start(int(1000 / config.FRAME_RATE_HZ))

    # ------------------------------------------------------------------
    # 공용 그리기
    # ------------------------------------------------------------------
    @staticmethod
    def _text(p: QPainter, rect, s, color, font, align=_AC):
        p.setFont(font)
        p.setPen(QPen(color))
        p.drawText(QRectF(*rect) if not isinstance(rect, QRectF) else rect, align, s)

    def _readout(self, p, cx, y, w, s, color, font=None):
        r = QRectF(cx - w / 2, y, w, 24)
        p.setPen(QPen(self._white, 1.2))
        p.setBrush(self._black)
        p.drawRect(r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        self._text(p, r, s, color, font or self._f11)

    def _level_color(self, value, amber, red=None, low=None):
        """값 -> 흰색/주황/빨강 (None 기준은 무시)"""
        if red is not None and value >= red:
            return self._red
        if (amber is not None and value >= amber) or (low is not None and value < low):
            return self._amber
        return self._white

    @staticmethod
    def _dial_angle(v, lo, hi):
        return _DIAL_A0 - _DIAL_SPAN * (max(lo, min(hi, v)) - lo) / (hi - lo)

    @staticmethod
    def _dial_point(cx, a, r):
        return QPointF(cx + r * math.cos(math.radians(a)), _DIAL_CY - r * math.sin(math.radians(a)))

    # ------------------------------------------------------------------
    # 정적 배경 (한 번만)
    # ------------------------------------------------------------------
    def _render_background(self) -> QPixmap:
        pm = QPixmap(config.PANEL_WIDTH, config.PANEL_HEIGHT)
        pm.fill(QColor(config.COLOR_SYS_BG))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        self._text(p, (8, 2, 40, 16), "SAT", self._label, self._f9, _AL)
        self._text(p, (262, 2, 80, 16), "BATT", self._label, self._f9, _AR)

        # 엔진 원형 계기: 호(흰/주황), 빨강 한계 눈금, 눈금, 이름
        for key, cx, label, rng, amber_name, red_name in _DIALS:
            lo, hi = _cfg(rng)
            amber = _cfg(amber_name) if amber_name else None
            red = _cfg(red_name) if red_name else None
            r = _DIAL_R
            rect = QRectF(cx - r, _DIAL_CY - r, 2 * r, 2 * r)
            white_end = amber if amber is not None else (red if red is not None else hi)
            a_end = self._dial_angle(white_end, lo, hi)
            p.setPen(QPen(self._white, 2))
            p.drawArc(rect, int(_DIAL_A0 * 16), int((a_end - _DIAL_A0) * 16))
            if amber is not None:
                a_amber = self._dial_angle(amber, lo, hi)
                a_stop = self._dial_angle(red if red is not None else hi, lo, hi)
                p.setPen(QPen(self._amber, 2.4))
                p.drawArc(rect, int(a_amber * 16), int((a_stop - a_amber) * 16))
            if red is not None:
                a_red = self._dial_angle(red, lo, hi)
                p.setPen(QPen(self._red, 2.6))
                p.drawLine(self._dial_point(cx, a_red, r - 6), self._dial_point(cx, a_red, r + 5))
            p.setPen(QPen(self._white, 1.2))
            for tv in (lo, (lo + hi) / 2, hi):
                a = self._dial_angle(tv, lo, hi)
                p.drawLine(self._dial_point(cx, a, r - 5), self._dial_point(cx, a, r))
            self._text(p, (cx - 50, _DIAL_CY + r - 2, 100, 14), label, self._label, self._f8)
        p.setPen(QPen(self._dark, 1))
        p.drawLine(QPointF(10, 118), QPointF(390, 118))

        # 차 평면도
        cx, top = _CAR_CX, _CAR_TOP
        body = QPainterPath()
        body.addRoundedRect(QRectF(cx - _CAR_W / 2, top, _CAR_W, _CAR_H), 34, 26)
        p.setPen(QPen(self._white, 1.6))
        p.drawPath(body)
        glass = QPainterPath()
        glass.moveTo(cx - 38, top + 58)
        glass.quadTo(cx, top + 44, cx + 38, top + 58)
        glass.lineTo(cx + 34, top + 150)
        glass.quadTo(cx, top + 160, cx - 34, top + 150)
        glass.closeSubpath()
        p.setPen(QPen(self._gray, 1.2))
        p.drawPath(glass)
        self._text(p, (cx - 30, top + 6, 60, 14), "FRONT", self._gray, self._f7)
        self._text(p, (cx - 30, top + 72, 60, 14), "GEAR", self._label, self._f7)
        self._text(p, (18, top + _CAR_H / 2 - 8, 100, 14), "TIRE  PSI", self._label, self._f8, _AR)
        p.setPen(QPen(self._dark, 1))
        p.drawLine(QPointF(10, _BOTTOM_Y - 14), QPointF(390, _BOTTOM_Y - 14))

        # 아래: 브레이크/연료 막대 테두리와 눈금, 조향각 눈금
        self._text(p, (8, _BOTTOM_Y - 8, 70, 14), "BRAKE", self._label, self._f8)
        self._text(p, (150, _BOTTOM_Y - 8, 100, 14), "STEER", self._label, self._f8)
        tank = config.SYS_FUEL_TANK_L
        for bar, ticks in ((_BRAKE_BAR, ((0, "0"), (0.5, "50"), (1, "100"))),
                           (_FUEL_BAR, ((0, "0"), (0.5, f"{tank / 2:.0f}"), (1, f"{tank:.0f}")))):
            p.setPen(QPen(self._white, 1.2))
            p.drawRect(bar)
            p.setFont(self._f7n)
            for f, lab in ticks:
                y = bar.bottom() - bar.height() * f
                p.drawLine(QPointF(bar.left() - 5, y), QPointF(bar.left(), y))
                p.drawText(QRectF(bar.left() - 34, y - 7, 26, 14), _AR, lab)

        rng = config.SYS_STEER_RANGE_DEG
        ppd = (_STEER_X1 - _STEER_X0) / (2 * rng)
        p.setPen(QPen(self._white, 1.4))
        p.drawLine(QPointF(_STEER_X0, _STEER_Y), QPointF(_STEER_X1, _STEER_Y))
        p.setFont(self._f7n)
        step = 90
        for a in range(-int(rng // step) * step, int(rng) + 1, step):
            x = 200 + a * ppd
            major = a % (2 * step) == 0
            p.drawLine(QPointF(x, _STEER_Y), QPointF(x, _STEER_Y + (9 if major else 5)))
            if major:
                p.drawText(QRectF(x - 14, _STEER_Y + 10, 28, 12), _AC, str(abs(a)))
        self._text(p, (_STEER_X0 - 14, _STEER_Y - 6, 12, 12), "L", self._white, self._f8)
        self._text(p, (_STEER_X1 + 2, _STEER_Y - 6, 12, 12), "R", self._white, self._f8)
        p.end()
        return pm

    # ------------------------------------------------------------------
    # 매 프레임
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.drawPixmap(0, 0, self._background)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_top(p)
        for dial in _DIALS:
            self._draw_dial(p, *dial)
        self._draw_car(p)
        self._draw_brake(p)
        self._draw_fuel(p)
        self._draw_steer(p)
        p.end()

    def _draw_top(self, p):
        sat = self.vm.value("sat_c")
        self._text(p, (38, 2, 70, 16), "--" if sat is None else f"{sat:+.0f}°C",
                   self._nodata if sat is None else self._white, self._f10, _AL)
        bv = self.vm.value("batt_v")
        if bv is None:
            color, s = self._nodata, "--"
        else:
            color = self._level_color(bv, None, low=config.SYS_BATT_LOW_V)
            if config.SYS_BATT_HIGH_V is not None and bv > config.SYS_BATT_HIGH_V:
                color = self._amber
            s = f"{bv:.1f}V"
        self._text(p, (330, 2, 62, 16), s, color, self._f10, _AR)

    def _draw_dial(self, p, key, cx, label, rng, amber_name, red_name):
        lo, hi = _cfg(rng)
        value = self.vm.value(key)
        box = QRectF(cx + 2, _DIAL_CY - _DIAL_R - 24, 48, 20)
        p.setPen(QPen(self._white, 1.1))
        p.setBrush(self._black)
        p.drawRect(box)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if value is None:
            self._text(p, QRectF(box.x(), box.y(), box.width() - 4, box.height()), "--", self._nodata, self._f11, _AR)
            return
        color = self._level_color(value, _cfg(amber_name) if amber_name else None,
                                  _cfg(red_name) if red_name else None)
        r = _DIAL_R
        a = self._dial_angle(value, lo, hi)
        wedge = QPainterPath(QPointF(cx, _DIAL_CY))
        wedge.arcTo(QRectF(cx - r, _DIAL_CY - r, 2 * r, 2 * r), _DIAL_A0, a - _DIAL_A0)
        wedge.closeSubpath()
        fill = QColor("#3c3c3c") if color == self._white else QColor(color.red(), color.green(), color.blue(), 90)
        p.fillPath(wedge, fill)
        p.setPen(QPen(color, 2.4))
        p.drawLine(QPointF(cx, _DIAL_CY), self._dial_point(cx, a, r - 2))
        self._text(p, QRectF(box.x(), box.y(), box.width() - 4, box.height()), f"{value:.0f}", color, self._f11, _AR)

    def _padlock(self, p, cx, cy, locked):
        c = self._white if locked else self._amber
        p.fillRect(QRectF(cx - 6, cy - 1, 12, 9), c)
        p.setPen(QPen(c, 1.6))
        path = QPainterPath()
        if locked:      # 고리가 몸통에 끼워진 모양
            path.moveTo(cx - 4, cy - 1)
            path.lineTo(cx - 4, cy - 5)
            path.arcTo(QRectF(cx - 4, cy - 9, 8, 8), 180, -180)
            path.lineTo(cx + 4, cy - 1)
        else:           # 고리가 들려서 오른쪽 다리가 빠진 모양
            path.moveTo(cx - 4, cy - 1)
            path.lineTo(cx - 4, cy - 9)
            path.arcTo(QRectF(cx - 4, cy - 13, 8, 8), 180, -180)
            path.lineTo(cx + 4, cy - 6)
        p.drawPath(path)

    def _draw_car(self, p):
        cx, top, half = _CAR_CX, _CAR_TOP, _CAR_W / 2

        # 앞문 잠금 (뒷문은 데이터 없음 -> 회색)
        locks = (self.vm.value("lock_fl"), self.vm.value("lock_fr"))
        for side, locked in ((-1, locks[0]), (1, locks[1])):
            x = cx + side * half
            color = self._gray if locked is None else (self._white if locked else self._amber)
            p.setPen(QPen(color, 4))
            p.drawLine(QPointF(x, _FRONT_DOOR[0]), QPointF(x, _FRONT_DOOR[1]))
            p.setPen(QPen(self._gray, 4))
            p.drawLine(QPointF(x, _REAR_DOOR[0]), QPointF(x, _REAR_DOOR[1]))
            if locked is not None:
                self._padlock(p, cx + side * 31, _FRONT_DOOR[0] + 12, locked)
        known = [v for v in locks if v is not None]
        if known:
            all_locked = all(known)
            self._text(p, (cx - 40, top + 160, 80, 14), "LOCKED" if all_locked else "UNLOCKED",
                       self._white if all_locked else self._amber, self._f8n if all_locked else self._f8)

        # 기어 (후진이면 R 주황)
        reverse = self.vm.value("reverse")
        gear = self.vm.value("gear")
        if reverse:
            s, color = "R", self._amber
        elif gear is not None:
            s, color = str(gear), self._white
        else:
            s, color = "-", self._nodata
        p.setPen(QPen(self._white, 1.4))
        p.setBrush(self._black)
        p.drawRect(_GEAR_BOX)
        p.setBrush(Qt.BrushStyle.NoBrush)
        self._text(p, _GEAR_BOX, s, color, self._f20)

        # 타이어 + 공기압/온도
        tires = self.vm.value("tires") or {}
        for name, ty in _TIRE_Y.items():
            side = -1 if name.endswith("L") else 1
            pressure, temp, warn = tires.get(name, (None, None, False))
            low = warn or (pressure is not None and pressure < config.SYS_TIRE_LOW_PSI)
            tx = cx + side * (half + 4) - (14 if side < 0 else 0)
            outline = self._gray if pressure is None and not warn else (self._amber if low else self._white)
            p.setPen(QPen(outline, 1.4))
            p.setBrush(self._amber if low else self._black)
            p.drawRoundedRect(QRectF(tx, ty - 2, 14, 38), 3, 3)
            p.setBrush(Qt.BrushStyle.NoBrush)
            bx = 18 if side < 0 else 282
            align = _AR if side < 0 else _AL
            self._text(p, (bx, ty - 6, 100, 24), "--" if pressure is None else f"{pressure:.1f}",
                       self._nodata if pressure is None else (self._amber if low else self._white), self._f16, align)
            self._text(p, (bx, ty + 16, 100, 14), "--" if temp is None else f"{temp:.0f}°C",
                       self._nodata if temp is None else self._label, self._f9, align)

    def _bar_fill(self, p, bar, frac, color):
        h = (bar.height() - 4) * max(0.0, min(1.0, frac))
        if h > 0:
            p.fillRect(QRectF(bar.left() + 2, bar.bottom() - 2 - h, bar.width() - 4, h), color)

    def _draw_brake(self, p):
        bp = self.vm.value("brake_pct")
        if bp is None:
            self._readout(p, 54, _BOTTOM_Y + 86, 52, "--", self._nodata)
            return
        bp = max(0.0, bp)
        self._bar_fill(p, _BRAKE_BAR, bp / 100.0, self._white)
        self._readout(p, 54, _BOTTOM_Y + 86, 52, f"{bp:.0f}%", self._white)

    def _draw_fuel(self, p):
        fl = self.vm.value("fuel_l")
        low = fl is not None and config.SYS_FUEL_LOW_L is not None and fl < config.SYS_FUEL_LOW_L
        self._text(p, (300, _BOTTOM_Y - 8, 110, 14), "LOW FUEL" if low else "FUEL",
                   self._amber if low else self._label, self._f8)
        if fl is None:
            self._readout(p, 364, _BOTTOM_Y + 86, 58, "--", self._nodata)
            return
        color = self._amber if low else self._white
        self._bar_fill(p, _FUEL_BAR, fl / config.SYS_FUEL_TANK_L, color)
        self._readout(p, 364, _BOTTOM_Y + 86, 58, f"{fl:.1f}L", color)

    def _draw_steer(self, p):
        st = self.vm.value("steer_deg")
        if st is None:
            self._readout(p, 200, _BOTTOM_Y + 14, 78, "--", self._nodata, self._f13)
            return
        rng = config.SYS_STEER_RANGE_DEG
        x = 200 + max(-rng, min(rng, st)) * (_STEER_X1 - _STEER_X0) / (2 * rng)
        tri = QPolygonF([QPointF(x, _STEER_Y - 2), QPointF(x - 7, _STEER_Y - 14), QPointF(x + 7, _STEER_Y - 14)])
        p.setPen(QPen(self._pointer, 1))
        p.setBrush(self._pointer)
        p.drawPolygon(tri)
        p.setBrush(Qt.BrushStyle.NoBrush)
        side = "L " if st < -0.5 else ("R " if st > 0.5 else "")
        self._readout(p, 200, _BOTTOM_Y + 14, 78, f"{side}{abs(st):.0f}°", self._white, self._f13)
