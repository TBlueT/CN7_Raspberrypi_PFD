"""
카메라 + YOLO 리더 스레드 (NCNN 변환 버전)

모델을 라즈베리파이4 ARM CPU에 더 적합한 NCNN 포맷으로 변환해서 씁니다.

왜: 라즈베리파이4의 GPU(VideoCore VI)는 PyTorch 텐서 연산을 가속할 공식
경로가 없어서 "GPU 켜기"가 사실상 의미가 없습니다. 대신 Ultralytics 공식
가이드가 추천하는 대로, ARM CPU에 최적화된 NCNN으로 변환하면 같은 CPU에서도
더 빠르게 돌아갈 수 있습니다 (NCNN은 필요하면 Vulkan으로 GPU도 추가로 쓸 수
있음).

동작 순서:
  1. 탐지 모델로 프레임에서 차량/사람 등을 탐지 (바운딩박스 + 클래스)
  2. 깊이 모델로 같은 프레임의 픽셀별 깊이맵(미터) 추정
  3. 각 탐지 박스 영역 안의 깊이값 중앙값 = 그 물체까지의 거리
  4. 박스 중심의 화면 x좌표를 카메라 시야각 기준 각도로 변환
  5. (angle_deg, distance_m) 리스트로 scan_updated 발행
     - views.nd_view가 이 값을 받아 물체 하나당 마름모(TCAS 스타일)로 표시

라이다(YDLIDAR G2)가 차량 자외선차단 필름의 근적외선을 통과 못해서, 가시광선
카메라 + 딥러닝 방식으로 교체했습니다.

실기(라즈베리파이4)에서 탐지+깊이 두 모델의 NCNN 변환/추론이 정상 동작하고
PyTorch 직접 로드 대비 체감 속도가 개선됨을 확인했습니다.

참고:
- 최초 실행 시 각 모델마다 "PyTorch 로드 -> NCNN으로 export -> 다시 로드"
  과정을 한 번 거치므로 첫 실행이 평소보다 오래 걸립니다. 이후 실행부터는
  이미 변환된 <모델명>_ncnn_model 폴더를 바로 불러오므로 빠릅니다.
"""

import os

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

import config
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
    connection_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._detect_model = None
        self._depth_model = None
        self._depth_attr_warned = False

    def run(self):
        self._running = True

        if cv2 is None:
            self.connection_error.emit("opencv-python이 설치되어 있지 않습니다.")
            return
        if YOLO is None:
            self.connection_error.emit("ultralytics가 설치되어 있지 않습니다.")
            return

        while self._running:
            self._connect_and_run()
            if self._running:
                self.msleep(int(config.CAMERA_RECONNECT_INTERVAL_SEC * 1000))

    # ---------------------------------------------------------------
    # NCNN 변환/로딩 (V1과 다른 부분은 여기뿐)
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
        # export()가 반환하는 경로가 폴더 자체이거나 그 안의 파일일 수 있어
        # 방어적으로 실제 폴더 경로를 다시 계산해서 사용
        load_path = exported_path if os.path.isdir(exported_path) else ncnn_dir
        self.connection_error.emit(f"NCNN 변환 완료, 로드 중: {load_path}")
        return YOLO(load_path)

    def _load_models(self) -> bool:
        try:
            if self._detect_model is None:
                self._detect_model = self._load_or_export_ncnn(config.CAMERA_DETECT_MODEL_PATH)
            if self._depth_model is None:
                self._depth_model = self._load_or_export_ncnn(config.CAMERA_DEPTH_MODEL_PATH)
            return True
        except Exception as exc:
            self.connection_error.emit(f"모델 로드/변환 실패: {type(exc).__name__}: {exc}")
            return False

    # ---------------------------------------------------------------
    # 아래는 원본(camera_depth_reader.py)과 동일한 처리 로직
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
            return

        cap = self._open_camera()
        if cap is None or not cap.isOpened():
            self.connection_error.emit(f"카메라를 열 수 없습니다 (source={config.CAMERA_SOURCE})")
            return

        self.connection_error.emit("카메라 연결 및 추론 시작됨 (NCNN)")

        try:
            while self._running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    self.connection_error.emit("프레임 읽기 실패, 재연결 시도")
                    return

                objects = self._process_frame(frame)
                self.scan_updated.emit(objects)

                self.msleep(int(config.CAMERA_DEPTH_UPDATE_INTERVAL_SEC * 1000))
        except Exception as exc:
            self.connection_error.emit(f"추론 중 오류: {type(exc).__name__}: {exc}")
        finally:
            cap.release()

    def _process_frame(self, frame: np.ndarray) -> list:
        height, width = frame.shape[:2]

        detections = self._run_detection(frame)
        if not detections:
            return []

        depth_map = self._run_depth(frame)
        if depth_map is None:
            return []

        results = []
        for x1, y1, x2, y2, _label in detections:
            distance_m = self._box_distance(depth_map, x1, y1, x2, y2)
            if distance_m is None:
                continue

            center_x = (x1 + x2) / 2
            fraction = center_x / max(1, width - 1)   # 0.0(왼쪽 끝) ~ 1.0(오른쪽 끝)
            angle_deg = (fraction - 0.5) * config.CAMERA_HORIZONTAL_FOV_DEG
            angle_deg += config.CAMERA_ANGLE_OFFSET_DEG

            results.append((angle_deg, distance_m))

        return results

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

    def _run_depth(self, frame: np.ndarray):
        """깊이 모델로 픽셀별 깊이맵(미터)을 반환. 실패하면 None.

        ultralytics의 Results.depth는 DepthMap 객체(BaseTensor 상속)이고,
        실제 torch.Tensor/np.ndarray는 그 .data 속성에 들어있음."""
        result = self._depth_model.predict(frame, verbose=False)[0]

        depth = getattr(result, "depth", None)
        if depth is None:
            if not self._depth_attr_warned:
                self._depth_attr_warned = True
                attrs = [a for a in dir(result) if not a.startswith("_")]
                self.connection_error.emit(
                    f"result.depth가 없습니다 (NCNN 백엔드에서 다르게 나올 수 있음). "
                    f"result 속성들: {attrs}")
            return None

        data = getattr(depth, "data", depth)  # DepthMap.data가 실제 텐서
        arr = data.cpu().numpy() if hasattr(data, "cpu") else np.asarray(data)
        if arr.ndim == 3:
            arr = arr[0]
        return arr

    @staticmethod
    def _box_distance(depth_map: np.ndarray, x1: int, y1: int, x2: int, y2: int):
        h, w = depth_map.shape[:2]
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None

        region = depth_map[y1:y2, x1:x2]
        valid = region[region > 0]
        if valid.size == 0:
            return None
        return float(np.median(valid))

    def stop(self):
        self._running = False
        self.wait(2000)
