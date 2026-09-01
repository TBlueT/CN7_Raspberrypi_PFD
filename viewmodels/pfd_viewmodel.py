"""
PFDViewModel

services(Reader 스레드)의 시그널을 받는 슬롯들을 제공하고, 내부 Model을
갱신합니다.

주의: 여기서는 View에 "다시 그려라" 신호를 보내지 않습니다. IMU 같은 센서는
초당 수십~수백 번 갱신될 수 있는데, 그때마다 화면을 다시 그리면 실제 화면
주사율(config.FRAME_RATE_HZ)보다 훨씬 잦은 불필요한 repaint가 발생합니다.
대신 View가 FRAME_RATE_HZ 주기의 타이머로 스스로 최신 상태를 읽어가서 그리는
방식(폴링)을 씁니다. 이 클래스는 그냥 "가장 최신 값이 뭔지" 저장만 합니다.

차량 선회/제동/급가속 시 관성력이 가속도계를 흔들어서 실제로는 평지인데도
롤(roll) 값이 틀어지는 노이즈가 생길 수 있습니다. 이를 억제하기 위해 롤 값에
EMA(지수이동평균) 스무딩을 걸되, "지금 흔들릴 만한 상황인지"를 판단하는 기준은
두 가지를 씁니다:

1. 가속도 기반(우선) - EBIMU의 중력성분 제거된 가속도 필드가 켜져 있으면
   (config.IMU_ACCEL_FIELDS_ENABLED=True), 그 크기가 임계값을 넘을 때 "흔들림
   상황"으로 판단. 원심력/제동/급가속을 직접 감지하므로 더 정확함.
2. 요값 변화율 기반(폴백) - 가속도 데이터가 없거나 최근 것이 너무 오래됐으면,
   헤딩(yaw) 변화 속도로 "선회 중"만 근사 판단.
"""

import time

from PyQt6.QtCore import QObject, QTimer

import config
from models.attitude_model import AttitudeModel
from models.vehicle_model import VehicleModel


class PFDViewModel(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.attitude = AttitudeModel()
        self.vehicle = VehicleModel()

        # 롤 스무딩용 내부 상태
        self._smoothed_roll = 0.0
        self._last_yaw = None
        self._last_time = None

        # 가속도 기반 판단용 상태
        self._last_accel_magnitude = 0.0
        self._last_accel_time = None

        # 속도 표시 애니메이션용 타이머 - CAN 데이터 도착 빈도와 무관하게
        # 고정 주기로 표시값을 목표치 쪽으로 조금씩 이동시킴
        self._speed_anim_last_time = None
        self._speed_anim_timer = QTimer(self)
        self._speed_anim_timer.timeout.connect(self._on_speed_animation_tick)
        self._speed_anim_timer.start(int(1000 / config.FRAME_RATE_HZ))

    # ---- services.imu_reader.ImuReaderThread.attitude_updated 에 연결 ----
    def on_attitude_updated(self, roll_deg: float, pitch_deg: float, yaw_deg: float):
        yaw_deg = yaw_deg % 360.0
        is_dynamic = self._is_dynamic_situation(yaw_deg)

        alpha = (config.ROLL_STABILIZE_ALPHA_TURNING if is_dynamic
                 else config.ROLL_STABILIZE_ALPHA_NORMAL)
        self._smoothed_roll += alpha * (roll_deg - self._smoothed_roll)

        self.attitude.roll_deg = self._smoothed_roll
        self.attitude.pitch_deg = pitch_deg
        self.attitude.yaw_deg = yaw_deg

    # ---- services.imu_reader.ImuReaderThread.linear_accel_updated 에 연결 ----
    def on_linear_accel_updated(self, ax: float, ay: float, az: float):
        self._last_accel_magnitude = (ax ** 2 + ay ** 2 + az ** 2) ** 0.5
        self._last_accel_time = time.monotonic()

    # ---- services.imu_reader.ImuReaderThread.feature_check_updated 에 연결 ----
    def on_imu_feature_check(self, confirmed: bool):
        self.attitude.imu_feature_confirmed = confirmed

    def _is_dynamic_situation(self, yaw_deg: float) -> bool:
        """가속도 데이터가 최근에 들어왔으면 그걸 우선 쓰고, 없으면 요값
        변화율로 폴백."""
        if self._last_accel_time is not None:
            age = time.monotonic() - self._last_accel_time
            if age <= config.ROLL_STABILIZE_ACCEL_DATA_TIMEOUT_SEC:
                return self._last_accel_magnitude > config.ROLL_STABILIZE_ACCEL_THRESHOLD_MPS2

        yaw_rate = self._compute_yaw_rate(yaw_deg)
        return abs(yaw_rate) > config.ROLL_STABILIZE_YAW_RATE_THRESHOLD_DEG_S

    def _compute_yaw_rate(self, yaw_deg: float) -> float:
        """직전 샘플과 비교해 초당 헤딩 변화량(deg/s)을 계산.
        360<->0 경계를 넘어가도 최단 각도 차이로 정확히 계산됨."""
        now = time.monotonic()
        if self._last_yaw is None or self._last_time is None:
            self._last_yaw, self._last_time = yaw_deg, now
            return 0.0

        dt = now - self._last_time
        self._last_time = now
        if dt <= 0:
            return 0.0

        delta = ((yaw_deg - self._last_yaw + 180) % 360) - 180
        self._last_yaw = yaw_deg
        return delta / dt

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
