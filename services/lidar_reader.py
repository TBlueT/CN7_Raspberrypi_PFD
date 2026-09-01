"""
YDLIDAR G2 리더 스레드

공식 YDLidar-SDK의 Python 바인딩(ydlidar 모듈)을 사용합니다.
SDK가 설치되어 있어야 합니다 (README 참고: cmake -DBUILD_PYTHON=ON 빌드 필요).

G2 사양(YDLidar 공식 문서 기준):
- Baudrate: 230400
- LidarType: TYPE_TRIANGLE (삼각측량 방식)
- DeviceType: YDLIDAR_TYPE_SERIAL
- SampleRate: 5
- SingleChannel: False

주의: ydlidar 모듈의 정확한 property 이름/열거형 값은 SDK 버전에 따라 조금씩
다를 수 있습니다. 설치 후 `YDLidar-SDK/python/examples/ydlidar_test.py`를
한번 열어서 이 코드의 setlidaropt 호출들과 실제 값이 일치하는지 대조해보시는
걸 추천합니다 (버전 차이로 상수 이름이 다르면 여기서 ImportError/AttributeError
가 날 수 있음).
"""

from PyQt6.QtCore import QThread, pyqtSignal

import config

try:
    import ydlidar
except ImportError:
    ydlidar = None


class LidarReaderThread(QThread):
    # points: list of (angle_deg, distance_m) 튜플
    scan_updated = pyqtSignal(list)
    connection_error = pyqtSignal(str)

    def __init__(self, port=None, parent=None):
        super().__init__(parent)
        self._port = port or getattr(config, "LIDAR_SERIAL_PORT", "/dev/ttyUSB0")
        self._running = False
        self._laser = None

    def run(self):
        if ydlidar is None:
            self.connection_error.emit(
                "ydlidar 모듈을 못 찾았습니다. YDLidar-SDK Python 바인딩이 "
                "설치/PYTHONPATH 등록되어 있는지 확인하세요.")
            return

        self._running = True

        while self._running:
            if self._connect_and_scan():
                # 정상적으로 스캔 루프가 돌다가 끊긴 경우, 잠깐 쉬고 재시도
                pass
            if self._running:
                self.msleep(int(getattr(config, "LIDAR_RECONNECT_INTERVAL_SEC", 2.0) * 1000))

    def _connect_and_scan(self) -> bool:
        ydlidar.os_init()

        laser = ydlidar.CYdLidar()
        laser.setlidaropt(ydlidar.LidarPropSerialPort, self._port)
        laser.setlidaropt(ydlidar.LidarPropSerialBaudrate, 230400)
        laser.setlidaropt(ydlidar.LidarPropLidarType, ydlidar.TYPE_TRIANGLE)
        laser.setlidaropt(ydlidar.LidarPropDeviceType, ydlidar.YDLIDAR_TYPE_SERIAL)
        laser.setlidaropt(ydlidar.LidarPropScanFrequency, 8.0)
        laser.setlidaropt(ydlidar.LidarPropSampleRate, 5)
        laser.setlidaropt(ydlidar.LidarPropSingleChannel, False)

        if not laser.initialize():
            self.connection_error.emit(f"LiDAR 초기화 실패 (포트: {self._port})")
            laser.disconnecting()
            return False

        if not laser.turnOn():
            self.connection_error.emit("LiDAR 모터/스캔 시작 실패")
            laser.disconnecting()
            return False

        self.connection_error.emit("LiDAR 연결 및 스캔 시작됨")
        self._laser = laser
        scan = ydlidar.LaserScan()

        try:
            while self._running and ydlidar.os_isOk():
                result = laser.doProcessSimple(scan)
                if result:
                    points = [(p.angle * 180.0 / 3.14159265, p.range) for p in scan.points]
                    self.scan_updated.emit(points)
                else:
                    self.msleep(20)
        except Exception as exc:
            self.connection_error.emit(f"LiDAR 스캔 중 오류: {type(exc).__name__}: {exc}")
        finally:
            laser.turnOff()
            laser.disconnecting()
            self._laser = None

        return True

    def stop(self):
        self._running = False
        self.wait(2000)
