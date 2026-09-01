"""순수 데이터: IMU에서 나오는 자세각. 렌더링/비즈니스 로직 없음."""

from dataclasses import dataclass


@dataclass
class AttitudeModel:
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    imu_feature_confirmed: bool = False   # 내장 안정화 기능(AVC 등) 확인 여부
