"""
NDViewModel

services(IMU/LiDAR Reader 스레드)의 시그널을 받는 슬롯들을 제공하고, 내부
Model을 갱신합니다.

주의: PFDViewModel과 마찬가지로 View에 즉시 repaint 신호를 보내지 않습니다.
View가 FRAME_RATE_HZ 주기 타이머로 스스로 최신 상태를 읽어가서 그립니다.
"""

import time

from PyQt6.QtCore import QObject

import config
from models.scan_model import ScanModel


class NDViewModel(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.heading_deg = 0.0
        self._heading_time = None   # 마지막 헤딩 수신 시각 (None = 아직 못 받음)
        self.scan = ScanModel()
        # "disconnected" / "camera_error" / "connected" - 초기값은 아직
        # 한 번도 연결 시도 결과를 못 받은 상태이므로 "disconnected"로 시작
        self.connection_status = "disconnected"
        # 추론 모델 준비 상태: "loading" / "ready" / "failed"
        # 시작하자마자 모델 로딩부터 하므로 "loading"으로 시작
        self.model_state = "loading"
        self.model_lines = []          # [{"text", "timer", "seconds"}, ...]
        self.model_status_time = time.monotonic()   # 마지막 상태 수신 시각 (경과/카운트다운 기준)

    # ---- services.imu_reader.ImuReaderThread.attitude_updated 에 연결 (yaw만 사용) ----
    def on_heading_updated(self, yaw_deg: float):
        self.heading_deg = (yaw_deg + config.HEADING_OFFSET_DEG) % 360.0   # 헤딩 오프셋 적용
        self._heading_time = time.monotonic()

    # ---- services.camera_depth_reader.CameraDepthReaderThread.scan_updated 에 연결 ----
    # (예전 services.lidar_reader.LidarReaderThread.scan_updated 와 완전히 같은 인터페이스)
    def on_scan_updated(self, points: list):
        """points: [(angle_deg, distance_m), ...] - 물체 하나당 하나씩"""
        self.scan.points = points

    # ---- services.camera_depth_reader.CameraDepthReaderThread.status_updated 에 연결 ----
    def on_status_updated(self, status: str):
        self.connection_status = status

    # ---- services.camera_depth_reader.CameraDepthReaderThread.model_status_updated 에 연결 ----
    def on_model_status_updated(self, state: str, lines: list):
        self.model_state = state
        self.model_lines = lines
        self.model_status_time = time.monotonic()

    @property
    def heading_valid(self) -> bool:
        """IMU 헤딩이 IMU_STALE_SEC 이내에 들어왔는지 (False면 헤딩 박스에 HDG 표시)"""
        return (self._heading_time is not None
                and time.monotonic() - self._heading_time <= config.IMU_STALE_SEC)
