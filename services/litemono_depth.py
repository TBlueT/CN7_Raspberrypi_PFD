"""
Lite-Mono 단안 깊이 추정 (기본: lite-mono-tiny, 2.2M 파라미터, 입력 640x192)

- KITTI 주행 영상으로 자기지도 학습된 모델이라 출력은 "상대 깊이"(임의 스케일)다.
  미터 변환 배율은 camera_depth_reader 에서 기하학 거리로 자동 보정한다.
- 입력이 가로로 긴 640:192 비율이라, 일반 카메라 영상은 지평선 주변 띠를 잘라서(crop)
  넣는다. 띠 밖은 NaN (그 영역 물체는 거리 없음).

런타임 (config.LITE_MONO_RUNTIME):
  "torch" : Lite-Mono 저장소(networks) + encoder.pth/depth.pth 로 직접 실행
  "onnx"  : onnxruntime 으로 실행. .onnx 가 없으면 torch 모델로 한 번 변환해서 저장.
            .onnx 만 있으면 라즈베리파이에 저장소/torch 가 없어도 동작
            (데스크탑에서 변환해서 복사해도 됨).
"""

import os
import sys

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

# monodepth2/Lite-Mono 의 disp_to_depth(min_depth=0.1, max_depth=100)
_MIN_DISP = 1.0 / 100.0
_MAX_DISP = 1.0 / 0.1


class LiteMonoDepth:
    def __init__(self, repo_dir, weights_dir, model_name="lite-mono-tiny",
                 runtime="torch", onnx_path=None, num_threads=4,
                 horizon_in_band=0.45, crop=True):
        self.repo_dir = repo_dir
        self.weights_dir = weights_dir
        self.model_name = model_name
        self.runtime = runtime
        self.onnx_path = onnx_path or os.path.join(weights_dir, f"{model_name}.onnx")
        self.num_threads = num_threads
        self.horizon_in_band = horizon_in_band
        self.crop = crop
        self.in_w, self.in_h = 640, 192
        self._torch_model = None
        self._ort_session = None
        self._ort_input = None
        self.log = []   # 로딩 과정 메시지 (호출 측에서 로그로 내보냄)

        if runtime == "onnx":
            self._init_onnx()
        else:
            self._init_torch()

    # ---------------- 로딩 ----------------
    def _build_torch_model(self):
        import torch

        if not os.path.isdir(os.path.join(self.repo_dir, "networks")):
            raise FileNotFoundError(f"Lite-Mono 저장소(networks 폴더) 없음: {self.repo_dir}")
        enc_path = os.path.join(self.weights_dir, "encoder.pth")
        dec_path = os.path.join(self.weights_dir, "depth.pth")
        if not os.path.isfile(enc_path) or not os.path.isfile(dec_path):
            raise FileNotFoundError(f"encoder.pth/depth.pth 없음: {self.weights_dir}")

        if self.repo_dir not in sys.path:
            sys.path.insert(0, self.repo_dir)
        import networks  # Lite-Mono 저장소의 networks 패키지

        enc_dict = torch.load(enc_path, map_location="cpu", weights_only=False)
        dec_dict = torch.load(dec_path, map_location="cpu", weights_only=False)
        self.in_h = int(enc_dict.get("height", 192))
        self.in_w = int(enc_dict.get("width", 640))

        encoder = networks.LiteMono(model=self.model_name, height=self.in_h, width=self.in_w)
        md = encoder.state_dict()
        encoder.load_state_dict({k: v for k, v in enc_dict.items() if k in md})
        decoder = networks.DepthDecoder(encoder.num_ch_enc, scales=range(3))
        md = decoder.state_dict()
        decoder.load_state_dict({k: v for k, v in dec_dict.items() if k in md})

        class _Wrapped(torch.nn.Module):
            def __init__(self, enc, dec):
                super().__init__()
                self.enc, self.dec = enc, dec

            def forward(self, x):
                return self.dec(self.enc(x))[("disp", 0)]

        return _Wrapped(encoder, decoder).eval()

    def _init_torch(self):
        import torch

        torch.set_num_threads(self.num_threads)
        self._torch_model = self._build_torch_model()
        self.log.append(f"Lite-Mono(torch) 로드 완료: {self.model_name} {self.in_w}x{self.in_h}")

    def _init_onnx(self):
        import onnxruntime as ort

        if not os.path.isfile(self.onnx_path):
            self.log.append(f"ONNX 파일이 없어 변환합니다: {self.onnx_path}")
            self._export_onnx()

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.num_threads
        self._ort_session = ort.InferenceSession(
            self.onnx_path, opts, providers=["CPUExecutionProvider"])
        inp = self._ort_session.get_inputs()[0]
        self._ort_input = inp.name
        shape = inp.shape   # [1, 3, H, W]
        if isinstance(shape[2], int) and isinstance(shape[3], int):
            self.in_h, self.in_w = shape[2], shape[3]
        self.log.append(f"Lite-Mono(onnx) 로드 완료: {self.onnx_path} {self.in_w}x{self.in_h}")

    def _export_onnx(self):
        import torch

        model = self._build_torch_model()
        dummy = torch.rand(1, 3, self.in_h, self.in_w)
        kwargs = {"input_names": ["input"], "output_names": ["disp"], "opset_version": 17}
        try:
            torch.onnx.export(model, dummy, self.onnx_path, dynamo=False, **kwargs)
        except TypeError:   # 구버전 torch 는 dynamo 인자가 없음
            torch.onnx.export(model, dummy, self.onnx_path, **kwargs)
        self.log.append(f"ONNX 변환 완료: {self.onnx_path}")

    # ---------------- 추론 ----------------
    def band_rows(self, h, w, horizon_row):
        """crop 할 가로 띠의 (top, bottom). 지평선이 띠의 horizon_in_band 위치에 오게."""
        if not self.crop:
            return 0, h
        band_h = min(h, int(round(w * self.in_h / self.in_w)))
        top = int(round(horizon_row - self.horizon_in_band * band_h))
        top = max(0, min(h - band_h, top))
        return top, top + band_h

    def _infer_disp(self, x_nchw: np.ndarray) -> np.ndarray:
        if self._ort_session is not None:
            return self._ort_session.run(None, {self._ort_input: x_nchw})[0][0, 0]
        import torch

        with torch.no_grad():
            return self._torch_model(torch.from_numpy(x_nchw))[0, 0].numpy()

    def predict(self, frame_bgr: np.ndarray, horizon_row: float) -> np.ndarray:
        """원본 크기의 상대 깊이맵(float32) 반환. crop 띠 밖은 NaN."""
        h, w = frame_bgr.shape[:2]
        top, bottom = self.band_rows(h, w, horizon_row)
        roi = cv2.resize(frame_bgr[top:bottom], (self.in_w, self.in_h),
                         interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
        x = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0

        disp = self._infer_disp(x)
        depth = 1.0 / (_MIN_DISP + (_MAX_DISP - _MIN_DISP) * disp)
        depth = cv2.resize(depth.astype(np.float32), (w, bottom - top))

        full = np.full((h, w), np.nan, np.float32)
        full[top:bottom] = depth
        return full
