"""
NDViewModel

ND(오른쪽 화면)에 필요한 상태를 보관합니다. 지금은 헤딩만 씁니다.
(카메라 레이더 기능은 삭제됨 — 나중에 ADS-B 등 표시 대상이 생기면 여기에 추가)

주의: PFDViewModel과 마찬가지로 View에 즉시 repaint 신호를 보내지 않습니다.
View가 FRAME_RATE_HZ 주기 타이머로 스스로 최신 상태를 읽어가서 그립니다.
"""

import time

from PyQt6.QtCore import QObject

import config


class NDViewModel(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.heading_deg = 0.0
        self._heading_time = None   # 마지막 헤딩 수신 시각 (None = 아직 못 받음)

    # ---- services.imu_reader.ImuReaderThread.attitude_updated 에 연결 (yaw만 사용) ----
    def on_heading_updated(self, yaw_deg: float):
        self.heading_deg = (yaw_deg + config.HEADING_OFFSET_DEG) % 360.0   # 헤딩 오프셋 적용
        self._heading_time = time.monotonic()

    @property
    def heading_valid(self) -> bool:
        """IMU 헤딩이 IMU_STALE_SEC 이내에 들어왔는지 (False면 헤딩 박스에 HDG 표시)"""
        return (self._heading_time is not None
                and time.monotonic() - self._heading_time <= config.IMU_STALE_SEC)
