"""
SysViewModel

오른쪽 차량 시스템 화면 상태를 보관합니다. CanMonitorThread.sys_values_updated
(항목 이름, {키: 값}) 를 받아 모델에 넣고, 항목별 마지막 수신 시각으로 유효성을 판단합니다.

유효 시간 = max(실제 수신 간격 x SYS_STALE_FACTOR, SYS_STALE_MIN_SEC).
실제 수신 간격은 설정 주기와 최근 수신 간격 평균 중 큰 값 - 어댑터가 느려서 설정보다
드물게 들어와도 값이 "--" 로 깜빡이지 않게 함. 한 번도 못 받았거나(미지원 포함)
유효 시간이 지나면 View 는 "--" 로 표시합니다.

조향각/브레이크/부하(SYS_SMOOTH_KEYS)는 받은 값 사이를 계기 바늘처럼 부드럽게 이어서 보여줌
(viewmodels/smooth_value.py, FRAME_RATE_HZ 타이머로 진행).

PFDViewModel 과 마찬가지로 View 에 신호를 보내지 않고, View 가 주기적으로 읽어갑니다.
"""

import time

from PyQt6.QtCore import QObject, QTimer

import config
from models.vehicle_sys_model import VehicleSysModel
from services.obd_decode import POLL_ITEMS
from viewmodels.smooth_value import SmoothValue

SYS_STALE_FACTOR = 3.0
SYS_STALE_MIN_SEC = 10.0
SYS_STALE_FIRST_SEC = 30.0   # 수신 간격을 아직 모를 때(두 번째 값이 오기 전) 유지 시간


class SysViewModel(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.sys = VehicleSysModel()
        self._key_time = {}    # 값 키 -> 마지막 수신 시각
        self._key_gap = {}     # 값 키 -> 수신 간격 평균 (초)
        self._key_item = {}    # 값 키 -> 항목 이름
        self._speed = None     # (km/h, 수신 시각) - 기어 표시용 (주행 중이면 D)
        bounds = {"brake_pct": (0.0, 100.0), "load_pct": (0.0, 100.0)}
        self._smooth = {k: SmoothValue(tau=config.SYS_SMOOTH_TAU_SEC, gain=config.SYS_SMOOTH_GAIN,
                                       horizon=config.SYS_SMOOTH_PREDICT_MAX_SEC,
                                       lo=bounds.get(k, (None, None))[0], hi=bounds.get(k, (None, None))[1])
                        for k in config.SYS_SMOOTH_KEYS}
        if self._smooth:
            self._smooth_timer = QTimer(self)
            self._smooth_timer.timeout.connect(self.tick)
            self._smooth_timer.start(int(1000 / config.FRAME_RATE_HZ))

    def tick(self, now=None):
        """부드럽게 표시하는 값들을 한 프레임 진행 (타이머가 호출)"""
        now = time.monotonic() if now is None else now
        for smoother in self._smooth.values():
            smoother.tick(now)

    # ---- services.can_reader.CanMonitorThread.sys_values_updated 에 연결 ----
    def on_sys_values(self, item: str, values: dict):
        """None(해석 못 한 값)은 기존 값을 지우지 않음 - 값마다 따로 수신 시각을 기록해서
        오래된 값은 유효 시간으로 걸러냄"""
        now = time.monotonic()
        for key, value in values.items():
            if key == "tires":
                good = {t: v for t, v in value.items() if v[0] is not None or v[1] is not None}
                if not good:
                    continue
                merged = dict(self.sys.tires)
                merged.update(good)
                value = merged
            elif value is None:
                continue
            smoother = self._smooth.get(key)
            if smoother is not None:          # 끊겼다 다시 오면(유효하지 않았으면) 바로 그 값으로
                smoother.on_sample(value, now, reset=not self.valid(key))
            setattr(self.sys, key, value)
            self._key_item[key] = item
            last = self._key_time.get(key)
            if last is not None:
                gap = now - last
                prev = self._key_gap.get(key)
                self._key_gap[key] = gap if prev is None else prev + 0.3 * (gap - prev)
            self._key_time[key] = now

    # ---- CanMonitorThread.speed_updated 에 연결 ----
    def on_speed_updated(self, kph: float):
        self._speed = (kph, time.monotonic())

    @property
    def gear_text(self):
        """기어 표시: 기어 값이 있으면 숫자, 없으면 후진 R / 주행 중(3km/h 이상) D / 그 외 None('-').
        (국내형 CN7 은 변속기 ECU 가 기어 요청을 거절해서 후진 비트 + 속도로 판단)"""
        gear = self.value("gear")
        reverse = self.value("reverse")
        if reverse:
            return "R"
        if gear is not None:
            return str(gear)
        moving = (self._speed is not None and time.monotonic() - self._speed[1] <= config.SPEED_STALE_SEC
                  and self._speed[0] >= 3)
        if reverse is False and moving:
            return "D"
        return None

    def valid(self, key: str) -> bool:
        """값이 있고 유효 시간 안에 받은 것인지"""
        item = self._key_item.get(key)
        if item is None or item not in POLL_ITEMS or getattr(self.sys, key, None) in (None, {}):
            return False
        interval = max(config.OBD_SYS_INTERVALS.get(item, 1.0), self._key_gap.get(key, 0.0))
        limit = max(interval * SYS_STALE_FACTOR, SYS_STALE_MIN_SEC)
        if key not in self._key_gap:
            limit = max(limit, SYS_STALE_FIRST_SEC)
        return time.monotonic() - self._key_time[key] <= limit

    def value(self, key: str):
        """유효하면 값, 아니면 None (View 는 None 을 '--' 로 표시). 부드럽게 표시하는 값은 표시값."""
        if not self.valid(key):
            return None
        smoother = self._smooth.get(key)
        if smoother is not None and smoother.value is not None:
            return smoother.value
        return getattr(self.sys, key)
