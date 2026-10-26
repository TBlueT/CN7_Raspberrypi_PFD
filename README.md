# CN7 Raspberry Pi PFD

현대 아반떼 CN7 차량용, 보잉 737-800 조종석 PFD(Primary Flight Display) 디자인을
참고해서 만든 라즈베리파이 기반 대시보드. 왼쪽은 자세계(PFD), 오른쪽은 ND(항법 화면)
자리로 구성됩니다. ND 는 지금 거리 링과 헤딩만 표시합니다 (카메라 전방 감지 기능은
삭제됨 — 이후 ADS-B 항공기 표시 등을 넣을 예정).

## 화면 구성

```
┌─────────────────────┬─────────────────────┐
│                      │                     │
│   PFD (자세계)         │   ND (전방 180도)       │
│   - 지평선/뱅크각        │   - 거리 링             │
│   - 속도/RPM 테이프       │   - 헤딩 표시           │
│   - 헤딩 나침반          │                     │
│   - SCC/LFA/HDA 모드바  │                     │
│                      │                     │
└─────────────────────┴─────────────────────┘
        400px                   400px
           (총 800x480, 라즈베리파이 정품 7인치 터치스크린)
```

## 하드웨어

| 구성요소 | 모델 | 연결 방식 |
|---|---|---|
| 메인 보드 | Raspberry Pi 4 | - |
| 디스플레이 | 라즈베리파이 정품 7인치 터치스크린 (800x480) | DSI |
| 자세 센서 | EBIMU-9DOFV5-R3 | USB 시리얼 (CP2102 UART 브릿지) |
| 차량 데이터 | 인포카 OBD 어댑터 (BLE) | Bluetooth LE (GATT, FFF0/FFF1/FFF2) |

전방 감지는 라이다(YDLIDAR G2, 자외선차단 필름에 막혀 실패) → 카메라 + 단안 깊이
추정(Sony QX10 + yolo26n + Lite-Mono) 순으로 시도했다가 정확도·부하 문제로 삭제했습니다.

## 소프트웨어 구조 (MVVM)

```
services/     하드웨어에서 원시 데이터를 읽는 스레드 (Model 공급원)
  imu_reader.py           EBIMU ASCII 프로토콜 파싱. 기본 Roll/Pitch/Yaw 외에
                          raw 자이로(sog1)/raw 가속도(soa1)도 파싱해 발행
  ahrs_filter.py          Mahony AHRS 상보필터 - raw 자이로+가속도로 롤/피치를
                          직접 추정 (원심력/가감속 영향을 자연스럽게 완화)
  can_reader.py           인포카 BLE로 OBD 표준 PID 조회 (속도 010D, RPM 010C;
                          SCC/LFA/HDA는 이 어댑터 한계로 현재 불가, 아래 참고)

models/       순수 데이터 구조 (dataclass)
  attitude_model.py   roll, pitch, yaw, IMU 내장기능 확인 여부
  vehicle_model.py    속도·RPM(실측치/표시치), 가속도, SCC/LFA/HDA 상태

viewmodels/   services의 데이터를 받아 저장만 함 (신호 없이 폴링 대상)
  pfd_viewmodel.py   Mahony 필터(원심력/가감속 보정)로 롤/피치 계산, 속도/RPM 평활,
                     데이터 유효성, 과속 판정
  nd_viewmodel.py    헤딩 보관

views/        순수 렌더링 (QWidget). 값 계산 없이 ViewModel을 고정 프레임레이트로
              읽어가서(QTimer 폴링) 그리기만 함
  pfd_view.py   ADI 볼, 속도/RPM 테이프(드럼식 현재값 박스), 헤딩 나침반
  nd_view.py    거리 링 + 헤딩 박스
  fail_flag.py  데이터 끊김 고장 플래그(ATT/SPD/RPM/HDG) 공용 그리기

config.py     화면 크기, 색상, 시리얼/BLE 설정, 각종 임계값 등 전역 설정
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

선회 중 원심력, 가감속 중 관성력이 가속도계에 섞이면 평지인데도 롤/피치가 틀어집니다.
Mahony 필터만으로는 회전이 몇 초 이상 이어질 때 서서히 기울어지므로(시속 40km·15°/s
좌회전 8초 시뮬레이션에서 롤 11°), 필터에 넣기 전에 **OBD 속도로 운동 가속도를 뺍니다**
(항공 AHRS 의 속도 보정과 같은 원리):

- 좌우: 원심 가속도 = 속도 × 요레이트 → 같은 시뮬레이션에서 롤 오차 0.6°
- 앞뒤: OBD 속도 변화율 → 0.6g 급제동 피치 오차 20.8° → 약 7° (OBD 0.7초 간격 한계)
- OBD 속도가 끊기면 보정 없이 필터만 동작

**차에서 처음 확인할 것**: 회전할 때 `[AHRS] 원심력 점검: 실측/계산 = …` 로그가 나옵니다.
+1 근처면 정상, -1 근처면 `IMU_LATERAL_SIGN` 반대, 0 근처면 `IMU_LATERAL_AXIS` 확인,
약 9.8/0.1 이면 `ACCEL_UNIT_IS_G` 확인. 설정이 맞으면 `AHRS_COMP_DIAGNOSTIC = False` 로 끄면 됨.

## 속도/RPM 테이프 (B737 NG 스타일)

- 회색 테이프, 속도는 10 km/h 눈금 / 20 km/h 숫자, RPM 은 100 / 500 rpm
  (오른쪽 테이프는 예전 고도 자리 — 고도 센서가 없어 엔진 RPM 으로 교체, OBD PID 010C)
- 현재값 박스: 검정 바탕 + 흰 테두리 + ADI 쪽 포인터. 속도는 일의 자리,
  RPM 은 끝 두 자리(50 단위)가 드럼처럼 굴러감. `RPM_REDLINE`(6500) 이상이면
  빨강/검정 줄무늬 + 박스 빨강, RPM 이 3초 이상 안 오면 `RPM` 고장 표시
- 초록 트렌드 화살표 = `SPEED_TREND_SEC`(3초) 뒤 예상 속도
- SCC 설정속도: 테이프 위 마젠타 숫자 + 마젠타 버그 (SCC 데이터 수신 시)
- 과속 바버폴: `SPEED_BARBER_POLE_KPH`(110) 이상 빨강/검정 줄무늬, `None`이면 끔
- 헤딩: 나침반 원판이 헤딩만큼 1:1 회전, 위쪽 고정 포인터 + 현재 헤딩 박스.
  10° 마다 두 자리 숫자(24=240°), N/E/S/W, 5°/10° 눈금. 반지름 220·중심 (200,640)으로
  크게 잡아 숫자가 겹치지 않게 함 (화면에는 약 ±45° 보임)

## 데이터 끊김 표시 (B737 고장 플래그 스타일)

센서 값이 일정 시간 안 들어오면 마지막 값을 그대로 보여주지 않고 주황 테두리 플래그로 바꿉니다.

| 데이터 | 표시 | 기준 (`config.py`) |
|---|---|---|
| IMU 자세 | ADI 볼 비우고 `ATT` | `IMU_STALE_SEC = 0.5` |
| IMU 헤딩 | PFD 헤딩 나침반 / ND 헤딩 박스 `HDG` | `IMU_STALE_SEC` |
| OBD 속도 | 속도 테이프 비우고 `SPD` | `SPEED_STALE_SEC = 3.0` |
| OBD RPM | RPM 테이프 비우고 `RPM` | `RPM_STALE_SEC = 3.0` |

부팅 직후 한 번도 못 받은 상태도 끊김으로 표시하고, 데이터가 다시 들어오면 바로 복귀합니다.

## 설치

```bash
git clone https://github.com/TBlueT/CN7_Raspberrypi_PFD.git ~/cn7
cd ~/cn7
pip install pyserial bleak --break-system-packages   # PyQt6 는 기존 설치 방식 그대로
```

## 실행 전 `config.py`에서 확인할 것

- `IMU_SERIAL_PORT` — EBIMU가 잡히는 실제 포트 (`ls /dev/serial/by-id/`)
- `IMU_INIT_COMMANDS` — `<soa1>`(가속도), `<sog1>`(자이로) 등 부팅시 자동 전송 명령
- `IMU_EXTRA_FIELD_ORDER`, `GYRO_SIGN`, `ACCEL_SIGN` — 실기 응답에 맞게 조정 필요
- `MAHONY_KP` / `MAHONY_KI` — 필터 반응성 튜닝 (기본 0.3 / 0.0)
- `OBD_BLE_ADDRESS` — 인포카 어댑터 MAC 주소 (`ble_scan.py`로 재확인 가능)
- `HEADING_OFFSET_DEG` — 헤딩 오프셋 (IMU 장착 방향/지자기 보정)
- `RPM_REDLINE`, `SPEED_BARBER_POLE_KPH` — 레드라인 / 과속 경고 기준

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
런처는 부팅 화면(`CN7 PFD STARTUP`)에 NETWORK / BACKUP / UPDATE / LAUNCH 단계와
업데이트된 커밋, 실패 원인을 표시하고 3초 뒤 main.py를 실행합니다. 화면을 띄우지
못하면 콘솔 출력 방식으로 자동 전환되어 부팅이 멈추지 않습니다.

## 알려진 제약/TODO

- **RPM 갱신 주기**: 속도(010D)와 RPM(010C)을 번갈아 요청. 응답 끝 프롬프트('>')를
  받는 즉시 다음 요청을 보내므로 보통 한 바퀴 0.3~0.5초, 최악(프롬프트 미수신)엔 0.9초
- **SCC/LFA/HDA**: 인포카 어댑터가 raw CAN 모니터 모드(`ATMA`)를 지원하지 않아,
  표준 PID 요청-응답 범위를 벗어나는 제조사 고유 데이터는 현재 못 받아옴.
  대안으로 UDS Mode 22(진단 세션) 방식이나 별도 CAN 트랜시버(MCP2515 등) 하드웨어
  검토 중
- **헤딩**: 오프셋을 맞춰도 다시 틀어지는 현상 — EBIMU 지자기 사용 모드 / 차체 자기
  간섭(방향별 편차) 확인 필요
- **ND**: 표시 대상 없음 (거리 링·헤딩만). ADS-B 수신 표시 검토 중
- **IMU 내장 안정화 기능 확인**(`<savc1>` 등)은 정확한 명령어 미검증 상태

## 참고 도구 스크립트

- `ble_scan.py` — BLE 기기 서비스/특성 UUID 탐색
- `at_test.py`, `ble_pid_test.py` — OBD 어댑터 AT 명령/PID 응답 테스트
- `scc_lfa_hda_probe.py` — SCC/LFA/HDA 후보 PID 반복 조회 테스트
