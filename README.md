# CN7 Raspberry Pi PFD

현대 아반떼 CN7 차량용, 보잉 737-800 조종석 PFD(Primary Flight Display) 디자인을
참고해서 만든 라즈베리파이 기반 대시보드. 왼쪽은 자세계(PFD), 오른쪽은 B737/787 EICAS
시놉틱 스타일의 차량 시스템 화면(엔진 온도, 타이어, 문 잠금, 기어, 조향각, 연료 등)으로
구성됩니다.

## 화면 구성

```
┌─────────────────────┬─────────────────────┐
│                      │                     │
│   PFD (자세계)         │   차량 시스템 (EICAS풍)  │
│   - 지평선/뱅크각        │   - 외기온도/배터리 전압   │
│   - 속도/RPM 테이프       │   - 부하/냉각수/오일 계기  │
│   - 헤딩 나침반          │   - 타이어/문잠금/기어     │
│                      │   - 브레이크/조향각/연료    │
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
  imu_reader.py           EBIMU ASCII 프로토콜 파싱 (Roll/Pitch/Yaw, 추가 필드
                          자이로/가속도는 파싱만)
  can_reader.py           인포카 BLE로 OBD 조회 (속도 010D, RPM 010C 매 바퀴 +
                          차량 시스템 항목을 주기별로 하나씩 끼워서 요청)
  obd_decode.py           ELM327 응답 파싱 + 차량 시스템 항목 정의/해석 (OBDb 규칙)

models/       순수 데이터 구조 (dataclass)
  attitude_model.py   roll, pitch, yaw, IMU 내장기능 확인 여부
  vehicle_model.py    속도·RPM(실측치/표시치), 가속도, SCC/LFA/HDA 상태
  vehicle_sys_model.py  차량 시스템 화면 값 (조향각, 기어, 타이어, 온도 등)

viewmodels/   services의 데이터를 받아 저장만 함 (신호 없이 폴링 대상)
  pfd_viewmodel.py   IMU 자세값 보관(필터 없음), 속도/RPM 평활, 데이터 유효성, 과속 판정
  sys_viewmodel.py   차량 시스템 값 보관 + 항목별 유효 시간 판단

views/        순수 렌더링 (QWidget). 값 계산 없이 ViewModel을 고정 프레임레이트로
              읽어가서(QTimer 폴링) 그리기만 함
  pfd_view.py   ADI 볼, 속도/RPM 테이프(드럼식 현재값 박스), 헤딩 나침반
  sys_view.py   차량 시스템 화면 (EICAS 원형 계기, 차 평면도, 막대/눈금)
  fail_flag.py  데이터 끊김 고장 플래그(ATT/SPD/RPM/HDG) 공용 그리기

config.py     화면 크기, 색상, 시리얼/BLE 설정, 각종 임계값 등 전역 설정
main.py       services/viewmodels/views를 생성하고 서로 연결(wiring)
launcher.py   부팅 시 인터넷 연결되면 GitHub에서 최신 코드로 업데이트 + 로그 업로드 후 main.py 실행
log_uploader.py  실행 로그 파일 저장 + 온라인일 때 GitHub 이슈로 업로드
```

데이터 흐름: **services → viewmodels → views** (단방향). views에는 `set_*` 류
메서드가 없고, viewmodels에도 View에 보내는 신호가 없습니다 — views가
`config.FRAME_RATE_HZ` 주기로 스스로 최신 상태를 읽어가는 폴링 방식이라,
센서가 아무리 빨리 갱신돼도 화면 갱신 속도는 이 값으로 제한됩니다 (200회
연속 데이터 갱신 → 실제 repaint 14회로 검증됨).

## 자세각(롤/피치/요)

EBIMU 가 출력하는 Roll/Pitch/Yaw 를 **필터·보정 없이 그대로** 표시합니다
(헤딩만 `HEADING_OFFSET_DEG` 적용). 예전 Mahony 필터 + OBD 속도 보정은 가감속 시
각도가 틀어지는 문제가 있어 삭제했고, 생데이터 확인 후 다시 설계할 예정입니다.

## 속도/RPM 테이프 (B737 NG 스타일)

- 회색 테이프, 속도는 10 km/h 눈금 / 20 km/h 숫자, RPM 은 100 / 500 rpm
  (오른쪽 테이프는 예전 고도 자리 — 고도 센서가 없어 엔진 RPM 으로 교체, OBD PID 010C)
- 현재값 박스: 검정 바탕 + 흰 테두리 + ADI 쪽 포인터. 속도는 일의 자리,
  RPM 은 끝 두 자리(50 단위)가 드럼처럼 굴러감. `RPM_REDLINE`(6500) 이상이면
  빨강/검정 줄무늬 + 박스 빨강, RPM 이 3초 이상 안 오면 `RPM` 고장 표시
- 속도 표시는 가속도 예측 보간: OBD 새 값이 오면 다음 값이 올 시간(수신 간격 평균) 동안
  "새 값 + 가속도 x 간격 x `SPEED_PREDICT_GAIN`(0.8)" 까지 일정한 속도로 이동 → 가속 중에도
  지연이 적고 부드러움. 강한 감속이 끝날 때 1~3 km/h 정도 지나쳤다 돌아올 수 있음
  (`SPEED_PREDICT_GAIN = 0` 이면 지연은 있지만 지나침 없는 단순 보간).
  처음/재연결 직후 첫 값은 예측 없이 그 값까지만 이동하고, 수신이 1초 넘게 끊기면
  마지막 OBD 값으로 천천히 복귀
- 초록 트렌드 화살표 = `SPEED_TREND_SEC`(3초) 뒤 예상 속도 (OBD 속도 변화율로 계산)
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
| IMU 헤딩 | PFD 헤딩 나침반 `HDG` | `IMU_STALE_SEC` |
| OBD 속도 | 속도 테이프 비우고 `SPD` | `SPEED_STALE_SEC = 3.0` |
| OBD RPM | RPM 테이프 비우고 `RPM` | `RPM_STALE_SEC = 3.0` |

부팅 직후 한 번도 못 받은 상태도 끊김으로 표시하고, 데이터가 다시 들어오면 바로 복귀합니다.

## 차량 시스템 화면 (오른쪽)

| 항목 | 요청 (헤더 / 명령) | 기본 주기 |
|---|---|---|
| 조향각 + 브레이크 압력 | 7D1 / 22 0104 | **매 바퀴** (`OBD_FAST_ITEM`, 속도+RPM 과 함께) |
| 앞문 잠금 + 후진(R) | 770 / 22 BC04 | 2초 |
| ~~현재 기어~~ | ~~7E1 / 22 01A4~~ | 국내형 CN7 은 변속기 ECU 가 거절(`7F 22 22`) → 끔. 화면 기어 = 후진 R / 주행 중 D / 정차 - |
| 엔진 부하 | 표준 0104 | 1초 |
| 냉각수 온도 / 배터리 전압 | 표준 0105 (없으면 0167) / ATRV (안 되면 0142) | 10초 |
| 엔진오일 온도 | 7E0 / 22 E001 | 10초 |
| 외기 온도 | 표준 0146 | 30초 |
| 연료량 | 7C6 / 22 B002 | 30초 |
| 타이어 공기압/온도 | 7A0 / 22 C00B | 30초 |

- 명령과 비트 위치는 [OBDb/Hyundai-Elantra](https://github.com/OBDb/Hyundai-Elantra) 기준.
  CN7 연식(2021~2026) 실차 응답 4,592개 값으로 해석 결과를 검증함 (미국형 기준, 국내형은 실차 확인 필요)
- 속도+RPM 한 바퀴마다 주기가 된 항목을 하나씩 끼워서 요청 → 속도 갱신은 조금만 느려짐.
  요구량이 처리량보다 많으면 모든 항목이 주기에 비례해서 같이 느려짐 (`OBD_SYS_INTERVALS`)
- 속도/RPM/표준 PID 는 엔진 ECU(7E0)로 직접 요청 (`OBD_BASE_HEADER`), 제조사 명령은 ATSH/ATCRA 로
  모듈별 전환. 어댑터가 ATSH 를 거부하면 7DF 로 표준 PID 만 요청하고 제조사 명령은 끔
- 한 번도 응답한 적 없는 항목이 3번 연속 실패하면 미지원으로 보고 60초마다만 재시도
  (로그 `[CAN] … 미지원`). 한 번이라도 응답한 항목은 실패해도 원래 주기로 계속 요청
- 응답이 제한시간을 넘기면 프롬프트('>')를 `OBD_LATE_PROMPT_WAIT_SEC` 만큼 더 기다림 —
  ELM327 은 응답 중 새 명령을 받으면 하던 일을 STOPPED 로 끊고 새 명령도 버려서 요청이 줄줄이 실패함
- 멀티프레임 일부가 빠진 응답은 버리고, 해석 못 한 값은 이전 값을 지우지 않음
- 값이 없거나 오래되면 회색 `--` (유효 시간 = 실제 수신 간격 x3, 최소 10초)
- ELM327 부가 기능 (연결 후 자동 확인, 안 되면 기존 방식):
  - 응답 개수 지정: 표준 PID 끝에 `1` 을 붙여(`010D1`) 응답 하나 받으면 바로 끝냄 → 다른 ECU 응답 대기 시간 절약
    (`OBD_USE_RESPONSE_COUNT`)
  - `ATRV`: 배터리 전압을 어댑터가 OBD 포트에서 직접 측정 → ECU 요청(0142)·헤더 전환 없음 (`OBD_USE_ATRV`)
  - 연결 시 `ATI`(어댑터 버전)와 각 기능 지원 여부를 로그로 남김
  - 긴 응답(멀티프레임) 끊김: 실차에서 응답이 길수록 중간이 빠지고 끝('>')만 옴 (문잠금/연료 100%,
    조향각 44바이트 ~50%, 오일 95바이트 0%) - 어댑터 블루투스 쪽이 긴 출력을 다 못 넘기는 것으로 보임.
    대응: `ATS0`(공백 제거로 출력 약 30% 감소, `OBD_COMPACT_OUTPUT`), 끊긴 응답은 미지원 판정에서 제외(조향각은
    끊겨도 매 바퀴 계속 요청), 끊기면 0.2초 기다렸다가 늦게 온 나머지를 버림(`OBD_SETTLE_AFTER_CUT_SEC`).
    시험 후 끈 것: 적응형 타이밍 끄기(`OBD_FIXED_TIMING`), 흐름 제어 프레임 간격(`OBD_FLOW_CONTROL`, ATFCSM2 는
    문잠금/연료까지 NO DATA), 즉시 재요청(`OBD_MODE22_RETRY`, 끝부분만 옴)
  - 연결 시 엔진 ECU 의 지원 PID 목록(0100~0160)을 로그로 남기고, 냉각수 0105 가 없고 0167 이 있으면 0167 사용
  - 항목별 첫 실패 응답은 전체(최대 300자)를 로그로 남김
- 30초마다 콘솔에 요약 로그: `[CAN] 30초 요약: 속도 N회(간격) | 늦은응답 | 무응답 | 항목 성공/요청 평균응답시간 실패응답='…'`
  (`OBD_DIAG_INTERVAL_SEC`, 0 이면 끔)
- 경고색 기준은 `config.py` 의 `SYS_*` (냉각수/오일 주황·빨강, 배터리 저·고전압, 타이어 저압, 연료 부족).
  타이어는 차의 TPMS 경고 비트가 켜져도 주황

## 설치

```bash
git clone https://github.com/TBlueT/CN7_Raspberrypi_PFD.git ~/cn7
cd ~/cn7
pip install pyserial bleak --break-system-packages   # PyQt6 는 기존 설치 방식 그대로
```

## 실행 전 `config.py`에서 확인할 것

- `IMU_SERIAL_PORT` — EBIMU가 잡히는 실제 포트 (`ls /dev/serial/by-id/`)
- `IMU_INIT_COMMANDS` — `<soa1>`(가속도), `<sog1>`(자이로) 등 부팅시 자동 전송 명령
- `IMU_EXTRA_FIELD_ORDER` — 실기 응답 필드 순서에 맞게 조정
- `OBD_BLE_ADDRESS` — 인포카 어댑터 MAC 주소 (`ble_scan.py`로 재확인 가능)
- `HEADING_OFFSET_DEG` — 헤딩 오프셋 (IMU 장착 방향/지자기 보정)
- `SYS_*` — 차량 시스템 화면 경고색 기준 / 계기 범위, `SYS_DOOR_LOCKED_BIT` (잠금 표시가 반대면 0)
- `SYS_STEER_SIGN` — 조향각 방향 (실차 기준 -1, 반대로 보이면 1)
- `OBD_GEAR_LAYOUT` — 2025년식 이후는 `"2025"`, `OBD_SYS_INTERVALS` — 항목별 요청 주기
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
런처는 부팅 화면(`CN7 PFD STARTUP`)에 NETWORK / BACKUP / UPDATE / LOGS / LAUNCH 단계와
업데이트된 커밋, 실패 원인을 표시하고 3초 뒤 main.py를 실행합니다. 화면을 띄우지
못하면 콘솔 출력 방식으로 자동 전환되어 부팅이 멈추지 않습니다.

## 실차 로그 자동 업로드 (GitHub 이슈)

- 실행할 때마다 콘솔 로그(`[CAN]`/`[IMU]`, 오류, 프로그램이 죽을 때의 Traceback)를 시각과 함께
  `~/cn7_logs/run_날짜_시각.log` 에 저장 (코드 폴더 밖이라 git pull 과 무관, 실행당 최대 5MB)
- 줄마다 `시각 +경과시간` (예: `17:38:11 +0:00:03`). 라즈베리파이 4 는 시계 배터리(RTC)가 없어서
  오프라인 부팅 시 시각이 지난번 종료 시각부터 시작함 → 인터넷 연결로 시계가 바뀌면
  `[LOG] 시계 변경 감지` 줄을 남기고, 이슈의 실행 길이는 경과 시간으로 계산
- 같은 줄 생략: 60초 안에 이미 기록한 줄이 또 나오면 파일엔 안 쓰고 횟수만 세었다가
  `... 반복 생략 29번 (10:00:02~10:00:58): 내용` 한 줄로 남김 (번갈아 반복되는 재연결 로그도 해당,
  Traceback 은 그대로, 콘솔 출력은 그대로). 재연결 루프 10분 기준 48KB → 6KB. `LOG_DEDUP_WINDOW_SEC`
- **인터넷이 될 때만** 아직 안 올린 로그를 모아 **새 이슈 하나**로 올림
  - 부팅 시: 런처 화면 `LOGS` 단계 (UPLOADED / NO NEW LOGS / NO TOKEN / FAIL)
  - 실행 중: 10분마다 확인 (부팅 때 오프라인이었다가 나중에 연결된 경우, 지금 실행 중인 로그는 제외)
- 이슈 내용: 실행별 요약 표 + 문제/상태 줄(같은 줄 반복은 접음) + 마지막 200줄(접힌 상세)
- 올린 로그는 `~/cn7_logs/sent/` 로 옮기고 30개까지만 보관. 업로드 실패 시 다음에 다시 시도
- 저장소가 공개면 이슈도 공개됨 (로그에 OBD 어댑터 MAC 주소 포함)

**토큰 설정 (한 번만)**
1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token
2. Repository access: *Only select repositories* → `CN7_Raspberrypi_PFD`
3. Permissions → Repository permissions → **Issues: Read and write** (다른 권한은 필요 없음)
4. 라즈베리파이에서:
   ```bash
   mkdir -p ~/.config/cn7
   nano ~/.config/cn7/github_token      # 토큰 붙여넣고 저장
   chmod 600 ~/.config/cn7/github_token
   ```
토큰 파일이 없으면 업로드만 건너뛰고 로그 저장은 계속됨. 설정은 `config.py` 의 `LOG_*`.

## 알려진 제약/TODO

- **RPM 갱신 주기**: 속도(010D)와 RPM(010C)을 번갈아 요청. 응답 끝 프롬프트('>')를
  받는 즉시 다음 요청을 보내므로 보통 한 바퀴 0.3~0.5초, 최악(프롬프트 미수신)엔 0.9초
- **SCC/LFA/HDA**: 인포카 어댑터가 raw CAN 모니터 모드(`ATMA`)를 지원하지 않아,
  표준 PID 요청-응답 범위를 벗어나는 제조사 고유 데이터는 현재 못 받아옴.
  대안으로 UDS Mode 22(진단 세션) 방식이나 별도 CAN 트랜시버(MCP2515 등) 하드웨어
  검토 중
- **헤딩**: 오프셋을 맞춰도 다시 틀어지는 현상 — EBIMU 지자기 사용 모드 / 차체 자기
  간섭(방향별 편차) 확인 필요
- **차량 시스템 화면**: 인포카가 제조사 명령(Mode 22)과 ATSH/ATCRA 를 받아주는지 실차 미확인.
  안 되는 항목은 `--` 로 남음. 기어는 무단변속기(IVT)라 의미 없는 숫자일 수 있음 (후진 R 은 별도 비트),
  브레이크 압력 %의 실제 의미, 문 잠금 비트 방향도 실차 확인 필요
- **IMU 내장 안정화 기능 확인**(`<savc1>` 등)은 정확한 명령어 미검증 상태

## 참고 도구 스크립트

- `ble_scan.py` — BLE 기기 서비스/특성 UUID 탐색
- `at_test.py`, `ble_pid_test.py` — OBD 어댑터 AT 명령/PID 응답 테스트
- `scc_lfa_hda_probe.py` — SCC/LFA/HDA 후보 PID 반복 조회 테스트
