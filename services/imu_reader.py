"""
EBIMU-9DOFV5-R3 시리얼 수신 스레드

ASCII 출력 포맷: "*Roll,Pitch,Yaw[,추가 필드...]\r\n"
- 시작: '*' (IMU_LINE_PREFIX), 구분자 ',', 종료 CR LF
- 추가 필드(자이로/가속도)는 IMU_EXTRA_FIELD_ORDER 순서로 3개씩 (실기: 가속도 + 카운터)

시리얼 읽기는 블로킹이라 QThread 에서 돌리고, 자세각은 시그널로 GUI 스레드에 전달.
연결될 때마다 IMU_INIT_COMMANDS 를 보내고 응답을 로그로 남김.
"""

import serial
from PyQt6.QtCore import QThread, pyqtSignal

import config


class ImuReaderThread(QThread):
    # roll_deg, pitch_deg, yaw_deg
    attitude_updated = pyqtSignal(float, float, float)
    # 추가 출력 필드 (IMU_EXTRA_FIELD_ORDER 에 있을 때만 발행. 지금 화면에는 안 씀)
    gyro_updated = pyqtSignal(float, float, float)          # gx, gy, gz (EBIMU 출력 단위 그대로)
    linear_accel_updated = pyqtSignal(float, float, float)  # ax, ay, az (g)
    log_message = pyqtSignal(str)                           # 연결 상태/초기화 응답 로그 (main 이 [IMU] 로 출력)

    def __init__(self, port=None, baudrate=None, parent=None):
        super().__init__(parent)
        self._port = port or config.IMU_SERIAL_PORT
        self._baudrate = baudrate or config.IMU_BAUDRATE
        self._running = False
        self._extra_debug_count = 0     # 자이로/가속도 파싱 성공 로그, 처음 5회만
        self._extra_warned_short = False  # 필드 부족 경고, 1회만

    def run(self):
        self._running = True

        while self._running:
            ser = self._try_open_serial()
            if ser is None:
                # 부팅 직후 USB가 아직 열거되지 않은 상태일 수 있으므로
                # 에러로 죽지 않고 일정 간격으로 재시도한다.
                self.msleep(int(config.IMU_RECONNECT_INTERVAL_SEC * 1000))
                continue

            self._send_init_commands(ser)
            self._read_loop(ser)  # 이 함수는 읽기 오류/연결 끊김 시 리턴됨

            if ser.is_open:
                ser.close()

            if self._running:
                # 연결이 끊겼다면(케이블 문제 등) 잠시 대기 후 재연결 시도
                self.msleep(int(config.IMU_RECONNECT_INTERVAL_SEC * 1000))

    def _send_init_commands(self, ser: serial.Serial):
        """부팅/재연결 시마다 config.IMU_INIT_COMMANDS 를 보내고 응답을 로그로 남긴다.

        주의: EBIMU는 명령을 보내는 동안에도 자세각 데이터 스트림(*1.2,3.4,5.6\r\n
        형태)을 계속 쏟아낸다. 그래서 명령 보낸 직후 버퍼를 그냥 읽으면 진짜
        응답이 아니라 그 사이에 낀 평범한 데이터 줄을 응답으로 착각할 수 있다.
        여기서는 짧은 시간 동안 들어오는 걸 전부 줄 단위로 나눈 뒤,
        IMU_LINE_PREFIX(데이터 줄)로 시작하지 않는 줄만 "진짜 응답 후보"로
        골라서 보여준다."""
        commands = getattr(config, "IMU_INIT_COMMANDS", [])
        if not commands:
            return

        for command in commands:
            try:
                ser.reset_input_buffer()
                ser.write(command.encode("ascii"))
                self.log_message.emit(f"IMU 초기화 명령 전송: {command!r}")

                response_lines = self._collect_non_data_lines(
                    ser, window_sec=config.IMU_INIT_COMMAND_DELAY_SEC)
                if response_lines:
                    for line in response_lines:
                        self.log_message.emit(f"IMU 응답: {line!r}")
                else:
                    self.log_message.emit(
                        "IMU 응답 없음 (데이터 스트림에 섞여 못 찾았거나, "
                        "명령이 무시됐을 수 있음)")
            except serial.SerialException as exc:
                self.log_message.emit(f"IMU 초기화 명령 전송 실패: {exc}")
                return

    def _collect_non_data_lines(self, ser: serial.Serial, window_sec: float) -> list:
        """window_sec 동안 들어오는 바이트를 줄 단위로 모아서, 평범한 자세각
        데이터 줄(IMU_LINE_PREFIX로 시작)은 제외하고 나머지만 반환한다."""
        buffer = b""
        deadline_ms = int(window_sec * 1000)
        elapsed_ms = 0
        step_ms = 20
        non_data_lines = []

        while elapsed_ms < deadline_ms:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buffer += chunk
            self.msleep(step_ms)
            elapsed_ms += step_ms

        for raw_line in buffer.replace(b"\r", b"\n").split(b"\n"):
            if not raw_line:
                continue
            text = raw_line.decode("ascii", errors="ignore").strip()
            if not text:
                continue
            if text.startswith(config.IMU_LINE_PREFIX):
                continue  # 평범한 자세각 데이터 줄 - 응답 아님, 무시
            non_data_lines.append(text)

        return non_data_lines

    def _try_open_serial(self):
        try:
            return serial.Serial(
                self._port,
                self._baudrate,
                timeout=config.IMU_TIMEOUT_SEC,
            )
        except serial.SerialException as exc:
            self.log_message.emit(f"시리얼 포트 열기 대기 중: {exc}")
            return None

    def _read_loop(self, ser: serial.Serial):
        buffer = b""
        while self._running:
            try:
                chunk = ser.read(ser.in_waiting or 1)
                if not chunk:
                    continue
                buffer += chunk

                # CR LF 기준으로 라인 분리, 마지막 미완성 조각은 버퍼에 유지
                while b"\r\n" in buffer:
                    line, buffer = buffer.split(b"\r\n", 1)
                    self._parse_line(line)

                # 버퍼가 비정상적으로 커지면(파싱 실패 누적) 방어적으로 비움
                if len(buffer) > 256:
                    buffer = b""

            except serial.SerialException as exc:
                self.log_message.emit(f"시리얼 연결 끊김, 재연결 시도: {exc}")
                return

    def _parse_line(self, raw_line: bytes):
        try:
            text = raw_line.decode("ascii", errors="ignore").strip()
        except UnicodeDecodeError:
            return

        if not text.startswith(config.IMU_LINE_PREFIX):
            return

        payload = text[len(config.IMU_LINE_PREFIX):]
        parts = payload.split(",")
        if len(parts) < 3:
            return

        try:
            roll = float(parts[0])
            pitch = -float(parts[1])
            yaw = float(parts[2])
        except ValueError:
            return

        # EBIMU에 추가 출력 필드(자이로/가속도)가 켜져 있으면 Roll,Pitch,Yaw
        # 뒤에 3개씩 이어서 옴. 순서는 config.IMU_EXTRA_FIELD_ORDER로 결정
        # (예: ["gyro","accel"] -> Roll,Pitch,Yaw,gx,gy,gz,ax,ay,az).
        # (같은 샘플의 자이로/가속도를 먼저 발행한 뒤 자세각을 발행)
        extra_order = getattr(config, "IMU_EXTRA_FIELD_ORDER", [])
        expected_len = 3 + 3 * len(extra_order)

        if extra_order:
            if len(parts) >= expected_len:
                try:
                    values = [float(p) for p in parts[3:expected_len]]
                except ValueError:
                    values = None

                if values is not None:
                    for i, kind in enumerate(extra_order):
                        x, y, z = values[i * 3:i * 3 + 3]
                        if kind == "gyro":
                            self.gyro_updated.emit(x, y, z)
                        elif kind == "accel":
                            self.linear_accel_updated.emit(x, y, z)

                    if self._extra_debug_count < 5:
                        self._extra_debug_count += 1
                        self.log_message.emit(
                            f"IMU 추가 필드({'+'.join(extra_order)}) 파싱됨 "
                            f"[{self._extra_debug_count}/5]: {values}")
            elif not self._extra_warned_short:
                self._extra_warned_short = True
                self.log_message.emit(
                    f"IMU_EXTRA_FIELD_ORDER={extra_order}인데 필드가 {len(parts)}개뿐입니다 "
                    f"({expected_len}개 필요) - 초기화 명령이 안 먹혔거나 필드 순서가 다를 "
                    f"수 있습니다. raw payload 예시: {payload!r}")

        self.attitude_updated.emit(roll, pitch, yaw)

    def stop(self):
        self._running = False
        self.wait(1000)
