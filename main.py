"""
PFD 애플리케이션 진입점 (MVVM 구조)

데이터 흐름: services(Reader 스레드) -> viewmodels(상태+신호) -> views(렌더링)
main.py는 이 셋을 만들고 서로 연결(wiring)하는 역할만 합니다.

기존에 쓰시던 linuxfb 환경변수 세팅 그대로 사용하시면 됩니다:

    export QT_QPA_PLATFORM=linuxfb:fb=/dev/fb0:size=800x480
    export QT_QPA_FB_TSLIB=1
    export TSLIB_FBDEVICE=/dev/fb0
    export TSLIB_TSDEVICE=/dev/input/event0
    python3 main.py
"""

import sys

from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QHBoxLayout
from PyQt6.QtCore import Qt

import config
from viewmodels.pfd_viewmodel import PFDViewModel
from viewmodels.nd_viewmodel import NDViewModel
from views.pfd_view import PFDView
from views.nd_view import NDView
from services.imu_reader import ImuReaderThread
from services.can_reader import CanMonitorThread
from services.lidar_reader import LidarReaderThread


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PFD")
        self.setFixedSize(config.SCREEN_WIDTH, config.SCREEN_HEIGHT)
        self.setCursor(Qt.CursorShape.BlankCursor)

        # ---- ViewModel ----
        self.pfd_vm = PFDViewModel()
        self.nd_vm = NDViewModel()

        # ---- View ----
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.pfd_view = PFDView(self.pfd_vm, container)
        self.nd_view = NDView(self.nd_vm, container)
        layout.addWidget(self.pfd_view)
        layout.addWidget(self.nd_view)

        self.setCentralWidget(container)

        # ---- Service(Reader 스레드) -> ViewModel 연결 ----
        self.imu_thread = ImuReaderThread()
        self.imu_thread.attitude_updated.connect(self.pfd_vm.on_attitude_updated)
        self.imu_thread.linear_accel_updated.connect(self.pfd_vm.on_linear_accel_updated)
        self.imu_thread.feature_check_updated.connect(self.pfd_vm.on_imu_feature_check)
        self.imu_thread.attitude_updated.connect(
            lambda roll, pitch, yaw: self.nd_vm.on_heading_updated(yaw))
        self.imu_thread.connection_error.connect(self._on_imu_error)
        self.imu_thread.start()

        self.can_thread = CanMonitorThread()
        self.can_thread.speed_updated.connect(self.pfd_vm.on_speed_updated)
        self.can_thread.scc_status_updated.connect(self.pfd_vm.on_scc_status_updated)
        self.can_thread.lfa_status_updated.connect(self.pfd_vm.on_lfa_status_updated)
        self.can_thread.hda_curve_updated.connect(self.pfd_vm.on_hda_curve_updated)
        self.can_thread.connection_error.connect(self._on_can_error)
        self.can_thread.start()

        self.lidar_thread = LidarReaderThread()
        self.lidar_thread.scan_updated.connect(self.nd_vm.on_scan_updated)
        self.lidar_thread.connection_error.connect(self._on_lidar_error)
        self.lidar_thread.start()

    # ---- 에러 로깅 (콘솔 출력만 우선 처리) ----
    def _on_imu_error(self, message: str):
        print(f"[IMU] {message}", file=sys.stderr)

    def _on_can_error(self, message: str):
        print(f"[CAN] {message}", file=sys.stderr)

    def _on_lidar_error(self, message: str):
        print(f"[LIDAR] {message}", file=sys.stderr)

    def closeEvent(self, event):
        self.imu_thread.stop()
        self.can_thread.stop()
        self.lidar_thread.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    # window.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
