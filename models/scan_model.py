"""순수 데이터: 카메라(또는 라이다)로 감지한 물체 단위 (각도,거리). 렌더링/비즈니스 로직 없음."""

from dataclasses import dataclass, field


@dataclass
class ScanModel:
    # [(angle_deg, distance_m), ...] - 물체 하나당 하나씩 (camera_depth_reader가
    # 깊이맵을 클러스터링해서 이미 물체 단위로 묶어서 냄)
    points: list = field(default_factory=list)
