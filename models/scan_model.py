"""순수 데이터: 라이다 스캔 포인트. 렌더링/비즈니스 로직 없음."""

from dataclasses import dataclass, field


@dataclass
class ScanModel:
    # [(angle_deg, distance_m), ...]
    points: list = field(default_factory=list)
