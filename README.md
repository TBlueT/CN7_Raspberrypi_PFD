# CN7 Raspberry Pi PFD

현대 아반떼 CN7 차량용, 보잉 737-800 조종석 PFD(Primary Flight Display) 디자인을
참고해서 만든 라즈베리파이 기반 대시보드. 왼쪽은 자세계(PFD), 오른쪽은 2D 라이다
레이더(ND) 화면으로 구성됩니다.

## 화면 구성

```
┌─────────────────────┬─────────────────────┐
│                      │                     │
│   PFD (자세계)         │   ND (전방 180도 레이더)  │
│   - 지평선/뱅크각        │   - 라이다 스캔 포인트       │
│   - 속도/고도 테이프      │   - 거리 링(3m/5m/8m)     │
│   - 헤딩 아크           │   - 헤딩 표시             │
│   - SCC/LFA/HDA 모드바  │                     │
│                      │                     │
└─────────────────────┴─────────────────────┘
        400px                   400px
           (총 800x480, 라즈베리파이 정품 7인치 터치스크린)
```

## 하드웨어

| 구성요소 | 모델 | 연결 방식 |
|---|---|---|
| 메인 보드 | Raspberry Pi 3 | - |
| 디스플레이 | 라즈베리파이 정품 7인치 터치스크린 (800x480) | DSI |
| 자세 센서 | EBIMU-9DOFV5-R3 | USB 시리얼 (CP2102 UART 브릿지) |
| 차량 데이터 | 인포카 OBD 어댑터 (BLE) | Bluetooth LE (GATT, FFF0/FFF1/FFF2) |
| 라이다 | YDLIDAR G2 | USB 시리얼 (YDLidar-SDK Python 바인딩) |

## 소프트웨어 구조 (MVVM)

```
services/     하드웨어에서 원시 데이터를 읽는 스레드 (Model 공급원)
  imu_reader.py    EBIMU ASCII 프로토콜 파싱, 자세각(+선택적 가속도) 발행
  can_reader.py    인포카 BLE로 차량 CAN 데이터 조회(속도/SCC/LFA/HDA)
  lidar_reader.py  YDLIDAR G2 스캔 포인트 발행

models/       순수 데이터 구조 (dataclass)
  attitude_model.py   roll, pitch, yaw
  vehicle_model.py    속도, SCC/LFA/HDA 상태
  scan_model.py       라이다 포인트 리스트

viewmodels/   services의 데이터를 받아 저장만 함 (신호 없이 폴링 대상)
  pfd_viewmodel.py   자세/차량 상태 보관, 선회시 롤 노이즈 스무딩 로직 포함
  nd_viewmodel.py    헤딩/스캔 데이터 보관

views/        순수 렌더링 (QWidget). 값 계산 없이 ViewModel을 고정 프레임레이트로
              읽어가서(QTimer 폴링) 그리기만 함
  pfd_view.py
  nd_view.py

config.py     화면 크기, 색상, 시리얼 포트, 각종 임계값 등 전역 설정
main.py       services/viewmodels/views를 생성하고 서로 연결(wiring)
launcher.py   부팅 시 인터넷 연결되면 GitHub에서 최신 코드로 업데이트 후 main.py 실행
```

데이터 흐름: **services → viewmodels → views** (단방향). views에는 `set_*` 류
메서드가 없고, viewmodels에도 View에 보내는 신호가 없습니다 — views가
`config.FRAME_RATE_HZ` 주기로 스스로 최신 상태를 읽어가는 폴링 방식이라,
센서가 아무리 빨리 갱신돼도 화면 갱신 속도는 이 값으로 제한됩니다.

## 설치

```bash
git clone https://github.com/TBlueT/CN7_Raspberrypi_PFD.git ~/cn7
cd ~/cn7
pip install -r requirements.txt --break-system-packages
```

YDLIDAR G2를 쓰려면 공식 SDK(Python 바인딩)도 별도 빌드해야 합니다:
```bash
sudo apt install -y cmake pkg-config python3-dev swig git build-essential
git clone https://github.com/YDLIDAR/YDLidar-SDK.git
cd YDLidar-SDK && mkdir build && cd build
cmake -DBUILD_PYTHON=ON .. && make && sudo make install
```

## 실행 전 `config.py`에서 확인할 것

- `IMU_SERIAL_PORT` — EBIMU가 잡히는 실제 포트 (`ls /dev/serial/by-id/`)
- `LIDAR_SERIAL_PORT` — YDLIDAR G2가 잡히는 실제 포트
- `OBD_BLE_ADDRESS` — 인포카 어댑터 MAC 주소 (`ble_scan.py`로 재확인 가능)
- `OBD_DBC_PATH` — 기본값은 같은 폴더의 `hyundai_can_clean.dbc` 자동 참조

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

- **고도**: 센서 미연동, 항상 0 고정 표시 (`set_altitude()`는 존재하나 미호출)
- **SCC/LFA/HDA**: 인포카 어댑터가 raw CAN 모니터 모드(`ATMA`)를 지원하지 않아,
  표준 PID 요청-응답 범위를 벗어나는 제조사 고유 데이터는 현재 못 받아옴.
  대안으로 UDS Mode 22(진단 세션) 방식이나 별도 CAN 트랜시버(MCP2515 등) 하드웨어
  검토 중
- **롤 노이즈 보정**: 선회/제동 시 원심력으로 인한 롤 값 왜곡을 완화하기 위해
  가속도(우선) 또는 헤딩 변화율(폴백) 기반 스무딩 적용 (`config.ROLL_STABILIZE_*`)
- **라이다 각도 컨벤션**: 실장착 방향에 따라 `config.LIDAR_ANGLE_OFFSET_DEG` 보정 필요

## 참고 도구 스크립트

- `ble_scan.py` — BLE 기기 서비스/특성 UUID 탐색
- `at_test.py`, `ble_pid_test.py` — OBD 어댑터 AT 명령/PID 응답 테스트
- `scc_lfa_hda_probe.py` — SCC/LFA/HDA 후보 PID 반복 조회 테스트
