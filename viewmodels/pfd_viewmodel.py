"""
PFDViewModel

services(Reader 스레드)의 시그널을 받는 슬롯들을 제공하고, 내부 Model을
갱신합니다.

주의: 여기서는 View에 "다시 그려라" 신호를 보내지 않습니다. IMU 같은 센서는
초당 수십~수백 번 갱신될 수 있는데, 그때마다 화면을 다시 그리면 실제 화면
주사율(config.FRAME_RATE_HZ)보다 훨씬 잦은 불필요한 repaint가 발생합니다.
대신 View가 FRAME_RATE_HZ 주기의 타이머로 스스로 최신 상태를 읽어가서 그리는
방식(폴링)을 씁니다. 이 클래스는 그냥 "가장 최신 값이 뭔지" 저장만 합니다.

롤/피치/요는 EBIMU 가 출력하는 값을 필터·보정 없이 그대로 표시합니다
(헤딩만 HEADING_OFFSET_DEG 적용).
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

        # 데이터 유효성: 마지막 수신 시각 (None = 아직 한 번도 못 받음)
        self._attitude_time = None
        self._speed_time = None
        self._speed_anim_last = None   # 애니메이션 틱 시각
        # 속도 보간: 새 OBD 값까지 일정 속도로 이동
        self._speed_rate = 0.0         # 표시 바늘 이동 속도 (km/h/s)
        self._obd_interval = config.SPEED_INTERP_INIT_SEC   # OBD 속도 수신 간격 평균
        self._prev_obd_speed = None    # (시각, km/h) - 가속도 계산용
        self._overspeed_since = None   # 과속 시작 시각 (None = 과속 아님)
        self._rpm_time = None          # 마지막 RPM 수신 시각

        # 속도 표시 애니메이션용 타이머 - OBD 값 도착 빈도와 무관하게 고정 주기로
        # 표시값을 목표치 쪽으로 일정 속도(보간)로 움직임
        self._speed_anim_timer = QTimer(self)
        self._speed_anim_timer.timeout.connect(self._on_speed_animation_tick)
        self._speed_anim_timer.start(int(1000 / config.FRAME_RATE_HZ))

    # ---- services.imu_reader.ImuReaderThread.attitude_updated 에 연결 ----
    def on_attitude_updated(self, roll_deg: float, pitch_deg: float, yaw_deg: float):
        """EBIMU 출력 그대로 표시 (필터/보정 없음). 헤딩만 오프셋 적용."""
        self._attitude_time = time.monotonic()
        self.attitude.roll_deg = roll_deg
        self.attitude.pitch_deg = pitch_deg
        self.attitude.yaw_deg = (yaw_deg + config.HEADING_OFFSET_DEG) % 360.0

    # ---- services.imu_reader.ImuReaderThread.feature_check_updated 에 연결 ----
    def on_imu_feature_check(self, confirmed: bool):
        self.attitude.imu_feature_confirmed = confirmed

    # ---- services.can_reader.CanMonitorThread 각 시그널에 연결 ----
    def on_speed_updated(self, value: float):
        """OBD 새 속도 수신: 다음 값이 올 예상 시간 동안 현재 표시값에서 새 값까지
        일정 속도로 가도록 바늘 이동 속도를 정함 (실제 이동은 애니메이션 타이머가 처리).
        트렌드 화살표용 가속도는 OBD 값 변화율로 계산."""
        now = time.monotonic()
        if self._prev_obd_speed is not None and self.speed_valid:
            t_prev, v_prev = self._prev_obd_speed
            dt = now - t_prev
            if dt > 0.05:
                self._obd_interval += 0.3 * (dt - self._obd_interval)
                k = dt / (config.SPEED_ACCEL_SMOOTHING_SEC + dt)
                accel = (value - v_prev) / dt
                self.vehicle.speed_accel_kph_s += k * (accel - self.vehicle.speed_accel_kph_s)
        else:
            self.vehicle.speed_accel_kph_s = 0.0   # 처음 / 끊겼다 다시 들어옴
        self._prev_obd_speed = (now, value)

        duration = max(config.SPEED_INTERP_MIN_SEC, self._obd_interval)
        self._speed_rate = (value - self.vehicle.speed_kph) / duration
        self.vehicle.speed_kph_target = value
        self._speed_time = now

    def _on_speed_animation_tick(self):
        now = time.monotonic()
        dt = (now - self._speed_anim_last) if self._speed_anim_last is not None else 0.0
        self._speed_anim_last = now

        # 보간: 일정 속도로 목표치까지 이동, 도착하면 멈춤 (지나치지 않음)
        current = self.vehicle.speed_kph
        target = self.vehicle.speed_kph_target
        new = current + self._speed_rate * dt
        if (self._speed_rate >= 0 and new >= target) or (self._speed_rate < 0 and new <= target):
            new = target
        self.vehicle.speed_kph = new

        self._update_overspeed(now)

        # RPM 표시값도 같은 타이머로 평활 (OBD 는 0.5초 이상 간격으로 옴)
        self.vehicle.rpm += config.RPM_SMOOTHING_ALPHA * (self.vehicle.rpm_target - self.vehicle.rpm)

    def _update_overspeed(self, now: float):
        """표시 속도 기준 과속 판정. 한계 이상이면 시작, 한계 - HYST 아래로 내려가면 해제
        (한계 근처에서 경고가 켜졌다 꺼졌다 반복하지 않도록)."""
        limit = getattr(config, "SPEED_BARBER_POLE_KPH", None)
        if limit is None or not self.speed_valid:
            self._overspeed_since = None
            return
        v = self.vehicle.speed_kph
        if self._overspeed_since is None:
            if v >= limit:
                self._overspeed_since = now
        elif v < limit - config.SPEED_OVERSPEED_HYST_KPH:
            self._overspeed_since = None

    # ---- services.can_reader.CanMonitorThread.rpm_updated 에 연결 ----
    def on_rpm_updated(self, value: float):
        self.vehicle.rpm_target = value
        self._rpm_time = time.monotonic()

    def on_scc_status_updated(self, main_on: bool, active: bool, set_speed: float):
        self.vehicle.scc_main_on = main_on
        self.vehicle.scc_active = active
        self.vehicle.scc_set_speed = set_speed

    def on_lfa_status_updated(self, active: bool):
        self.vehicle.lfa_active = active

    def on_hda_curve_updated(self, active: bool):
        self.vehicle.hda_curve_active = active

    # ---- 데이터 유효성 (View 가 매 프레임 확인) ----
    @staticmethod
    def _is_fresh(t, limit_sec: float) -> bool:
        return t is not None and (time.monotonic() - t) <= limit_sec

    @property
    def attitude_valid(self) -> bool:
        """IMU 자세 데이터가 IMU_STALE_SEC 이내에 들어왔는지 (False면 ATT 표시)"""
        return self._is_fresh(self._attitude_time, config.IMU_STALE_SEC)

    @property
    def heading_valid(self) -> bool:
        """헤딩(요)도 같은 IMU 줄에서 오므로 자세와 같은 기준 (False면 HDG 표시)"""
        return self.attitude_valid

    @property
    def rpm_valid(self) -> bool:
        """OBD RPM 이 RPM_STALE_SEC 이내에 들어왔는지 (False면 RPM 고장 표시)"""
        return self._is_fresh(self._rpm_time, config.RPM_STALE_SEC)

    @property
    def overspeed(self) -> bool:
        return self._overspeed_since is not None

    @property
    def overspeed_elapsed(self):
        """과속이 시작된 뒤 지난 시간(초). 과속 아니면 None (View 의 깜빡임 계산용)."""
        if self._overspeed_since is None:
            return None
        return time.monotonic() - self._overspeed_since

    @property
    def speed_valid(self) -> bool:
        """OBD 속도가 SPEED_STALE_SEC 이내에 들어왔는지 (False면 SPD 표시)"""
        return self._is_fresh(self._speed_time, config.SPEED_STALE_SEC)
