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
ALT_TAPE_RECT = (324, 40, 66, 350)       # ADI 볼 오른쪽에 밀착
SPEED_PIXELS_PER_UNIT = 3
ALT_PIXELS_PER_UNIT = 0.15

HEADING_ARC_CENTER = (200, 550)
HEADING_ARC_RADIUS = 130
HEADING_ARC_SPAN_DEG = 60

# ---- ND(레이더) 패널 레이아웃 (전방 180도 반원) ----
ND_CENTER = (200, 460)        # 반원의 밑변 중심점 (패널 하단 쪽)
ND_RADIUS = 380

# ---- YDLIDAR G2 ----
# ---- 카메라 + YOLO26-Depth (라이다가 자외선차단필름을 못 뚫어서 교체) ----
# CAMERA_SOURCE: "usb"(일반 웹캠, CAMERA_INDEX 사용) 또는
# "sony_qx10"(Wi-Fi로 붙는 소니 렌즈카메라, 아래 SONY_QX10_* 사용)
CAMERA_SOURCE = "sony_qx10"
CAMERA_INDEX = 0

# ---- 소니 DSC-QX10 (Wi-Fi 라이브뷰) ----
# 지금은 SSDP 자동탐색을 우선 시도함. 같은 네트워크에서 탐색이 안 되면,
# 카메라 IP를 직접 알아내서(QX10 자체 설정 화면 등) 아래에 고정 지정하세요.
# 형태 예: "http://192.168.122.1:8080/services/camera"
SONY_QX10_DISCOVERY_TIMEOUT_SEC = 5.0
SONY_QX10_FIXED_ENDPOINT_URL = None
CAMERA_HORIZONTAL_FOV_DEG = 75.0     # 쓰시는 웹캠 스펙에 맞춰 조정
CAMERA_DEPTH_MODEL_PATH = "yolo26n-depth.pt"
CAMERA_DEPTH_MAX_RANGE_M = 8.0       # 화면 가장자리(ND_RADIUS)에 대응하는 거리
CAMERA_DEPTH_UPDATE_INTERVAL_SEC = 0.1   # RPi4 실측 후 조정 (추론이 느리면 늘리기)
CAMERA_ANGLE_OFFSET_DEG = 0.0            # 카메라 장착 방향 보정용

# 깊이맵에서 도로/장애물 영역만 보게, 세로 방향 관심영역(위:하늘/대시보드
# 제외, 아래:보닛 제외) 비율. 0.0=맨 위, 1.0=맨 아래
CAMERA_ROI_TOP_FRAC = 0.35
CAMERA_ROI_BOTTOM_FRAC = 0.75

# 물체 클러스터링 (TCAS 스타일 - 점 구름 대신 물체 단위로 묶어서 표시)
OBJECT_CLUSTER_DEPTH_TOLERANCE_M = 1.0   # 이 안이면 "같은 물체"로 묶음
OBJECT_MIN_CLUSTER_WIDTH_PX = 8           # 노이즈 제거용 최소 폭(픽셀 컬럼 수)


# ---- EBIMU-9DOFV5-R3 시리얼 설정 ----
# 실제 연결된 포트로 변경 (예: /dev/ttyUSB0, /dev/ttyAMA0, /dev/serial0 등)
IMU_SERIAL_PORT = "/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_76526e479582ea11b563d7d96a30b0f3-if00-port0"
IMU_BAUDRATE = 115200        # EBIMU 기본값. 장비에서 변경했다면 맞춰서 수정
IMU_TIMEOUT_SEC = 0.1
IMU_RECONNECT_INTERVAL_SEC = 1.0   # 부팅 직후 USB 인식 전일 수 있으므로 연결 실패 시 재시도 간격

# ---- 인포카 OBD 어댑터 (클래식 블루투스 SPP, /dev/rfcomm0) ----
# 사전에 bluetoothctl로 페어링 + `sudo rfcomm bind /dev/rfcomm0 <MAC>` 필요
OBD_SERIAL_PORT = "/dev/rfcomm0"
OBD_BAUDRATE = 38400          # RFCOMM 가상 포트라 실제 물리 baud와는 무관하지만 pyserial API상 필요한 값
OBD_TIMEOUT_SEC = 0.2
OBD_RECONNECT_INTERVAL_SEC = 0.1

# ---- 속도 표시 애니메이션 ----
# CAN에서 새 속도값이 뜨문뜨문 와도(수백ms 간격), 화면 표시값은 매 프레임
# 이 속도(초당 km/h)로만 목표치를 향해 부드럽게 움직임
SPEED_ANIMATION_MAX_RATE_KPH_PER_SEC = 40.0

# ---- 인포카 BLE 연결 정보 (ble_scan.py로 확인됨) ----
OBD_BLE_ADDRESS = "66:1E:11:14:04:63"     # Infocar-OH-03, 재스캔 시 바뀔 수 있으니 안 붙으면 ble_scan.py 재실행
OBD_BLE_TX_CHAR_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"   # AT 명령 보내는 채널
OBD_BLE_RX_CHAR_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"   # 응답/모니터 데이터 받는 채널

import os as _os
OBD_DBC_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "hyundai_can_clean.dbc")


# EBIMU ASCII 출력 포맷: "*Roll,Pitch,Yaw\r\n"
IMU_LINE_PREFIX = "*"

# EBIMU에 가속도 추가 출력 필드가 켜져 있는지 (Roll,Pitch,Yaw,ax,ay,az 형태).
# EBTerminal 등으로 해당 출력 모드를 켜고 필드 순서를 확인한 뒤 True로 바꾸세요.
# False인 동안은 롤 스무딩이 요값 변화율 기반(폴백)으로만 동작합니다.
IMU_ACCEL_FIELDS_ENABLED = True

# ---- EBIMU 부팅/재연결 시 자동으로 보낼 초기화 명령 ----
# "<soa1>"은 이투박스 EBIMU 계열에서 확인된, Roll/Pitch/Yaw 뒤에 가속도(ax,ay,az)를
# 추가로 붙여 출력하게 하는 명령입니다(구형 V3 문서 기준으로 확인됨 - V5-R3가
# 하위호환된다는 제조사 설명을 근거로 우선 적용). 100% 검증된 값은 아니니,
# 실기에서 [IMU] 로그로 실제 파싱되는 값 개수를 꼭 확인하세요 (아래 "확인 방법" 참고).
# 만약 이 명령이 안 먹히거나 필드 순서가 다르면, imu_reader.py는 그냥 필드가
# 3개인 걸로 보고 가속도 신호를 안 보내기만 할 뿐 기존 자세각 기능은 안전하게
# 그대로 동작합니다.
#   "<sor10>"   출력 주기를 10ms(100Hz)로 설정 (아직 미검증, 필요시 주석 해제)
# "<soa2>"는 이투박스 EBIMU 계열에서 확인된, Roll/Pitch/Yaw 뒤에 "중력성분이
# 제거된" 가속도(ax,ay,az, Local 기준)를 추가로 붙여 출력하게 하는 명령입니다.
# (공식 명령어 표로 확인됨: soa0=미출력, soa1=중력포함 원본, soa2=중력제거
# Local, soa3=중력제거 Global). 롤 보정 계산에는 중력이 빠진 순수 가속도가
# 필요해서 soa2를 씀.
IMU_INIT_COMMANDS = [
    "<soa1>",
    "<ltf3>",
    "<ssa2>",
]
IMU_INIT_COMMAND_DELAY_SEC = 0.2   # 각 명령 사이 대기 시간

# 내장 안정화 기능(AVC 등) 확인용 명령 - 정확한 명령어는 미검증 상태.
# EBTerminal로 확인되면 이 값을 실제 명령으로 바꾸세요.
IMU_STABILIZATION_CHECK_COMMAND = "<savc1>"

# ---- 선회 시 원심력으로 인한 롤(roll) 노이즈 보정 ----
# 물리 공식(원심가속도=속도*요레이트)으로 가짜 롤을 직접 계산해서 빼는 방식.
# 아래 값은 그 보정 후 남는 잔여 센서 노이즈를 살짝 다듬는 EMA 스무딩 강도.
# 1.0이면 스무딩 없음(원값 그대로), 작을수록 더 부드럽지만 반응은 느려짐.
ROLL_SMOOTHING_ALPHA = 0.5
PITCH_SMOOTHING_ALPHA = 0.5

# soa2로 받는 가로가속도(ay)/전후가속도(ax) 축이 실제로 어느 방향을 향하는지는
# IMU 장착 방향에 따라 달라짐. 실기에서 방향이 반대로 나오면 -1.0으로 바꾸세요.
LATERAL_ACCEL_SIGN = 1.0        # 롤 보정용 (좌우 원심력)
LONGITUDINAL_ACCEL_SIGN = 1.0   # 피치 보정용 (가감속)

# ---- 색상 (B737 스타일) ----
COLOR_SKY = "#1f5fa8"
COLOR_GROUND = "#6b4423"
COLOR_HORIZON_LINE = "#ffffff"
COLOR_WHITE = "#e8e8e8"
COLOR_BLACK = "#000000"
COLOR_TAPE_BORDER = "#4a4a4a"
COLOR_TAPE_TICK = "#666666"
COLOR_CURRENT_VALUE_BG = "#00c8c8"   # 사이언
COLOR_MODE_TEXT = "#2fd646"          # 초록 (활성 상태)
COLOR_MODE_TEXT_OFF = "#555555"      # 회색 (비활성 상태)
COLOR_WARN_TEXT = "#f0997b"
COLOR_HEADING_BG = "#333333"         # 헤딩 아크 배경 (시인성 확보용 회색)
COLOR_TREND = "#f0a020"              # 속도 트렌드 화살표
COLOR_ND_BG = "#0a0a0a"
COLOR_ND_RING = "#2a2a2a"

# IMU 내장 안정화 기능 확인 여부 표시 (ALT 글자색)
COLOR_FEATURE_OK = "#4aa8ff"        # 파랑 - 기능 확인됨
COLOR_FEATURE_MISSING = "#e05252"   # 빨강 - 기능 없음/미확인
