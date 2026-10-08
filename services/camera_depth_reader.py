"""
카메라 + 물체 탐지 + 깊이 추정 리더 스레드

동작 순서:
  1. 탐지 모델(yolo26n, NCNN)로 차량/사람 바운딩박스 + 클래스
  2. Lite-Mono-tiny 로 상대 깊이맵 추정 (미터 배율은 기하학 거리로 보정)
  3. 박스 영역 깊이 중앙값 = 물체 거리
  4. 박스 중심 x좌표 → 카메라 시야각 기준 각도
  5. [(angle_deg, distance_m), ...] 를 scan_updated 로 발행 → nd_view 가 TCAS 마름모로 표시

Lite-Mono 배율 보정:
  Lite-Mono 는 단안 자기지도 학습이라 절대 스케일이 없다. 박스 아랫변과 카메라 높이로
  구한 기하학 거리(services/ground_geometry.py)가 계산되는 박스들에서
  배율 = 기하학거리 / Lite-Mono값 의 중앙값을 구해 EMA 로 갱신한다.
  - 배율이 아직 없으면 기하학 거리가 있는 물체만 그 값으로 표시
  - 장착 후 배율이 안정되면 그 값을 config.LITE_MONO_FIXED_SCALE 에 고정해도 됨
    (TIMING 로그에 현재 배율이 같이 출력됨)

NCNN: 탐지 모델(.pt)은 최초 실행 시 <이름>_ncnn_model 로 변환 후 재사용.
라이다(YDLIDAR G2)는 차량 자외선차단 필름을 통과하지 못해 카메라 방식으로 교체함.
"""

import os
import time

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

import config
from services.ground_geometry import focal_px_from_hfov, geometric_distance_m
from services.sony_qx10_capture import SonyQX10Capture

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None


# COCO 클래스 중 관심 있는 것만 (차량류 + 사람). 다른 클래스는 탐지돼도 무시.
RELEVANT_CLASS_NAMES = {"person", "car", "truck", "bus", "motorcycle", "bicycle"}


class CameraDepthReaderThread(QThread):
    # [(angle_deg, distance_m), ...] - 물체 하나당 하나씩
    scan_updated = pyqtSignal(list)
    # "disconnected"(와이파이/카메라 네트워크 자체가 안 붙음),
    # "camera_error"(연결은 되는데 카메라 스트림/API 문제),
    # "connected"(정상 - 물체가 0개 감지돼도 이 상태임)
    status_updated = pyqtSignal(str)
    connection_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._detect_model = None
        self._lite = None

        self._lite_scale = getattr(config, "LITE_MONO_FIXED_SCALE", None)
        self._scale_fixed = self._lite_scale is not None

        self._timing = {"det": 0.0, "depth": 0.0, "frames": 0}
        self._last_timing_log = time.monotonic()

    def run(self):
        self._running = True

        if cv2 is None:
            self.status_updated.emit("camera_error")
            self.connection_error.emit("opencv-python이 설치되어 있지 않습니다.")
            return
        if YOLO is None:
            self.status_updated.emit("camera_error")
            self.connection_error.emit("ultralytics가 설치되어 있지 않습니다.")
            return

        while self._running:
            self._connect_and_run()
            if self._running:
                self.msleep(int(config.CAMERA_RECONNECT_INTERVAL_SEC * 1000))

    # ---------------------------------------------------------------
    # 모델 로딩
    # ---------------------------------------------------------------
    def _ncnn_model_dir(self, pt_path: str) -> str:
        """Ultralytics의 export(format="ncnn") 명명 규칙: <이름>_ncnn_model 폴더."""
        base_dir = os.path.dirname(pt_path)
        stem = os.path.splitext(os.path.basename(pt_path))[0]
        return os.path.join(base_dir, f"{stem}_ncnn_model")

    def _load_or_export_ncnn(self, pt_path: str):
        """이미 변환된 NCNN 모델 폴더가 있으면 바로 로드, 없으면 .pt를 먼저
        로드해서 export(format="ncnn")로 변환한 뒤 그 결과를 로드한다."""
        ncnn_dir = self._ncnn_model_dir(pt_path)

        if os.path.isdir(ncnn_dir):
            self.connection_error.emit(f"NCNN 모델 로드 중(기존 변환본 사용): {ncnn_dir}")
            return YOLO(ncnn_dir)

        self.connection_error.emit(f"NCNN 변환본이 없어 새로 변환합니다: {pt_path} -> {ncnn_dir}")
        pt_model = YOLO(pt_path)
        exported_path = pt_model.export(format="ncnn")
        load_path = exported_path if os.path.isdir(exported_path) else ncnn_dir
        self.connection_error.emit(f"NCNN 변환 완료, 로드 중: {load_path}")
        return YOLO(load_path)

    def _load_lite_mono(self):
        from services.litemono_depth import LiteMonoDepth

        lite = LiteMonoDepth(
            repo_dir=config.LITE_MONO_REPO_DIR,
            weights_dir=config.LITE_MONO_WEIGHTS_DIR,
            model_name=config.LITE_MONO_MODEL_NAME,
            runtime=config.LITE_MONO_RUNTIME,
            onnx_path=config.LITE_MONO_ONNX_PATH,
            num_threads=config.LITE_MONO_NUM_THREADS,
            horizon_in_band=config.LITE_MONO_HORIZON_IN_BAND,
            crop=config.LITE_MONO_CROP,
        )
        for msg in lite.log:
            self.connection_error.emit(msg)
        return lite

    def _load_models(self) -> bool:
        try:
            if self._detect_model is None:
                self._detect_model = self._load_or_export_ncnn(config.CAMERA_DETECT_MODEL_PATH)
            if self._lite is None:
                self._lite = self._load_lite_mono()
            return True
        except Exception as exc:
            self.connection_error.emit(f"모델 로드/변환 실패: {type(exc).__name__}: {exc}")
            return False

    # ---------------------------------------------------------------
    # 카메라 연결 / 루프
    # ---------------------------------------------------------------
    def _open_camera(self):
        """config.CAMERA_SOURCE에 따라 일반 USB 웹캠 또는 소니 QX10
        Wi-Fi 라이브뷰를 연다. 둘 다 isOpened()/read() 인터페이스가 같음."""
        if config.CAMERA_SOURCE == "sony_qx10":
            cap = SonyQX10Capture(
                discovery_timeout_sec=config.SONY_QX10_DISCOVERY_TIMEOUT_SEC,
                fixed_endpoint_url=config.SONY_QX10_FIXED_ENDPOINT_URL,
                wifi_interface=config.SONY_QX10_WIFI_INTERFACE,
            )
            cap.open()
            return cap
        return cv2.VideoCapture(config.CAMERA_INDEX)

    def _connect_and_run(self):
        if not self._load_models():
            self.status_updated.emit("camera_error")
            return

        cap = self._open_camera()
        if cap is None or not cap.isOpened():
            # SonyQX10Capture는 어느 단계에서 실패했는지 알려줌(discovery=
            # 와이파이 문제, api/stream=카메라 자체 문제).
            failure_stage = getattr(cap, "last_failure_stage", None)
            if failure_stage == "discovery":
                self.status_updated.emit("disconnected")
            else:
                self.status_updated.emit("camera_error")
            self.connection_error.emit(f"카메라를 열 수 없습니다 (source={config.CAMERA_SOURCE})")
            return

        self.status_updated.emit("connected")
        self.connection_error.emit(f"카메라 연결 및 추론 시작됨 (depth=Lite-Mono)")

        try:
            while self._running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    self.status_updated.emit("camera_error")
                    self.connection_error.emit("프레임 읽기 실패, 재연결 시도")
                    return

                objects = self._process_frame(frame)
                self.scan_updated.emit(objects)
                self._maybe_log_timing()

                self.msleep(int(config.CAMERA_DEPTH_UPDATE_INTERVAL_SEC * 1000))
        except Exception as exc:
            self.status_updated.emit("camera_error")
            self.connection_error.emit(f"추론 중 오류: {type(exc).__name__}: {exc}")
        finally:
            cap.release()

    # ---------------------------------------------------------------
    # 프레임 처리
    # ---------------------------------------------------------------
    def _process_frame(self, frame: np.ndarray) -> list:
        height, width = frame.shape[:2]

        t0 = time.perf_counter()
        detections = self._run_detection(frame)
        t1 = time.perf_counter()
        self._timing["det"] += t1 - t0
        self._timing["frames"] += 1
        if not detections:
            return []

        distances = self._litemono_distances(frame, detections)
        self._timing["depth"] += time.perf_counter() - t1

        results = []
        for (x1, y1, x2, y2, _label), distance_m in zip(detections, distances):
            if distance_m is None:
                continue
            center_x = (x1 + x2) / 2
            fraction = center_x / max(1, width - 1)   # 0.0(왼쪽 끝) ~ 1.0(오른쪽 끝)
            angle_deg = (fraction - 0.5) * config.CAMERA_HORIZONTAL_FOV_DEG
            angle_deg += config.CAMERA_ANGLE_OFFSET_DEG
            results.append((angle_deg, distance_m))
        return results

    def _litemono_distances(self, frame: np.ndarray, detections: list) -> list:
        """Lite-Mono 상대 깊이 + 기하학 배율 보정으로 박스별 거리(m) 목록."""
        h, w = frame.shape[:2]
        horizon_row = config.CAMERA_HORIZON_ROW_FRAC * h
        rel_map = self._lite.predict(frame, horizon_row)

        f_px = focal_px_from_hfov(w, config.CAMERA_HORIZONTAL_FOV_DEG)
        margin = config.CAMERA_BOTTOM_TRUNCATE_MARGIN_PX
        rel_list, geo_list, ratios = [], [], []
        for x1, y1, x2, y2, _ in detections:
            rel = self._box_distance(rel_map, x1, y1, x2, y2)
            geo = None
            if y2 < h - 1 - margin:
                geo = geometric_distance_m(y2, h, f_px, config.CAMERA_HEIGHT_M, horizon_row)
            if rel is not None and geo is not None:
                ratios.append(geo / rel)
            rel_list.append(rel)
            geo_list.append(geo)

        if ratios and not self._scale_fixed:
            r = float(np.median(ratios))
            a = config.LITE_MONO_SCALE_EMA_ALPHA
            self._lite_scale = r if self._lite_scale is None else (1 - a) * self._lite_scale + a * r

        out = []
        for rel, geo in zip(rel_list, geo_list):
            if rel is not None and self._lite_scale is not None:
                out.append(rel * self._lite_scale)
            else:
                out.append(geo)   # 배율 없거나 박스가 crop 띠 밖이면 기하학 거리로 대체
        return out

    def _run_detection(self, frame: np.ndarray) -> list:
        """탐지 모델로 관심 클래스(차량/사람)의 바운딩박스 목록을 반환.
        반환: [(x1, y1, x2, y2, label), ...] (픽셀 좌표)"""
        result = self._detect_model.predict(frame, verbose=False)[0]
        names = result.names

        boxes = []
        for box in result.boxes:
            cls_id = int(box.cls[0])
            label = names[cls_id] if not isinstance(names, dict) else names.get(cls_id, str(cls_id))
            if label not in RELEVANT_CLASS_NAMES:
                continue
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])
            boxes.append((x1, y1, x2, y2, label))
        return boxes

    @staticmethod
    def _box_distance(depth_map: np.ndarray, x1: int, y1: int, x2: int, y2: int):
        """박스 영역 깊이 중앙값. NaN(crop 띠 밖)과 0 이하는 제외."""
        h, w = depth_map.shape[:2]
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None

        region = depth_map[y1:y2, x1:x2]
        valid = region[np.isfinite(region) & (region > 0)]
        if valid.size == 0:
            return None
        return float(np.median(valid))

    def _maybe_log_timing(self):
        interval = getattr(config, "CAMERA_TIMING_LOG_INTERVAL_SEC", 0)
        now = time.monotonic()
        if not interval or now - self._last_timing_log < interval:
            return
        n = max(1, self._timing["frames"])
        msg = (f"[TIMING] det {self._timing['det'] / n * 1000:.0f}ms, "
               f"depth {self._timing['depth'] / n * 1000:.0f}ms/frame "
               f"({self._timing['frames']} frames)")
        scale = f"{self._lite_scale:.3f}" if self._lite_scale is not None else "미정"
        msg += f", lite scale={scale}{' (고정)' if self._scale_fixed else ''}"
        self.connection_error.emit(msg)
        self._timing = {"det": 0.0, "depth": 0.0, "frames": 0}
        self._last_timing_log = now

    def stop(self):
        self._running = False
        self.wait(2000)
