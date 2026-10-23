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
방식). 필터에 넣기 전에 OBD 속도로 가속도계 값을 보정합니다:
  - 좌우: 원심 가속도 = 속도 x 요레이트 를 빼서 회전 중 가짜 롤 제거
  - 앞뒤: 속도 변화율(가감속)을 빼서 가속/제동 중 가짜 피치 제거
OBD 속도가 끊기면 보정 없이 필터만 동작합니다. 요(yaw)는 이 필터로 계산하지
않고 EBIMU 자체 출력(지자기 보정 포함)을 그대로 씁니다.
"""

import math
import sys
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

        # 데이터 유효성: 마지막 수신 시각 (None = 아직 한 번도 못 받음)
        self._attitude_time = None
        self._speed_time = None
        self._speed_anim_last = None   # 애니메이션 틱 시각 (가속도 계산용)
        self._overspeed_since = None   # 과속 시작 시각 (None = 과속 아님)
        self._rpm_time = None          # 마지막 RPM 수신 시각

        # 가감속 보정용: OBD 속도 변화율 (m/s², 평활)
        self._long_accel_mps2 = 0.0
        self._prev_obd_speed = None    # (시각, m/s)
        # 원심력 보정 점검 로그용 누적값
        self._diag_mp = 0.0
        self._diag_pp = 0.0
        self._diag_n = 0

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
        yaw_deg = (yaw_deg + config.HEADING_OFFSET_DEG) % 360.0   # 헤딩 오프셋 적용

        now = time.monotonic()
        self._attitude_time = now
        dt = (now - self._ahrs_last_time) if self._ahrs_last_time is not None else None
        self._ahrs_last_time = now

        if self._latest_gyro is not None and self._latest_accel is not None and dt:
            gx, gy, gz = self._latest_gyro
            ax, ay, az = self._compensate_accel(self._latest_accel, self._latest_gyro)
            self._ahrs.update(gx, gy, gz, ax, ay, az, dt)
            roll_deg, pitch_deg = self._ahrs.get_roll_pitch_deg()
        # else: 자이로/가속도 원시값이 아직 없으면(sog1/soa1 미적용 등)
        # EBIMU 자체 계산값을 그대로 사용 (안전한 폴백)

        # 필터로 계산한 값에 잔여 노이즈 제거용 가벼운 스무딩을 추가로 얹음
        self._smoothed_roll += config.ROLL_SMOOTHING_ALPHA * (roll_deg - self._smoothed_roll)
        self._smoothed_pitch += config.PITCH_SMOOTHING_ALPHA * (pitch_deg - self._smoothed_pitch)

        self.attitude.roll_deg = self._smoothed_roll
        self.attitude.pitch_deg = self._smoothed_pitch

        # 요(헤딩)는 필터 없이 EBIMU 값 + 오프셋 그대로 사용 (ND 와 동일)
        self.attitude.yaw_deg = yaw_deg

    # ---------------------------------------------------------------
    # 원심력 / 가감속 보정
    # ---------------------------------------------------------------
    G = 9.80665

    def _compensate_accel(self, accel, gyro):
        """가속도계 값에서 차량 운동으로 생긴 가속도를 빼서 '중력만' 남김.
        OBD 속도가 끊겼으면 그대로 반환 (보정 없이 필터만 동작)."""
        a = list(accel)
        if not self.speed_valid:
            return tuple(a)
        unit = 1.0 / self.G if config.ACCEL_UNIT_IS_G else 1.0   # m/s² -> 가속도계 단위
        v = self.vehicle.speed_kph_target / 3.6

        if config.AHRS_CENTRIPETAL_COMP:
            yaw_rate = gyro[config.IMU_YAW_RATE_AXIS]               # rad/s, 좌회전 +
            centripetal = v * yaw_rate                               # m/s², 왼쪽 +
            lat = config.IMU_LATERAL_AXIS
            if config.AHRS_COMP_DIAGNOSTIC:
                measured = a[lat] * config.IMU_LATERAL_SIGN / unit   # 실측 좌우 (m/s², 왼쪽 +)
                self._diagnose_centripetal(measured, centripetal, v, yaw_rate)
            a[lat] -= config.IMU_LATERAL_SIGN * centripetal * unit

        if config.AHRS_LONGITUDINAL_COMP:
            lon = config.IMU_LONGITUDINAL_AXIS
            a[lon] -= config.IMU_LONGITUDINAL_SIGN * self._long_accel_mps2 * unit
        return tuple(a)

    def _diagnose_centripetal(self, measured, predicted, v, yaw_rate):
        """회전 중(시속 10km 이상, 요레이트 3°/s 이상) 실측/계산 비율을 모아서
        약 150 샘플마다 로그. 축·부호·단위 설정이 맞는지 차에서 확인하는 용도."""
        if v < 10 / 3.6 or abs(yaw_rate) < math.radians(3):
            return
        self._diag_mp += measured * predicted
        self._diag_pp += predicted * predicted
        self._diag_n += 1
        if self._diag_n >= 150 and self._diag_pp > 0:
            ratio = self._diag_mp / self._diag_pp
            if 0.6 <= ratio <= 1.5:
                verdict = "정상"
            elif -1.5 <= ratio <= -0.6:
                verdict = "부호 반대 -> IMU_LATERAL_SIGN 을 바꾸세요"
            elif abs(ratio) > 5 or 0 < abs(ratio) < 0.2:
                verdict = "단위 확인 (ACCEL_UNIT_IS_G) 또는 축 틀림"
            else:
                verdict = "축이 틀렸을 수 있음 (IMU_LATERAL_AXIS)"
            print(f"[AHRS] 원심력 점검: 실측/계산 = {ratio:+.2f} ({verdict})", file=sys.stderr)
            self._diag_mp = self._diag_pp = 0.0
            self._diag_n = 0

    # ---- services.imu_reader.ImuReaderThread.feature_check_updated 에 연결 ----
    def on_imu_feature_check(self, confirmed: bool):
        self.attitude.imu_feature_confirmed = confirmed

    # ---- services.can_reader.CanMonitorThread 각 시그널에 연결 ----
    def on_speed_updated(self, value: float):
        """CAN에서 새 속도가 오면 목표치만 갱신. 화면에 실제 반영되는 값은
        _on_speed_animation_tick()이 매 프레임 조금씩 따라가며 처리."""
        now = time.monotonic()
        # 가감속 보정용 속도 변화율 (OBD 는 0.7초 간격 정수 km/h 라 평활해서 사용)
        v = value / 3.6
        if self._prev_obd_speed is not None and self.speed_valid:
            t_prev, v_prev = self._prev_obd_speed
            dt = now - t_prev
            if dt > 0.05:
                rate = (v - v_prev) / dt
                k = dt / (config.SPEED_DERIV_SMOOTHING_SEC + dt)
                self._long_accel_mps2 += k * (rate - self._long_accel_mps2)
        else:
            self._long_accel_mps2 = 0.0    # 끊겼다 다시 들어오면 새로 시작
        self._prev_obd_speed = (now, v)

        self.vehicle.speed_kph_target = value
        self._speed_time = now

    def _on_speed_animation_tick(self):
        now = time.monotonic()
        dt = (now - self._speed_anim_last) if self._speed_anim_last is not None else 0.0
        self._speed_anim_last = now

        current = self.vehicle.speed_kph
        target = self.vehicle.speed_kph_target
        new = current + config.SPEED_SMOOTHING_ALPHA * (target - current)
        self.vehicle.speed_kph = new

        # 트렌드 화살표용 가속도: 표시 속도 변화율을 SPEED_ACCEL_SMOOTHING_SEC 로 평활
        if dt > 0:
            rate = (new - current) / dt
            k = dt / (config.SPEED_ACCEL_SMOOTHING_SEC + dt)
            self.vehicle.speed_accel_kph_s += k * (rate - self.vehicle.speed_accel_kph_s)

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
