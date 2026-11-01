"""순수 데이터: CAN에서 나오는 차량 상태(속도, SCC/LFA/HDA). 렌더링/비즈니스 로직 없음."""

from dataclasses import dataclass


@dataclass
class VehicleModel:
    speed_kph: float = 0.0          # 화면에 실제 표시되는 값 (보간 애니메이션)
    speed_kph_target: float = 0.0   # OBD 에서 받은 최신 실제값 (보간 목표치)
    speed_accel_kph_s: float = 0.0  # OBD 속도 변화율 평활값 (km/h/s, 트렌드 화살표용)
    rpm: float = 0.0                # 화면 표시 RPM (평활)
    rpm_target: float = 0.0         # OBD 에서 받은 최신 RPM

    scc_main_on: bool = False
    scc_active: bool = False
    scc_set_speed: float = 0.0
    lfa_active: bool = False
    hda_curve_active: bool = False
