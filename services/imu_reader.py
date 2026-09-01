"""
EBIMU-9DOFV5-R3 시리얼 수신 스레드

ASCII 출력 포맷: "*Roll,Pitch,Yaw\r\n"
- 시작: '*' (0x2A)
- 구분자: ','
- 종료: CR LF (0x0D 0x0A)

시리얼 읽기는 블로킹 특성이 있으므로 반드시 QThread에서 돌리고,
파싱된 최신 자세각만 시그널로 GUI 스레드에 전달합니다.
GUI 쪽 paintEvent가 시리얼 타이밍에 발목 잡히지 않도록 하기 위함입니다.
"""

import serial
from PyQt6.QtCore import QThread, pyqtSignal

import config


class ImuReaderThread(QThread):
    # roll_deg, pitch_deg, yaw_deg
    attitude_updated = pyqtSignal(float, float, float)
    # ax, ay, az (중력성분 제거된 가속도, m/s^2) - EBIMU에서 추가 출력 필드가
    # 켜져 있고 한 줄에 6개 이상 값이 올 때만 발행됨. 켜져 있지 않으면 이
    # 신호는 아예 안 옴 (연결한 쪽에서 굳이 처리 안 해도 무방).
    linear_accel_updated = pyqtSignal(float, float, float)
    # True: 내장 안정화 기능(AVC 등) 명령에 응답 있음 / False: 응답 없음(기능
    # 없거나 명령이 안 맞음). 연결될 때마다 한 번씩 발행됨.
    feature_check_updated = pyqtSignal(bool)
    connection_error = pyqtSignal(str)

    def __init__(self, port=None, baudrate=None, parent=None):
        super().__init__(parent)
        self._port = port or config.IMU_SERIAL_PORT
        self._baudrate = baudrate or config.IMU_BAUDRATE
        self._running = False
        self._accel_debug_count = 0     # 가속도 파싱 성공 로그, 처음 5회만
        self._accel_warned_short = False  # 필드 부족 경고, 1회만

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
            self._check_stabilization_feature(ser)
            self._read_loop(ser)  # 이 함수는 읽기 오류/연결 끊김 시 리턴됨

            if ser.is_open:
                ser.close()

            if self._running:
                # 연결이 끊겼다면(케이블 문제 등) 잠시 대기 후 재연결 시도
                self.msleep(int(config.IMU_RECONNECT_INTERVAL_SEC * 1000))

    def _check_stabilization_feature(self, ser: serial.Serial):
        """내장 안정화 기능(AVC 등) 명령을 보내보고, 응답이 있는지로 그 기능이
        이 하드웨어/펌웨어에 실제로 있는지 확인한다.

        주의: "응답이 온다"만 확인하는 방식이라, 그 응답이 진짜 "성공적으로
        켜졌다"는 뜻인지 "그런 명령 모른다"는 에러 응답인지까지는 구분 못한다.
        EBIMU 에러 응답 형식이 확인되면 그때 더 정확하게 판별하도록 개선 가능."""
        command = getattr(config, "IMU_STABILIZATION_CHECK_COMMAND", None)
        if not command:
            self.feature_check_updated.emit(False)
            return

        try:
            ser.reset_input_buffer()
            ser.write(command.encode("ascii"))
            self.connection_error.emit(f"IMU 안정화 기능 확인 명령 전송: {command!r}")

            response_lines = self._collect_non_data_lines(
                ser, window_sec=config.IMU_INIT_COMMAND_DELAY_SEC)

            if response_lines:
                for line in response_lines:
                    self.connection_error.emit(f"IMU 안정화 기능 응답: {line!r}")
                self.connection_error.emit("IMU 안정화 기능 확인됨 (응답 있음)")
                self.feature_check_updated.emit(True)
            else:
                self.connection_error.emit("IMU 안정화 기능 응답 없음 (기능 없거나 명령 불일치)")
                self.feature_check_updated.emit(False)
        except serial.SerialException as exc:
            self.connection_error.emit(f"IMU 안정화 기능 확인 실패: {exc}")
            self.feature_check_updated.emit(False)

    def _send_init_commands(self, ser: serial.Serial):
        """부팅/재연결 시마다 EBIMU에 필요한 설정 명령을 자동으로 보낸다.
        config.IMU_INIT_COMMANDS에 정확한 명령 문자열을 채워 넣으면 됨.

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
                self.connection_error.emit(f"IMU 초기화 명령 전송: {command!r}")

                response_lines = self._collect_non_data_lines(
                    ser, window_sec=config.IMU_INIT_COMMAND_DELAY_SEC)
                if response_lines:
                    for line in response_lines:
                        self.connection_error.emit(f"IMU 응답: {line!r}")
                else:
                    self.connection_error.emit(
                        "IMU 응답 없음 (데이터 스트림에 섞여 못 찾았거나, "
                        "명령이 무시됐을 수 있음)")
            except serial.SerialException as exc:
                self.connection_error.emit(f"IMU 초기화 명령 전송 실패: {exc}")
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
            self.connection_error.emit(f"시리얼 포트 열기 대기 중: {exc}")
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
                self.connection_error.emit(f"시리얼 연결 끊김, 재연결 시도: {exc}")
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

        self.attitude_updated.emit(roll, pitch, yaw)

        # EBIMU에 추가 출력 필드(가속도)가 켜져 있으면 Roll,Pitch,Yaw 뒤에
        # ax,ay,az가 이어서 옴. config.IMU_ACCEL_FIELDS_ENABLED로 이 프로젝트가
        # 그 필드를 기대하는지 표시하고, 실제로 필드 개수가 충분할 때만 파싱.
        if getattr(config, "IMU_ACCEL_FIELDS_ENABLED", False):
            if len(parts) >= 6:
                try:
                    ax = float(parts[3])
                    ay = float(parts[4])
                    az = float(parts[5])
                except ValueError:
                    return
                self.linear_accel_updated.emit(ax, ay, az)

                if self._accel_debug_count < 5:
                    self._accel_debug_count += 1
                    self.connection_error.emit(
                        f"IMU 가속도 필드 파싱됨 [{self._accel_debug_count}/5]: "
                        f"ax={ax:.3f} ay={ay:.3f} az={az:.3f}")
            elif not self._accel_warned_short:
                self._accel_warned_short = True
                self.connection_error.emit(
                    f"IMU_ACCEL_FIELDS_ENABLED=True인데 필드가 {len(parts)}개뿐입니다 "
                    f"(6개 이상 필요) - <soa1> 명령이 안 먹혔거나 필드 순서가 다를 수 "
                    f"있습니다. raw payload 예시: {payload!r}")

    def stop(self):
        self._running = False
        self.wait(1000)
