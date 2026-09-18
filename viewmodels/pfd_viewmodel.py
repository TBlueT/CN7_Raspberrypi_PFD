"""
PFDViewModel

services(Reader 스레드)의 시그널을 받는 슬롯들을 제공하고, 내부 Model을
갱신합니다.

주의: 여기서는 View에 "다시 그려라" 신호를 보내지 않습니다. IMU 같은 센서는
초당 수십~수백 번 갱신될 수 있는데, 그때마다 화면을 다시 그리면 실제 화면
주사율(config.FRAME_RATE_HZ)보다 훨씬 잦은 불필요한 repaint가 발생합니다.
대신 View가 FRAME_RATE_HZ 주기의 타이머로 스스로 최신 상태를 읽어가서 그리는
방식(폴링)을 씁니다. 이 클래스는 그냥 "가장 최신 값이 뭔지" 저장만 합니다.

롤/피치는 EBIMU 자체 계산값을 사후 보정하는 대신, services.ahrs_filter의
Mahony 필터로 raw 자이로+가속도에서 직접 추정합니다 (항공/로보틱스 표준
방식). CAN 속도나 요값 변화율에 의존하지 않고, 필터 자체가 선회/가감속
중 가속도계 신뢰도를 자연스럽게 낮추는 구조라 원심력 문제를 원천적으로
완화합니다. 요(yaw)는 이 필터로 계산하지 않고 EBIMU 자체 출력(지자기 보정
포함)을 그대로 씁니다.
"""

import math
import time

from PyQt6.QtCore import QObject, QTimer

import config
from models.attitude_model import AttitudeModel
from models.vehicle_model import VehicleModel
from services.ahrs_filter import MahonyAHRS


class PFDViewModel(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.attitude = AttitudeModel()
        self.vehicle = VehicleModel()

        self._ahrs = MahonyAHRS(kp=config.MAHONY_KP, ki=config.MAHONY_KI)
        self._latest_gyro = None    # (gx, gy, gz) rad/s
        self._latest_accel = None   # (ax, ay, az) 중력 포함
        self._ahrs_last_time = None
        self._smoothed_roll = 0.0
        self._smoothed_pitch = 0.0
        self._smoothed_yaw = 0.0

        # 속도 표시 애니메이션용 타이머 - CAN 데이터 도착 빈도와 무관하게
        # 고정 주기로 표시값을 목표치 쪽으로 EMA(지수감쇠)로 부드럽게 수렴시킴
        self._speed_anim_timer = QTimer(self)
        self._speed_anim_timer.timeout.connect(self._on_speed_animation_tick)
        self._speed_anim_timer.start(int(1000 / config.FRAME_RATE_HZ))

    # ---- services.imu_reader.ImuReaderThread.gyro_updated 에 연결 ----
    def on_gyro_updated(self, gx: float, gy: float, gz: float):
        """sog1 출력(deg/s 가정) - 축 부호 보정 후 Mahony 필터가 쓰는
        rad/s로 변환해서 저장."""
        sx, sy, sz = config.GYRO_SIGN
        gx, gy, gz = gx * sx, gy * sy, gz * sz
        if config.GYRO_UNIT_IS_DEG_PER_SEC:
            gx, gy, gz = math.radians(gx), math.radians(gy), math.radians(gz)
        self._latest_gyro = (gx, gy, gz)

    # ---- services.imu_reader.ImuReaderThread.linear_accel_updated 에 연결 ----
    def on_linear_accel_updated(self, ax: float, ay: float, az: float):
        """soa1 출력(중력 포함 원본) - 축 부호 보정 후 저장.
        Mahony 필터가 중력 방향 보정에 사용."""
        sx, sy, sz = config.ACCEL_SIGN
        self._latest_accel = (ax * sx, ay * sy, az * sz)

    # ---- services.imu_reader.ImuReaderThread.attitude_updated 에 연결 ----
    def on_attitude_updated(self, roll_deg: float, pitch_deg: float, yaw_deg: float):
        yaw_deg = yaw_deg % 360.0

        now = time.monotonic()
        dt = (now - self._ahrs_last_time) if self._ahrs_last_time is not None else None
        self._ahrs_last_time = now

        if self._latest_gyro is not None and self._latest_accel is not None and dt:
            gx, gy, gz = self._latest_gyro
            ax, ay, az = self._latest_accel
            self._ahrs.update(gx, gy, gz, ax, ay, az, dt)
            roll_deg, pitch_deg = self._ahrs.get_roll_pitch_deg()
        # else: 자이로/가속도 원시값이 아직 없으면(sog1/soa1 미적용 등)
        # EBIMU 자체 계산값을 그대로 사용 (안전한 폴백)

        # 필터로 계산한 값에 잔여 노이즈 제거용 가벼운 스무딩을 추가로 얹음
        self._smoothed_roll += config.ROLL_SMOOTHING_ALPHA * (roll_deg - self._smoothed_roll)
        self._smoothed_pitch += config.PITCH_SMOOTHING_ALPHA * (pitch_deg - self._smoothed_pitch)

        self.attitude.roll_deg = self._smoothed_roll
        self.attitude.pitch_deg = self._smoothed_pitch

        # 요(yaw)도 EMA 스무딩. 0<->360도 경계를 최단각도로 계산해서 래핑 처리
        # (예: 359도->1도는 실제로 2도 변화인데 그냥 평균내면 358도 변화로
        # 잘못 계산되는 문제 방지)
        yaw_delta = ((yaw_deg - self._smoothed_yaw + 180) % 360) - 180
        self._smoothed_yaw = (self._smoothed_yaw + yaw_delta * config.YAW_SMOOTHING_ALPHA) % 360
        self.attitude.yaw_deg = self._smoothed_yaw

    # ---- services.imu_reader.ImuReaderThread.feature_check_updated 에 연결 ----
    def on_imu_feature_check(self, confirmed: bool):
        self.attitude.imu_feature_confirmed = confirmed

    # ---- services.can_reader.CanMonitorThread 각 시그널에 연결 ----
    def on_speed_updated(self, value: float):
        """CAN에서 새 속도가 오면 목표치만 갱신. 화면에 실제 반영되는 값은
        _on_speed_animation_tick()이 매 프레임 조금씩 따라가며 처리."""
        self.vehicle.speed_kph_target = value

    def _on_speed_animation_tick(self):
        current = self.vehicle.speed_kph
        target = self.vehicle.speed_kph_target

        self.vehicle.prev_speed_kph = current
        self.vehicle.speed_kph += config.SPEED_SMOOTHING_ALPHA * (target - current)

    def on_altitude_updated(self, value: float):
        """추후 고도 센서 연동 시 사용. 지금은 아무 곳에서도 호출 안 됨(항상 0)."""
        self.vehicle.altitude_ft = value

    def on_scc_status_updated(self, main_on: bool, active: bool, set_speed: float):
        self.vehicle.scc_main_on = main_on
        self.vehicle.scc_active = active
        self.vehicle.scc_set_speed = set_speed

    def on_lfa_status_updated(self, active: bool):
        self.vehicle.lfa_active = active

    def on_hda_curve_updated(self, active: bool):
        self.vehicle.hda_curve_active = active
