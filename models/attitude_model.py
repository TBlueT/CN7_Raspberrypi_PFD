"""순수 데이터: IMU 자세각 (EBIMU 출력 그대로). 렌더링/비즈니스 로직 없음."""

from dataclasses import dataclass


@dataclass
class AttitudeModel:
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0             # 헤딩 (HEADING_OFFSET_DEG 적용)
