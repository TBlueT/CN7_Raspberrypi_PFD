"""
데스크탑에서 카메라+YOLO 탐지+깊이추정 파이프라인을 미리 확인하는 스크립트

라즈베리파이/소니 QX10 없이, 노트북/PC의 일반 웹캠으로 yolo26n.pt(탐지) +
yolo26n-depth.pt(깊이추정) 조합이 실제로 잘 동작하는지, 결과가 말이 되는지
미리 눈으로 확인할 수 있습니다.

services.camera_depth_reader.CameraDepthReaderThread의 실제 메서드
(_run_detection, _run_depth, _box_distance)를 그대로 재사용하기 때문에,
여기서 잘 되면 라즈베리파이에서도 같은 로직이 그대로 동작한다고 보면 됩니다
(속도 차이는 있겠지만 로직 정확성은 보장됨).

사용법:
    pip install ultralytics opencv-python numpy
    python3 test_camera_yolo_desktop.py

    기본은 config.py의 CAMERA_SOURCE 설정을 그대로 따릅니다
    ("usb"면 일반 웹캠, "sony_qx10"이면 소니 QX10 Wi-Fi 라이브뷰).
    일반 웹캠으로 강제 테스트하려면 인덱스를 인자로 주세요:
        python3 test_camera_yolo_desktop.py 0

화면에 뜬 창에서 'q' 키를 누르면 종료됩니다.

주의: 처음 실행하면 yolo26n.pt / yolo26n-depth.pt를 인터넷에서 자동
다운로드하느라 시간이 좀 걸릴 수 있습니다.
"""

import sys
import time

import cv2
from PyQt6.QtCore import QCoreApplication

import config
from services.camera_depth_reader import CameraDepthReaderThread


def main():
    # QThread/pyqtSignal이 제대로 동작하려면 QCoreApplication 인스턴스가 필요함
    # (GUI가 필요한 건 아니라서 QApplication 대신 QCoreApplication으로 충분)
    _app = QCoreApplication(sys.argv)

    reader = CameraDepthReaderThread()
    reader.connection_error.connect(lambda msg: print(f"[상세] {msg}"))

    # config.CAMERA_SOURCE 그대로 따름 ("usb"면 일반 웹캠, "sony_qx10"이면 소니
    # QX10 Wi-Fi 라이브뷰). 명령행에 인덱스를 주면 그때만 강제로 일반 웹캠 사용.
    if len(sys.argv) > 1:
        camera_index = int(sys.argv[1])
        print(f"명령행 인자로 웹캠 인덱스 {camera_index} 강제 사용")
        cap = cv2.VideoCapture(camera_index)
    else:
        print(f"config.CAMERA_SOURCE={config.CAMERA_SOURCE!r} 기준으로 카메라 여는 중...")
        cap = reader._open_camera()

    if cap is None or not cap.isOpened():
        print(f"카메라를 열 수 없습니다 (source={config.CAMERA_SOURCE}). "
              f"일반 웹캠으로 강제 테스트하려면: python3 {sys.argv[0]} 0")
        return
    print("모델 로딩 중 (처음 실행이면 자동 다운로드로 시간이 걸릴 수 있음)...")
    if not reader._load_models():
        print("모델 로드 실패. 위 [상세] 로그에서 실제 원인을 확인하세요.")
        cap.release()
        return
    print("모델 로드 완료. 창을 클릭하고 'q' 키를 누르면 종료됩니다.")

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("프레임 읽기 실패")
                break

            start = time.time()
            detections = reader._run_detection(frame)
            depth_map = reader._run_depth(frame)
            elapsed = time.time() - start

            height, width = frame.shape[:2]

            for x1, y1, x2, y2, label in detections:
                distance_m = None
                if depth_map is not None:
                    distance_m = reader._box_distance(depth_map, x1, y1, x2, y2)

                center_x = (x1 + x2) / 2
                fraction = center_x / max(1, width - 1)
                angle_deg = (fraction - 0.5) * config.CAMERA_HORIZONTAL_FOV_DEG

                text = label
                if distance_m is not None:
                    text += f" {distance_m:.1f}m ({angle_deg:+.0f}deg)"
                else:
                    text += " (거리 계산 실패)"

                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(frame, text, (x1, max(15, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
                print(f"[{label}] 거리={distance_m} 각도={angle_deg:.1f}")

            fps_text = f"{1.0 / elapsed:.1f} fps" if elapsed > 0 else "..."
            cv2.putText(frame, fps_text, (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)

            cv2.imshow("Camera YOLO Test (q to quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
