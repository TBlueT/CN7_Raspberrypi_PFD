# CN7 Raspberry Pi PFD

현대 아반떼 CN7 차량용, 보잉 737-800 조종석 PFD(Primary Flight Display) 디자인을
참고해서 만든 라즈베리파이 기반 대시보드. 왼쪽은 자세계(PFD), 오른쪽은 카메라
기반 전방 감지 화면(ND, TCAS 스타일)으로 구성됩니다.

## 화면 구성

```
┌─────────────────────┬─────────────────────┐
│                      │                     │
│   PFD (자세계)         │   ND (전방 180도, TCAS풍) │
│   - 지평선/뱅크각        │   - 물체당 마름모 1개 + 거리 │
│   - 속도/고도 테이프      │   - 거리 링, 시야각 경계선   │
│     (속도는 슬라이드 애니메이션)│   - 헤딩 표시             │
│   - 헤딩 아크           │                     │
│   - SCC/LFA/HDA 모드바  │                     │
│                      │                     │
└─────────────────────┴─────────────────────┘
        400px                   400px
           (총 800x480, 라즈베리파이 정품 7인치 터치스크린)
```

## 하드웨어

| 구성요소 | 모델 | 연결 방식 |
|---|---|---|
| 메인 보드 | Raspberry Pi 4 | - (카메라+깊이추정 모델 실행을 위해 3에서 업그레이드) |
| 디스플레이 | 라즈베리파이 정품 7인치 터치스크린 (800x480) | DSI |
| 자세 센서 | EBIMU-9DOFV5-R3 | USB 시리얼 (CP2102 UART 브릿지) |
| 차량 데이터 | 인포카 OBD 어댑터 (BLE) | Bluetooth LE (GATT, FFF0/FFF1/FFF2) |
| 전방 감지 카메라 | Sony DSC-QX10 (또는 일반 USB 웹캠) | Wi-Fi 라이브뷰 (또는 USB) |

**라이다(YDLIDAR G2)에서 카메라로 교체한 이유**: 차량 자외선차단 필름이 라이다의
근적외선 파장을 막아서 실내 장착 시 거의 작동하지 않음. 가시광선 카메라는 이
문제가 없어 교체함 (`services/lidar_reader.py`는 참고용으로 남아있으나 `main.py`
에서 더 이상 사용 안 함).

## 소프트웨어 구조 (MVVM)

```
services/     하드웨어에서 원시 데이터를 읽는 스레드 (Model 공급원)
  imu_reader.py           EBIMU ASCII 프로토콜 파싱. 기본 Roll/Pitch/Yaw 외에
                          raw 자이로(sog1)/raw 가속도(soa1)도 파싱해 발행
  ahrs_filter.py          Mahony AHRS 상보필터 - raw 자이로+가속도로 롤/피치를
                          직접 추정 (원심력/가감속 영향을 자연스럽게 완화)
  can_reader.py           인포카 BLE로 차량 CAN 데이터 조회 (속도는 PID 010D;
                          SCC/LFA/HDA는 이 어댑터 한계로 현재 불가, 아래 참고)
  camera_depth_reader.py  카메라 프레임 → YOLO26-Depth로 깊이맵 → 물체 단위
                          (각도,거리) 클러스터링 → 발행
  sony_qx10_capture.py    소니 QX10 Wi-Fi 라이브뷰를 cv2.VideoCapture와 같은
                          인터페이스로 감싼 캡처 클래스 (SSDP 탐색 포함)

models/       순수 데이터 구조 (dataclass)
  attitude_model.py   roll, pitch, yaw, IMU 내장기능 확인 여부
  vehicle_model.py    속도(실측치/애니메이션 표시치), SCC/LFA/HDA 상태
  scan_model.py       감지된 물체 (각도,거리) 리스트

viewmodels/   services의 데이터를 받아 저장만 함 (신호 없이 폴링 대상)
  pfd_viewmodel.py   Mahony 필터로 롤/피치 계산, 속도 슬라이드 애니메이션 타이머
  nd_viewmodel.py    헤딩/감지물체 데이터 보관

views/        순수 렌더링 (QWidget). 값 계산 없이 ViewModel을 고정 프레임레이트로
              읽어가서(QTimer 폴링) 그리기만 함
  pfd_view.py   ADI 볼, 속도/고도 테이프(속도는 오도미터 스타일 슬라이드), 헤딩 아크
  nd_view.py    TCAS 스타일 마름모 마커 + 거리 라벨, 카메라 시야각 경계선

config.py     화면 크기, 색상, 시리얼/카메라 소스, 각종 임계값 등 전역 설정
main.py       services/viewmodels/views를 생성하고 서로 연결(wiring)
launcher.py   부팅 시 인터넷 연결되면 GitHub에서 최신 코드로 업데이트 후 main.py 실행
```

데이터 흐름: **services → viewmodels → views** (단방향). views에는 `set_*` 류
메서드가 없고, viewmodels에도 View에 보내는 신호가 없습니다 — views가
`config.FRAME_RATE_HZ` 주기로 스스로 최신 상태를 읽어가는 폴링 방식이라,
센서가 아무리 빨리 갱신돼도 화면 갱신 속도는 이 값으로 제한됩니다 (200회
연속 데이터 갱신 → 실제 repaint 14회로 검증됨).

## 자세각(롤/피치) 계산 방식

EBIMU가 자체 계산해서 주는 Roll/Pitch를 그대로 쓰지 않고, `services/ahrs_filter.py`의
**Mahony AHRS 상보필터**로 raw 자이로(`sog1`)+raw 가속도(`soa1`, 중력 포함)를 직접
받아 롤/피치를 계산합니다. 요(yaw)는 EBIMU 자체 출력(지자기 보정 포함)을 그대로 씀.

이 방식을 쓰는 이유: 선회 중 원심력, 가감속 중 관성력이 가속도계에 섞여 들어와
실제로는 평지인데도 롤/피치가 틀어지는 노이즈가 생기는데, 자이로 각속도가 클수록
가속도계 보정 영향력이 자연스럽게 줄어드는 Mahony 필터 구조가 CAN 속도 등 외부
데이터 없이도 이 문제를 안정적으로 완화합니다. (요레이트 EMA 스무딩, `atan2` 기반
사후보정 등 더 단순한 방식들을 먼저 시도했으나 Mahony로 대체됨 — 원심가속도
72m/s² 시뮬레이션에서 사후보정 방식은 82도까지 새는 반면 Mahony는 6도만 샘)

## 전방 감지 (카메라 + YOLO26-Depth)

1. 카메라 프레임에서 관심영역(ROI, 하늘/보닛 제외)만 추출
2. YOLO26-Depth로 픽셀별 미터 단위 깊이맵 획득
3. 각 세로 열의 최소 깊이 = 그 방향 최근접 장애물 거리
4. 인접 열을 깊이 유사도로 클러스터링 → 노이즈성 좁은 클러스터 제거
5. 클러스터 중심을 카메라 시야각 기준 각도로 변환 → (각도,거리) 발행
6. ND 화면에 물체 하나당 마름모 마커 + 거리 라벨로 표시 (B737 TCAS 스타일)

## 설치

```bash
git clone https://github.com/TBlueT/CN7_Raspberrypi_PFD.git ~/cn7
cd ~/cn7
pip install pyserial bleak cantools opencv-python ultralytics numpy --break-system-packages
```

## 실행 전 `config.py`에서 확인할 것

- `IMU_SERIAL_PORT` — EBIMU가 잡히는 실제 포트 (`ls /dev/serial/by-id/`)
- `IMU_INIT_COMMANDS` — `<soa1>`(가속도), `<sog1>`(자이로) 등 부팅시 자동 전송 명령
- `IMU_EXTRA_FIELD_ORDER`, `GYRO_SIGN`, `ACCEL_SIGN` — 실기 응답에 맞게 조정 필요
- `MAHONY_KP` / `MAHONY_KI` — 필터 반응성 튜닝 (기본 0.3 / 0.0)
- `OBD_BLE_ADDRESS` — 인포카 어댑터 MAC 주소 (`ble_scan.py`로 재확인 가능)
- `OBD_DBC_PATH` — 기본값은 같은 폴더의 `hyundai_can_clean.dbc` 자동 참조
- `CAMERA_SOURCE` — `"sony_qx10"` 또는 `"usb"`
- `SONY_QX10_FIXED_ENDPOINT_URL` — SSDP 자동탐색 실패 시 카메라 IP 직접 지정
- `CAMERA_DEPTH_MODEL_PATH`, `CAMERA_HORIZONTAL_FOV_DEG`, `CAMERA_ROI_TOP/BOTTOM_FRAC`

## 실행

```bash
export QT_QPA_PLATFORM=linuxfb:fb=/dev/fb0:size=800x480
export QT_QPA_FB_TSLIB=1
export TSLIB_FBDEVICE=/dev/fb0
export TSLIB_TSDEVICE=/dev/input/event0
python3 main.py
```

또는 부팅 시 자동 업데이트까지 원하면 `main.py` 대신 `launcher.py` 실행
(systemd 서비스의 `ExecStart`를 `launcher.py`로 지정).

## 알려진 제약/TODO

- **고도**: 센서 미연동, 항상 0 고정 표시
- **SCC/LFA/HDA**: 인포카 어댑터가 raw CAN 모니터 모드(`ATMA`)를 지원하지 않아,
  표준 PID 요청-응답 범위를 벗어나는 제조사 고유 데이터는 현재 못 받아옴.
  대안으로 UDS Mode 22(진단 세션) 방식이나 별도 CAN 트랜시버(MCP2515 등) 하드웨어
  검토 중
- **YOLO26-Depth 결과 파싱**: `ultralytics` 버전에 따라 깊이맵 속성명이 다를 수
  있어 `camera_depth_reader.py`의 `_run_inference()` 조정이 필요할 수 있음
- **소니 QX10 연동**: SSDP 자동탐색/`startLiveview` 응답 구조는 실기 미검증 —
  안 되면 `SONY_QX10_FIXED_ENDPOINT_URL` 직접 지정하거나 응답 구조 확인 필요
- **IMU 내장 안정화 기능 확인**(`<savc1>` 등)은 정확한 명령어 미검증 상태

## 참고 도구 스크립트

- `ble_scan.py` — BLE 기기 서비스/특성 UUID 탐색
- `at_test.py`, `ble_pid_test.py` — OBD 어댑터 AT 명령/PID 응답 테스트
- `scc_lfa_hda_probe.py` — SCC/LFA/HDA 후보 PID 반복 조회 테스트
