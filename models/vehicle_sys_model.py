"""순수 데이터: 차량 시스템 화면(오른쪽) 값. None = 아직 못 받음 / 해석 불가."""

from dataclasses import dataclass, field


@dataclass
class VehicleSysModel:
    steer_deg: float = None      # 조향각 (왼쪽 -, 오른쪽 +)
    brake_pct: float = None      # 브레이크 압력 (%)
    lock_fl: bool = None         # 운전석 문 잠김
    lock_fr: bool = None         # 조수석 문 잠김
    reverse: bool = None         # 후진(R)
    gear_sel: str = None         # 변속 레버 위치 "P" / "R" / "N" / "D" / "M"
    gear: int = None             # 현재 기어 번호 (1~9, 모르면 0)
    load_pct: float = None       # 엔진 부하 (%)
    coolant_c: float = None      # 냉각수 온도
    oil_c: float = None          # 엔진오일 온도
    batt_v: float = None         # 배터리 전압
    sat_c: float = None          # 외기 온도
    fuel_l: float = None         # 연료량 (L)
    # 타이어: {"FL": (공기압 psi, 온도 °C, 차 경고), ...}  값마다 None 가능
    tires: dict = field(default_factory=dict)
