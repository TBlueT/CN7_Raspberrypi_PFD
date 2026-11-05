"""
SysViewModel

오른쪽 차량 시스템 화면 상태를 보관합니다. CanMonitorThread.sys_values_updated
(항목 이름, {키: 값}) 를 받아 모델에 넣고, 항목별 마지막 수신 시각으로 유효성을 판단합니다.

유효 시간 = max(OBD 요청 주기 x SYS_STALE_FACTOR, SYS_STALE_MIN_SEC).
한 번도 못 받았거나(미지원 포함) 유효 시간이 지나면 View 는 "--" 로 표시합니다.

PFDViewModel 과 마찬가지로 View 에 신호를 보내지 않고, View 가 주기적으로 읽어갑니다.
"""

import time

from PyQt6.QtCore import QObject

import config
from models.vehicle_sys_model import VehicleSysModel
from services.obd_decode import POLL_ITEMS

SYS_STALE_FACTOR = 4.0
SYS_STALE_MIN_SEC = 5.0


class SysViewModel(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.sys = VehicleSysModel()
        self._item_time = {}   # 항목 이름 -> 마지막 수신 시각
        self._key_item = {}    # 값 키 -> 항목 이름

    # ---- services.can_reader.CanMonitorThread.sys_values_updated 에 연결 ----
    def on_sys_values(self, item: str, values: dict):
        now = time.monotonic()
        for key, value in values.items():
            if key == "tires":
                merged = dict(self.sys.tires)
                merged.update(value)
                value = merged
            setattr(self.sys, key, value)
            self._key_item[key] = item
        self._item_time[item] = now

    def valid(self, key: str) -> bool:
        """값이 있고 유효 시간 안에 받은 것인지"""
        item = self._key_item.get(key)
        if item is None or item not in POLL_ITEMS or getattr(self.sys, key, None) in (None, {}):
            return False
        interval = config.OBD_SYS_INTERVALS.get(item, 1.0)
        limit = max(interval * SYS_STALE_FACTOR, SYS_STALE_MIN_SEC)
        return time.monotonic() - self._item_time[item] <= limit

    def value(self, key: str):
        """유효하면 값, 아니면 None (View 는 None 을 '--' 로 표시)"""
        return getattr(self.sys, key) if self.valid(key) else None
