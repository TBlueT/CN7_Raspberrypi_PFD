"""
카메라 + YOLO26-Depth 리더 스레드

라이다가 차량 자외선차단 필름(근적외선 차단)을 통과하지 못해 카메라 기반
단안 깊이추정으로 교체했습니다. YOLO26-Depth(Ultralytics)로 화면 전체의
픽셀별 미터 단위 깊이를 얻은 뒤, 라이다 때처럼 낱개 포인트를 그대로 뿌리는
대신 실제 B737 TCAS(공중충돌방지) 화면처럼 "물체 단위"로 묶어서 각도/거리를
하나씩만 냅니다.

동작 순서:
  1. 관심영역(ROI, 하늘/대시보드 제외한 도로 높이 부분)만 잘라냄
  2. 각 세로 열(column)의 최소 깊이값 = 그 방향에서 가장 가까운 장애물 거리
  3. 인접한 열끼리 깊이가 비슷하면(OBJECT_CLUSTER_DEPTH_TOLERANCE_M 이내)
     하나의 "물체"로 묶음 (노이즈 제거용으로 최소 폭 미만 클러스터는 버림)
  4. 각 클러스터의 중심 열 위치를 카메라 시야각 기준 각도로 변환
  5. (각도, 거리) 리스트를 scan_updated로 발행 - 기존 라이다 신호와 완전히
     같은 인터페이스라 NDViewModel/NDView 쪽은 안 건드려도 됨(표시 방식만
     nd_view.py에서 점 -> 마름모로 변경)

주의: 이 파일은 실제 카메라/모델 없이는 이 샌드박스에서 끝까지 실행해볼 수
없습니다. 클러스터링 로직(_extract_objects_from_depth_map)만 가짜 깊이
배열로 단위 테스트했습니다. 실기에서 다음을 꼭 확인하세요:
  - `pip install ultralytics opencv-python` 설치 여부
  - YOLO26n-depth.pt 최초 실행 시 자동 다운로드되는지(인터넷 필요)
  - model.predict()가 반환하는 깊이맵의 정확한 속성명/형태는 ultralytics
    버전에 따라 다를 수 있어 아래 _run_inference()의 결과 파싱 부분을
    실제 응답 구조 보고 조정해야 할 수 있음
"""

from PyQt6.QtCore import QThread, pyqtSignal

import config

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

from services.sony_qx10_capture import SonyQX10Capture


class CameraDepthReaderThread(QThread):
    scan_updated = pyqtSignal(list)   # [(angle_deg, distance_m), ...] - 물체 단위
    connection_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False

    def run(self):
        if cv2 is None:
            self.connection_error.emit("opencv-python이 설치되어 있지 않습니다 (pip install opencv-python)")
            return
        if YOLO is None:
            self.connection_error.emit("ultralytics가 설치되어 있지 않습니다 (pip install ultralytics)")
            return

        self._running = True

        while self._running:
            if self._connect_and_run():
                pass
            if self._running:
                self.msleep(int(config.CAMERA_DEPTH_UPDATE_INTERVAL_SEC * 1000 * 10))  # 실패 시 좀 더 길게 대기

    def _open_camera(self):
        """config.CAMERA_SOURCE에 따라 일반 USB 웹캠 또는 소니 QX10
        Wi-Fi 라이브뷰를 연다. 둘 다 cv2.VideoCapture와 같은
        isOpened()/read() 인터페이스라 이후 코드는 동일하게 씀."""
        source = getattr(config, "CAMERA_SOURCE", "usb")
        if source == "sony_qx10":
            cap = SonyQX10Capture(
                discovery_timeout_sec=getattr(config, "SONY_QX10_DISCOVERY_TIMEOUT_SEC", 5.0),
                fixed_endpoint_url=getattr(config, "SONY_QX10_FIXED_ENDPOINT_URL", None),
            )
            cap.open()
            return cap
        return cv2.VideoCapture(config.CAMERA_INDEX)

    def _connect_and_run(self) -> bool:
        cap = self._open_camera()
        if cap is None or not cap.isOpened():
            self.connection_error.emit(
                f"카메라를 열 수 없습니다 (source={getattr(config, 'CAMERA_SOURCE', 'usb')})")
            return False

        try:
            model = YOLO(config.CAMERA_DEPTH_MODEL_PATH)
        except Exception as exc:
            self.connection_error.emit(f"YOLO26-Depth 모델 로드 실패: {type(exc).__name__}: {exc}")
            cap.release()
            return False

        self.connection_error.emit("카메라+깊이추정 모델 준비 완료, 추론 시작")

        try:
            while self._running:
                ok, frame = cap.read()
                if not ok:
                    self.connection_error.emit("카메라 프레임 읽기 실패")
                    break

                depth_map = self._run_inference(model, frame)
                if depth_map is not None:
                    objects = self._extract_objects_from_depth_map(depth_map)
                    self.scan_updated.emit(objects)

                self.msleep(int(config.CAMERA_DEPTH_UPDATE_INTERVAL_SEC * 1000))
        except Exception as exc:
            self.connection_error.emit(f"카메라/추론 중 오류: {type(exc).__name__}: {exc}")
        finally:
            cap.release()

        return True

    def _run_inference(self, model, frame):
        """YOLO26-Depth로 프레임 전체의 픽셀별 깊이맵(미터, 2D 배열)을 얻는다.
        주의: ultralytics 버전에 따라 결과 객체의 속성명이 다를 수 있어,
        실기에서 print(result)로 실제 구조를 한번 확인해보는 걸 권장."""
        try:
            results = model.predict(frame, verbose=False)
            result = results[0]
            # ultralytics depth 결과의 관례적 속성명(버전에 따라 다를 수 있음)
            depth_map = getattr(result, "depth", None)
            if depth_map is None:
                depth_map = getattr(result, "depths", None)
            if depth_map is None:
                self.connection_error.emit(
                    "깊이맵 속성을 못 찾았습니다 - ultralytics 버전에 맞게 "
                    "_run_inference()의 속성명을 조정해야 합니다")
                return None
            return depth_map
        except Exception as exc:
            self.connection_error.emit(f"추론 실패: {type(exc).__name__}: {exc}")
            return None

    def _extract_objects_from_depth_map(self, depth_map) -> list:
        """깊이맵(2D 배열, [row][col] = 미터)을 물체 단위 (각도,거리) 리스트로 변환."""
        height = len(depth_map)
        if height == 0:
            return []
        width = len(depth_map[0])
        if width == 0:
            return []

        roi_top = int(height * config.CAMERA_ROI_TOP_FRAC)
        roi_bottom = int(height * config.CAMERA_ROI_BOTTOM_FRAC)
        roi_top, roi_bottom = max(0, roi_top), min(height, roi_bottom)
        if roi_bottom <= roi_top:
            roi_top, roi_bottom = 0, height

        # 각 열의 최소 깊이(=그 방향에서 가장 가까운 장애물)
        column_min_depth = []
        for col in range(width):
            best = None
            for row in range(roi_top, roi_bottom):
                d = depth_map[row][col]
                if d is None or d <= 0:
                    continue
                if d > config.CAMERA_DEPTH_MAX_RANGE_M:
                    continue
                if best is None or d < best:
                    best = d
            column_min_depth.append(best)

        # 인접 열을 깊이 비슷한 것끼리 클러스터링
        clusters = []
        current_cluster = []
        for col, depth in enumerate(column_min_depth):
            if depth is None:
                if current_cluster:
                    clusters.append(current_cluster)
                    current_cluster = []
                continue
            if current_cluster:
                last_depth = column_min_depth[current_cluster[-1]]
                if abs(depth - last_depth) > config.OBJECT_CLUSTER_DEPTH_TOLERANCE_M:
                    clusters.append(current_cluster)
                    current_cluster = []
            current_cluster.append(col)
        if current_cluster:
            clusters.append(current_cluster)

        objects = []
        for cluster in clusters:
            if len(cluster) < config.OBJECT_MIN_CLUSTER_WIDTH_PX:
                continue
            center_col = sum(cluster) / len(cluster)
            depths_in_cluster = [column_min_depth[c] for c in cluster]
            representative_depth = min(depths_in_cluster)  # 제일 가까운 지점 기준(안전 마진)

            # 최대거리 근처는 "감지된 물체"가 아니라 그냥 뻥 뚫린 먼 도로일
            # 가능성이 높아서 제외 (화면에 불필요한 마름모가 안 뜨게)
            if representative_depth >= config.CAMERA_DEPTH_MAX_RANGE_M * 0.95:
                continue

            fraction = center_col / max(1, width - 1)  # 0.0(왼쪽) ~ 1.0(오른쪽)
            angle = (-config.CAMERA_HORIZONTAL_FOV_DEG / 2
                     + fraction * config.CAMERA_HORIZONTAL_FOV_DEG
                     + config.CAMERA_ANGLE_OFFSET_DEG)

            objects.append((angle, representative_depth))

        return objects

    def stop(self):
        self._running = False
        self.wait(2000)
