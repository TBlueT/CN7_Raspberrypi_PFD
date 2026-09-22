"""
NDViewModel

services(IMU/LiDAR Reader 스레드)의 시그널을 받는 슬롯들을 제공하고, 내부
Model을 갱신합니다.

주의: PFDViewModel과 마찬가지로 View에 즉시 repaint 신호를 보내지 않습니다.
View가 FRAME_RATE_HZ 주기 타이머로 스스로 최신 상태를 읽어가서 그립니다.
"""

from PyQt6.QtCore import QObject

from models.scan_model import ScanModel


class NDViewModel(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.heading_deg = 0.0
        self.scan = ScanModel()
        # "disconnected" / "camera_error" / "connected" - 초기값은 아직
        # 한 번도 연결 시도 결과를 못 받은 상태이므로 "disconnected"로 시작
        self.connection_status = "disconnected"

    # ---- services.imu_reader.ImuReaderThread.attitude_updated 에 연결 (yaw만 사용) ----
    def on_heading_updated(self, yaw_deg: float):
        self.heading_deg = yaw_deg % 360.0

    # ---- services.camera_depth_reader.CameraDepthReaderThread.scan_updated 에 연결 ----
    # (예전 services.lidar_reader.LidarReaderThread.scan_updated 와 완전히 같은 인터페이스)
    def on_scan_updated(self, points: list):
        """points: [(angle_deg, distance_m), ...] - 물체 하나당 하나씩"""
        self.scan.points = points

    # ---- services.camera_depth_reader.CameraDepthReaderThread.status_updated 에 연결 ----
    def on_status_updated(self, status: str):
        self.connection_status = status
