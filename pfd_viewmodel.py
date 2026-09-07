"""
PFDViewModel

services(Reader 스레드)의 시그널을 받는 슬롯들을 제공하고, 내부 Model을
갱신합니다.

주의: 여기서는 View에 "다시 그려라" 신호를 보내지 않습니다. IMU 같은 센서는
초당 수십~수백 번 갱신될 수 있는데, 그때마다 화면을 다시 그리면 실제 화면
주사율(config.FRAME_RATE_HZ)보다 훨씬 잦은 불필요한 repaint가 발생합니다.
대신 View가 FRAME_RATE_HZ 주기의 타이머로 스스로 최신 상태를 읽어가서 그리는
방식(폴링)을 씁니다. 이 클래스는 그냥 "가장 최신 값이 뭔지" 저장만 합니다.

차량 선회 시 관성력이 가속도계를 흔들어서 실제로는 평지인데도 롤(roll) 값이
틀어지는 노이즈가 생길 수 있습니다. IMU 자체에는 이걸 자동으로 걸러주는
기능이 없어서, 원심력으로 인한 "가짜 롤"의 크기를 직접 계산해서 빼주는
방식으로 보정합니다.

CAN 속도 데이터는 쓰지 않고 IMU 자체 출력값만 사용합니다: EBIMU의 "중력성분
제거된 가속도"(soa2, Local 기준) 출력을 켜두면, 옆방향(가로) 가속도(ay)가
바로 지금 얼마나 원심력을 받고 있는지를 직접 알려줍니다. 이 값을 중력가속도와
비교한 각도가 곧 "가짜roll" 크기입니다.

    가짜roll(도) = atan2(가로가속도 ay, 중력가속도) 를 도(degree)로 변환
    보정된 롤 = 측정된 롤 - 가짜roll

가속도계가 실측한 실제 힘을 그대로 쓰는 방식이라, 속도 센서(CAN) 연결 여부나
정확도와 무관하게 동작합니다. 스무딩(EMA)과 달리 오차의 원인을 직접 제거하는
방식이라 선회가 얼마나 길게 지속되든 반응 지연 없이 정확합니다. 잔여 센서
노이즈 제거용으로 가벼운 EMA 스무딩을 보조적으로만 얹습니다.
"""

import math
import time

from PyQt6.QtCore import QObject, QTimer

import config
from models.attitude_model import AttitudeModel
from models.vehicle_model import VehicleModel

GRAVITY_MPS2 = 9.81


class PFDViewModel(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.attitude = AttitudeModel()
        self.vehicle = VehicleModel()

        # 롤 보정/스무딩용 내부 상태
        self._smoothed_roll = 0.0
        self._latest_lateral_accel = 0.0   # IMU soa2의 ay(가로가속도), m/s^2
        self._has_accel_data = False       # soa2 필드가 실제로 들어오고 있는지

        # 속도 표시 애니메이션용 타이머 - CAN 데이터 도착 빈도와 무관하게
        # 고정 주기로 표시값을 목표치 쪽으로 조금씩 이동시킴
        self._speed_anim_last_time = None
        self._speed_anim_timer = QTimer(self)
        self._speed_anim_timer.timeout.connect(self._on_speed_animation_tick)
        self._speed_anim_timer.start(int(1000 / config.FRAME_RATE_HZ))

    # ---- services.imu_reader.ImuReaderThread.attitude_updated 에 연결 ----
    def on_attitude_updated(self, roll_deg: float, pitch_deg: float, yaw_deg: float):
        yaw_deg = yaw_deg % 360.0

        if self._has_accel_data:
            spurious_roll = math.degrees(
                math.atan2(self._latest_lateral_accel, GRAVITY_MPS2))
            corrected_roll = roll_deg - spurious_roll
        else:
            # soa2 데이터가 아직 안 들어왔으면 보정 없이 원본 그대로 사용
            corrected_roll = roll_deg

        # 잔여 센서 노이즈 제거용 가벼운 스무딩 (주된 보정은 위에서 이미 끝남)
        self._smoothed_roll += config.ROLL_SMOOTHING_ALPHA * (corrected_roll - self._smoothed_roll)

        self.attitude.roll_deg = self._smoothed_roll
        self.attitude.pitch_deg = pitch_deg
        self.attitude.yaw_deg = yaw_deg

    # ---- services.imu_reader.ImuReaderThread.linear_accel_updated 에 연결 ----
    def on_linear_accel_updated(self, ax: float, ay: float, az: float):
        """soa2(중력성분 제거, Local 기준) 출력. ay를 가로(원심력) 방향
        가속도로 사용. IMU 장착 방향에 따라 부호/축이 다르면
        config.LATERAL_ACCEL_AXIS_SIGN으로 보정."""
        self._latest_lateral_accel = ay * config.LATERAL_ACCEL_SIGN
        self._has_accel_data = True

    # ---- services.imu_reader.ImuReaderThread.feature_check_updated 에 연결 ----
    def on_imu_feature_check(self, confirmed: bool):
        self.attitude.imu_feature_confirmed = confirmed

    # ---- services.can_reader.CanMonitorThread 각 시그널에 연결 ----
    def on_speed_updated(self, value: float):
        """CAN에서 새 속도가 오면 목표치만 갱신. 화면에 실제 반영되는 값은
        _on_speed_animation_tick()이 매 프레임 조금씩 따라가며 처리."""
        self.vehicle.speed_kph_target = value

    def _on_speed_animation_tick(self):
        now = time.monotonic()
        if self._speed_anim_last_time is None:
            dt = 1.0 / config.FRAME_RATE_HZ
        else:
            dt = now - self._speed_anim_last_time
        self._speed_anim_last_time = now

        current = self.vehicle.speed_kph
        target = self.vehicle.speed_kph_target
        diff = target - current

        max_step = config.SPEED_ANIMATION_MAX_RATE_KPH_PER_SEC * dt
        self.vehicle.prev_speed_kph = current

        if abs(diff) <= max_step:
            self.vehicle.speed_kph = target
        else:
            self.vehicle.speed_kph = current + max_step * (1 if diff > 0 else -1)

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
