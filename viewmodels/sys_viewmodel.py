"""
SysViewModel

오른쪽 차량 시스템 화면 상태를 보관합니다. CanMonitorThread.sys_values_updated
(항목 이름, {키: 값}) 를 받아 모델에 넣고, 항목별 마지막 수신 시각으로 유효성을 판단합니다.

유효 시간 = max(실제 수신 간격 x SYS_STALE_FACTOR, SYS_STALE_MIN_SEC).
실제 수신 간격은 설정 주기와 최근 수신 간격 평균 중 큰 값 - 어댑터가 느려서 설정보다
드물게 들어와도 값이 "--" 로 깜빡이지 않게 함. 한 번도 못 받았거나(미지원 포함)
유효 시간이 지나면 View 는 "--" 로 표시합니다.

PFDViewModel 과 마찬가지로 View 에 신호를 보내지 않고, View 가 주기적으로 읽어갑니다.
"""

import time

from PyQt6.QtCore import QObject

import config
from models.vehicle_sys_model import VehicleSysModel
from services.obd_decode import POLL_ITEMS

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
            setattr(self.sys, key, value)
            self._key_item[key] = item
            last = self._key_time.get(key)
            if last is not None:
                gap = now - last
                prev = self._key_gap.get(key)
                self._key_gap[key] = gap if prev is None else prev + 0.3 * (gap - prev)
            self._key_time[key] = now

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
        """유효하면 값, 아니면 None (View 는 None 을 '--' 로 표시)"""
        return getattr(self.sys, key) if self.valid(key) else None
