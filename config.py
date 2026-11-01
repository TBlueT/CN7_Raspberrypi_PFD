"""
PFD 프로젝트 전역 설정
디스플레이/시리얼 환경이 바뀌면 이 파일만 수정하면 됩니다.
"""

# ---- 디스플레이 설정 (라즈베리파이 정품 7인치 터치스크린) ----
PANEL_WIDTH = 400            # PFD/ND 각 패널 너비 (좌우 반반)
PANEL_HEIGHT = 480
SCREEN_WIDTH = PANEL_WIDTH * 2   # 800
SCREEN_HEIGHT = PANEL_HEIGHT     # 480
FRAME_RATE_HZ = 30          # 렌더링 갱신 주기. RPi3 성능 보고 20~30 사이로 조정 권장

# ---- PFD 레이아웃 (실기 참고: 둥근 ADI 볼 + 양옆 밀착 테이프) ----
# 아래 좌표는 모두 PFDWidget 자신의 로컬 좌표계(0~400, 0~480) 기준
FMA_BAR_RECT = (8, 8, 384, 22)          # x, y, w, h
FMA_DIVIDER_X = (104, 296)

ADI_BALL_RECT = (90, 80, 222, 270)      # x, y, w, h, rx=10
ADI_CENTER = (201, 230)
BANK_ARC_RADIUS = 140
PIXELS_PER_DEGREE_PITCH = 8

SPEED_TAPE_RECT = (8, 40, 66, 350)      # ADI 볼 왼쪽에 밀착
RPM_TAPE_RECT = (324, 40, 66, 350)       # ADI 볼 오른쪽에 밀착 (예전 고도 테이프 자리)
SPEED_PIXELS_PER_UNIT = 3
RPM_PIXELS_PER_UNIT = 0.1                 # 100 rpm = 10px (화면에 약 ±1500 rpm 보임)

# ---- 속도/고도 테이프 (B737 NG 스타일) ----
# 테이프 영역 = 위 띠(SCC 설정속도) + 본체(눈금) + 아래 띠(단위)
TAPE_TOP_STRIP_PX = 22
TAPE_BOTTOM_STRIP_PX = 18
SPEED_TICK_STEP = 10           # 눈금 간격 (km/h)
SPEED_LABEL_STEP = 20          # 숫자 간격 (km/h)
SPEED_TREND_SEC = 3.0          # 트렌드 화살표 = 이 시간 뒤 예상 속도 (실기 10초, 차는 가속이 빨라 3초)
SPEED_ACCEL_SMOOTHING_SEC = 0.4   # OBD 속도 변화율(가속도) 평활 시간 - 예측 보간 + 트렌드 화살표
SPEED_BARBER_POLE_KPH = 110    # 이 속도 이상 빨강/검정 줄무늬 (None 이면 표시 안 함)
RPM_TICK_STEP = 100            # 눈금 간격 (rpm)
RPM_LABEL_STEP = 500           # 숫자 간격 (rpm)
RPM_DRUM_STEP = 50             # 현재값 박스 끝 두 자리 드럼 단위 (00/50)
RPM_REDLINE = 6500             # 레드라인: 이상이면 빨강/검정 줄무늬 + 박스 빨강 (None 이면 끔)
RPM_SMOOTHING_ALPHA = 0.3      # 표시 RPM 평활 (1.0 = 평활 없음)
RPM_STALE_SEC = 3.0            # 이 시간 동안 RPM 이 안 오면 RPM 고장 표시
COLOR_TAPE_BG = "#4d5257"      # 테이프 회색 배경
COLOR_SPEED_TREND = "#2fd646"  # 트렌드 화살표 (초록)
COLOR_SELECTED = "#e05ae0"     # SCC 설정속도 숫자/버그 (마젠타)
COLOR_BARBER_POLE = "#e02020"
# 과속 경고: SPEED_BARBER_POLE_KPH 이상이면 현재 속도 박스가 빨강 (보잉 경고 관례대로
# 처음 SPEED_OVERSPEED_FLASH_SEC 동안 깜빡인 뒤 고정). 한계 - HYST 아래로 내려가야 해제
SPEED_OVERSPEED_HYST_KPH = 2.0
SPEED_OVERSPEED_FLASH_SEC = 10.0
SPEED_OVERSPEED_FLASH_HZ = 2.0
SPEED_OVERSPEED_TEXT = False     # True 면 테이프 아래 KM/H 자리에 빨간 OVERSPEED 표시 (실기엔 없음)
COLOR_OVERSPEED = "#ff2a2a"

# 헤딩 나침반 (B737 NG 스타일, 1:1 회전). 반지름을 크게 하고 중심을 화면 아래로
# 내려서 윗변(y = 중심 - 반지름 = 420)은 그대로 두고 10° 숫자가 겹치지 않게 함
HEADING_ARC_CENTER = (200, 640)
HEADING_ARC_RADIUS = 220
HEADING_VISIBLE_HALF_DEG = 55    # 그릴 범위 (화면에는 약 ±45° 보임)

# ---- ND(오른쪽 화면) 레이아웃 (전방 180도 반원) ----
ND_CENTER = (200, 460)        # 반원의 밑변 중심점 (패널 하단 쪽)
ND_RADIUS = 380
ND_MAX_RANGE = 50.0           # 반원 가장자리(ND_RADIUS)에 대응하는 거리
ND_RANGE_RING_STEP = 10.0     # 거리 링 간격
ND_RANGE_UNIT = "m"           # 링 라벨 단위 (ADS-B 등 추가 시 NM 등으로 변경)


# ---- EBIMU-9DOFV5-R3 시리얼 설정 ----
# 실제 연결된 포트로 변경 (예: /dev/ttyUSB0, /dev/ttyAMA0, /dev/serial0 등)
IMU_SERIAL_PORT = "/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_76526e479582ea11b563d7d96a30b0f3-if00-port0"
IMU_BAUDRATE = 115200        # EBIMU 기본값. 장비에서 변경했다면 맞춰서 수정
IMU_TIMEOUT_SEC = 0.1
IMU_RECONNECT_INTERVAL_SEC = 1.0   # 부팅 직후 USB 인식 전일 수 있으므로 연결 실패 시 재시도 간격

# ---- 인포카 OBD 어댑터 (BLE) 폴링 ----
OBD_RECONNECT_INTERVAL_SEC = 0.1
OBD_POLL_RPM = True               # 속도(010D) 다음에 엔진 RPM(010C)도 요청
OBD_RESPONSE_TIMEOUT_SEC = 0.4    # 응답 끝 프롬프트('>')를 기다리는 최대 시간 (이전 고정 대기와 같음)
OBD_POLL_INTERVAL_SEC = 0.1       # 한 바퀴(속도+RPM) 요청 후 쉬는 시간

# ---- 속도 표시 애니메이션 (가속도 예측 보간) ----
# OBD 새 값이 오면, 다음 값이 올 것으로 예상되는 시간(최근 수신 간격 평균) 동안
# "새 값 + 가속도 x 간격 x 게인" 을 향해 일정한 속도로 이동 (가속 중 지연 보상).
# 가속이 줄어드는 쪽은 가속도를 즉시 반영, 방향이 바뀌면 0 으로 (감속 끝 지나침/정속 흔들림 억제).
# 다음 값이 HOLD_FACTOR x 간격 안에 안 오면 바늘을 멈춤.
SPEED_PREDICT_GAIN = 0.8          # 0 = 보간만(지연 있음), 1.0 = 가속도 전부 반영 (지나침 커짐)
SPEED_INTERP_HOLD_FACTOR = 1.5
SPEED_SETTLE_AFTER_SEC = 1.0      # 새 값이 이 시간 넘게 안 오면 마지막 OBD 값으로 천천히 복귀
SPEED_INTERP_MIN_SEC = 0.3        # 한 구간 최소 이동 시간 (수신 간격이 이보다 짧아도 이 시간 사용)
SPEED_INTERP_INIT_SEC = 0.4       # 수신 간격 평균 초기값

# ---- 인포카 BLE 연결 정보 (ble_scan.py로 확인됨) ----
OBD_BLE_ADDRESS = "66:1E:11:14:04:63"     # Infocar-OH-03, 재스캔 시 바뀔 수 있으니 안 붙으면 ble_scan.py 재실행
OBD_BLE_TX_CHAR_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"   # AT 명령 보내는 채널
OBD_BLE_RX_CHAR_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"   # 응답/모니터 데이터 받는 채널



# EBIMU ASCII 출력 포맷: "*Roll,Pitch,Yaw\r\n"
IMU_LINE_PREFIX = "*"

# EBIMU에 추가 출력 필드(자이로/가속도)가 켜져 있으면 Roll,Pitch,Yaw 뒤에
# 순서대로 3개씩 더 붙어 옴. 이 리스트가 그 순서를 나타냄
# (예: ["gyro","accel"] -> *Roll,Pitch,Yaw,gx,gy,gz,ax,ay,az).
# 자이로/가속도 필드가 전혀 없으면 빈 리스트로 두면 됨(자세각만 파싱).
IMU_EXTRA_FIELD_ORDER = ["gyro", "accel"]

# ---- EBIMU 부팅/재연결 시 자동으로 보낼 초기화 명령 ----
# "<sog1>": 자이로(각속도) 원시값 추가 출력. "<soa1>": 가속도 원시값(중력 포함).
# 현재 자세 표시에는 쓰지 않음 (파싱/로그만). 실기에서 [IMU] 로그로 확인.
IMU_INIT_COMMANDS = [
    "<soa1>",
    "<ltf3>",
    "<ssa2>",
]
IMU_INIT_COMMAND_DELAY_SEC = 0.2   # 각 명령 사이 대기 시간

# 내장 안정화 기능(AVC 등) 확인용 명령 - 정확한 명령어는 미검증 상태.
# EBTerminal로 확인되면 이 값을 실제 명령으로 바꾸세요.
IMU_STABILIZATION_CHECK_COMMAND = "<savc1>"

# 롤/피치/요는 EBIMU 출력을 필터·보정 없이 그대로 표시 (헤딩만 아래 오프셋 적용)

# 헤딩 오프셋 (도): IMU 요값에 더해서 표시 (IMU 장착 방향/지자기 보정용).
# PFD 나침반과 ND 헤딩 박스 모두에 적용됨. 0 이면 보정 없음
HEADING_OFFSET_DEG = 101.0

# ---- 색상 (B737 스타일) ----
COLOR_SKY = "#1f5fa8"
COLOR_GROUND = "#6b4423"
COLOR_HORIZON_LINE = "#ffffff"
COLOR_WHITE = "#e8e8e8"
COLOR_BLACK = "#000000"
COLOR_TAPE_BORDER = "#4a4a4a"
COLOR_TAPE_TICK = "#666666"
COLOR_MODE_TEXT = "#2fd646"          # 초록 (활성 상태)
COLOR_MODE_TEXT_OFF = "#555555"      # 회색 (비활성 상태)
COLOR_FAIL_FLAG = "#f0a020"         # 데이터 끊김 고장 표시 (ATT/HDG/SPD, 보잉 계기 관례 주황)

# ---- 데이터 유효성 (이 시간 동안 새 값이 없으면 고장 표시) ----
IMU_STALE_SEC = 0.5      # 자세(ATT)/헤딩(HDG)
SPEED_STALE_SEC = 3.0    # 속도(SPD) - OBD 폴링 약 0.7초 간격
COLOR_HEADING_BG = "#3a3d40"         # 헤딩 나침반 원판 (진회색)
COLOR_ND_BG = "#0a0a0a"
COLOR_ND_RING = "#2a2a2a"

# IMU 내장 안정화 기능 확인 여부 표시 (ALT 글자색)
COLOR_FEATURE_OK = "#4aa8ff"        # 파랑 - 기능 확인됨
COLOR_FEATURE_MISSING = "#e05252"   # 빨강 - 기능 없음/미확인
