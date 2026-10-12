"""순수 데이터: CAN에서 나오는 차량 상태(속도, SCC/LFA/HDA). 렌더링/비즈니스 로직 없음."""

from dataclasses import dataclass


@dataclass
class VehicleModel:
    speed_kph: float = 0.0          # 화면에 실제 표시되는 값 (부드럽게 애니메이션됨)
    speed_kph_target: float = 0.0   # CAN에서 받은 최신 실제값 (애니메이션 목표치)
    speed_accel_kph_s: float = 0.0  # 표시 속도의 변화율 (km/h/s, 트렌드 화살표용)
    altitude_ft: float = 0.0        # 센서 미연동, 항상 0

    scc_main_on: bool = False
    scc_active: bool = False
    scc_set_speed: float = 0.0
    lfa_active: bool = False
    hda_curve_active: bool = False
