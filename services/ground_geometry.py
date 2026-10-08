"""
평평한 지면 가정 기하학 거리 계산

박스 아랫변(물체가 지면에 닿는 위치)의 화면 y좌표와 카메라 높이로 전방 거리를 구한다.
    tilt  = atan((cy - horizon_row) / f)          # 카메라 상하 기울기
    angle = atan((y_bottom - cy) / f) + tilt      # 지평선 아래로 내려간 각도
    dist  = camera_height / tan(angle)

Lite-Mono 처럼 절대 스케일이 없는 깊이 모델의 배율 보정 기준으로 쓴다.
한계: 오르막/내리막, 박스 아랫변이 화면 밖으로 잘린 경우, 먼 거리(픽셀 오차가 크게 증폭).
"""

import math

MIN_ANGLE_RAD = math.radians(0.2)


def focal_px_from_hfov(width: int, hfov_deg: float) -> float:
    return (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def geometric_distance_m(y_bottom: float, img_h: int, f_px: float,
                         cam_height_m: float, horizon_row: float):
    """지면 전방 거리(m). 지평선 위/근처라 계산 불가하면 None."""
    cy = img_h / 2.0
    tilt = math.atan((cy - horizon_row) / f_px)
    angle = math.atan((y_bottom - cy) / f_px) + tilt
    if angle <= MIN_ANGLE_RAD:
        return None
    return cam_height_m / math.tan(angle)
