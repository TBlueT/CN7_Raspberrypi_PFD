"""
인포카 BLE OBD 어댑터(FFF0 서비스, FFF1=notify RX, FFF2=write TX)를 통해
표준 OBD PID 요청-응답으로 차량 속도(010D)와 엔진 RPM(010C)을 가져옵니다.

차량 시스템 화면 데이터(조향각, 기어, 타이어, 온도 등)는 속도+RPM 한 바퀴마다
주기가 된 항목을 OBD_SYS_ITEMS_PER_CYCLE 개씩 끼워서 요청합니다 (항목 정의와
해석은 services/obd_decode.py). 제조사 명령(Mode 22)은 ATSH(요청 헤더) +
ATCRA(응답 헤더 필터)로 해당 모듈에 보내고, 속도/RPM/표준 PID 는 기본 헤더
OBD_BASE_HEADER(엔진 ECU 7E0)로 돌아와서 보냄. 엔진 ECU 로 직접 물으면 엔진오일
온도(7E0 22 E001)도 헤더 전환 없이 받을 수 있어 전환 횟수가 줄어듦.
어댑터가 ATSH 를 거부하면 예전처럼 7DF 로만 묻고 제조사 명령은 끔.
연속 OBD_SYS_FAIL_LIMIT 번 응답이 없으면 미지원으로 보고 OBD_SYS_UNSUPPORTED_RETRY_SEC
주기로만 다시 시도합니다 (속도 갱신이 느려지지 않도록).

요청을 보낸 뒤 고정 시간을 기다리지 않고, ELM327 이 응답 끝에 보내는 프롬프트
문자('>')가 오면 바로 다음 요청을 보냅니다 (최대 OBD_RESPONSE_TIMEOUT_SEC 대기).

배경: 이 어댑터는 raw CAN 버스 모니터 모드(ATMA)를 지원하지 않는 저가 클론으로
확인되어(표준 PID 요청-응답만 동작), SCC/LFA/HDA 같은 제조사 고유 CAN 메시지는
지금은 가져올 수 없습니다. 나중에 방법을 찾으면(UDS Mode22 리버스엔지니어링,
또는 MCP2515 등 별도 CAN 트랜시버 하드웨어 추가) scc_status_updated /
lfa_status_updated / hda_curve_updated 시그널에 실제 값을 emit하도록
채워넣으면 되고, main.py/pfd_widget.py는 지금 그대로 두면 됩니다
(그 세 시그널을 그냥 아무 것도 안 보내고 있을 뿐, 연결 자체는 이미 되어 있음).
"""

import asyncio
import re
import time

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from PyQt6.QtCore import QThread, pyqtSignal

import config
from services.obd_decode import POLL_ITEMS, decode_item

# "41 0D XX" (표준 PID 0D 응답: 속도, km/h) 패턴. 사이사이 공백/개행/프롬프트(>) 섞여도 매칭되게 함.
_SPEED_RESPONSE_PATTERN = re.compile(r"41\s*0D\s*([0-9A-Fa-f]{2})")
# "41 0C AA BB" (표준 PID 0C 응답: 엔진 RPM = (AA*256 + BB) / 4)
_RPM_RESPONSE_PATTERN = re.compile(r"41\s*0C\s*([0-9A-Fa-f]{2})\s*([0-9A-Fa-f]{2})")

_DEFAULT_HEADER = "7DF"   # 표준 OBD 기능 주소 (모든 ECU 에 묻기, ATZ 직후 기본값)


def _response_header(header: str):
    """요청 헤더 -> 응답 헤더 (11bit 진단: +8). 7DF(기능 주소)는 응답이 여러 곳이라 None(필터 해제)"""
    if header is None or header == _DEFAULT_HEADER:
        return None
    return f"{int(header, 16) + 8:03X}"


def new_sys_schedule():
    """항목별 폴링 상태: 다음 요청 시각, 연속 실패 수, 미지원 여부"""
    return {name: {"next": 0.0, "fails": 0, "unsupported": False}
            for name in POLL_ITEMS if name in config.OBD_SYS_INTERVALS}


def pick_due(schedule: dict, now: float):
    """요청할 때가 된 항목 중 '주기 대비' 가장 많이 밀린 것 (없으면 None).
    전부 밀려 있을 때(요청 처리량보다 요구량이 많을 때) 주기가 짧은 항목이 굶지 않고,
    모든 항목이 주기에 비례해서 고르게 느려지도록 함."""
    best, best_ratio = None, None
    for name, st in schedule.items():
        if st["next"] > now:
            continue
        interval = config.OBD_SYS_UNSUPPORTED_RETRY_SEC if st["unsupported"] else config.OBD_SYS_INTERVALS[name]
        ratio = (now - st["next"]) / interval
        if best_ratio is None or ratio > best_ratio:
            best, best_ratio = name, ratio
    return best


def update_after_poll(schedule: dict, name: str, ok: bool, now: float):
    """요청 결과 반영 -> 다음 요청 시각. 미지원으로 막 바뀌었으면 True 반환 (로그용)"""
    st = schedule[name]
    newly_unsupported = False
    if ok:
        st["fails"] = 0
        st["unsupported"] = False
    else:
        st["fails"] += 1
        if st["fails"] >= config.OBD_SYS_FAIL_LIMIT and not st["unsupported"]:
            st["unsupported"] = True
            newly_unsupported = True
    wait = config.OBD_SYS_UNSUPPORTED_RETRY_SEC if st["unsupported"] else config.OBD_SYS_INTERVALS[name]
    st["next"] = now + wait
    return newly_unsupported


class CanMonitorThread(QThread):
    speed_updated = pyqtSignal(float)                      # km/h
    rpm_updated = pyqtSignal(float)                        # 엔진 RPM
    scc_status_updated = pyqtSignal(bool, bool, float)      # main_on, active, set_speed_kph (지금은 미사용)
    lfa_status_updated = pyqtSignal(bool)                   # active (지금은 미사용)
    hda_curve_updated = pyqtSignal(bool)                    # active (지금은 미사용)
    sys_values_updated = pyqtSignal(str, dict)              # 항목 이름, {키: 값} (차량 시스템 화면)
    connection_error = pyqtSignal(str)

    def __init__(self, address=None, tx_uuid=None, rx_uuid=None, parent=None):
        super().__init__(parent)
        self._address = address or config.OBD_BLE_ADDRESS
        self._tx_uuid = tx_uuid or config.OBD_BLE_TX_CHAR_UUID
        self._rx_uuid = rx_uuid or config.OBD_BLE_RX_CHAR_UUID
        self._running = False
        self._rx_buffer = b""
        self._prompt_event = None   # 응답 끝 프롬프트('>') 수신 알림 (asyncio 루프 안에서 생성)
        self._header = None         # 지금 설정된 요청 헤더 (None = 모름)
        self._base_header = None    # 속도/RPM/표준 PID 요청 헤더 (연결 후 결정)
        self._header_switch_ok = True
        self._sys_schedule = new_sys_schedule()

    def run(self):
        self._running = True
        try:
            asyncio.run(self._async_main())
        except Exception as exc:
            self.connection_error.emit(f"BLE 스레드 종료됨: {type(exc).__name__}: {exc}")

    async def _async_main(self):
        while self._running:
            try:
                await self._connect_and_poll()
            except Exception as exc:
                self.connection_error.emit(f"BLE 연결 끊김/실패, 재연결 시도: {type(exc).__name__}: {exc}")

            if self._running:
                await asyncio.sleep(config.OBD_RECONNECT_INTERVAL_SEC)

    async def _connect(self):
        """이미 연결된 상태면 스캔에 안 잡힐 수 있으므로, 먼저 주소로 직접
        연결을 시도하고 실패할 때만 스캔으로 기기를 찾아서 연결한다."""
        self.connection_error.emit(f"BLE 직접 연결 시도 중: {self._address}")
        try:
            client = BleakClient(self._address, timeout=6.0)
            await client.connect()
            self.connection_error.emit(f"BLE 연결 성공(직접): {self._address}")
            return client
        except Exception:
            self.connection_error.emit("직접 연결 실패, 스캔으로 재시도")
            device = await BleakScanner.find_device_by_address(self._address, timeout=10.0)
            if device is None:
                return None
            client = BleakClient(device, timeout=15.0)
            await client.connect()
            self.connection_error.emit(f"BLE 연결 성공(스캔후): {self._address}")
            return client

    async def _connect_and_poll(self):
        client = await self._connect()
        if client is None:
            raise BleakError(f"주소 {self._address} 기기를 못 찾음 (전원/거리 확인 필요)")

        self._prompt_event = asyncio.Event()
        try:
            await client.start_notify(self._rx_uuid, self._on_notify)

            await self._send_at(client, "ATZ", wait_sec=1.0)
            await self._send_at(client, "ATE0")
            await self._send_at(client, "ATL0")
            await self._send_at(client, "ATSP6")   # 현대차 표준: CAN 11bit 500kbps 고정
            self._header = _DEFAULT_HEADER   # ATZ 직후 기본값
            await self._init_base_header(client)
            self.connection_error.emit(f"초기화 완료, 속도/RPM 폴링 시작 (헤더 {self._base_header})")

            while self._running and client.is_connected:
                await self._ensure_header(client, None, None)
                text = await self._request(client, "010D")
                match = _SPEED_RESPONSE_PATTERN.search(text)
                if match:
                    self.speed_updated.emit(float(int(match.group(1), 16)))

                if config.OBD_POLL_RPM:
                    text = await self._request(client, "010C")
                    match = _RPM_RESPONSE_PATTERN.search(text)
                    if match:
                        rpm = (int(match.group(1), 16) * 256 + int(match.group(2), 16)) / 4.0
                        self.rpm_updated.emit(rpm)

                if config.OBD_SYS_ENABLED:
                    for _ in range(config.OBD_SYS_ITEMS_PER_CYCLE):
                        name = pick_due(self._sys_schedule, time.monotonic())
                        if name is None:
                            break
                        await self._poll_sys_item(client, name)

                await asyncio.sleep(config.OBD_POLL_INTERVAL_SEC)
        finally:
            await client.disconnect()

    async def _init_base_header(self, client: BleakClient):
        """기본 헤더 결정: OBD_BASE_HEADER(7E0) 로 전환이 되면 그걸 쓰고, 어댑터가 ATSH 를
        거부하면 7DF 그대로 + 제조사 명령(헤더 전환 필요) 끔."""
        base = config.OBD_BASE_HEADER
        self._base_header = _DEFAULT_HEADER
        if not base or base == _DEFAULT_HEADER:
            return
        if await self._switch_header(client, base, _response_header(base)):
            self._base_header = base
            return
        self._header_switch_ok = False
        for name, item in POLL_ITEMS.items():
            if item[0] is not None and name in self._sys_schedule:
                self._sys_schedule[name]["unsupported"] = True
                self._sys_schedule[name]["next"] = float("inf")
        self.connection_error.emit("어댑터가 ATSH(헤더 지정)를 지원하지 않음 -> 7DF 로 표준 PID 만 요청, 제조사 명령 끔")

    async def _switch_header(self, client: BleakClient, header: str, rx_header) -> bool:
        reply = await self._request(client, f"ATSH{header}")
        if "?" in reply:
            return False
        await self._request(client, f"ATCRA{rx_header}" if rx_header else "ATCRA")
        self._header = header
        return True

    async def _ensure_header(self, client: BleakClient, header, rx_header):
        """요청 헤더 전환 (같으면 생략). header=None 이면 기본 헤더(속도/RPM/표준 PID)."""
        if header is None:
            header = self._base_header
            rx_header = _response_header(header)
        if self._header == header:
            return True
        return await self._switch_header(client, header, rx_header)

    async def _poll_sys_item(self, client: BleakClient, name: str):
        header, rx_header, command, _, _ = POLL_ITEMS[name]
        if await self._ensure_header(client, header, rx_header):
            timeout = config.OBD_MODE22_TIMEOUT_SEC if command.startswith("22") else config.OBD_RESPONSE_TIMEOUT_SEC
            text = await self._request(client, command, timeout)
        else:
            text = "?"
        values = decode_item(name, text)
        if values is not None:
            self.sys_values_updated.emit(name, values)
        if update_after_poll(self._sys_schedule, name, values is not None, time.monotonic()):
            self.connection_error.emit(
                f"{name}({header or self._base_header} {command}) 응답 없음 -> 미지원으로 보고 "
                f"{config.OBD_SYS_UNSUPPORTED_RETRY_SEC:.0f}초마다 재시도. 마지막 응답: {text.strip()[:60]!r}")

    async def _request(self, client: BleakClient, command: str, timeout: float = None) -> str:
        """명령을 보내고 프롬프트('>')가 올 때까지(최대 timeout, 기본 OBD_RESPONSE_TIMEOUT_SEC)
        기다린 뒤 받은 응답 문자열을 반환."""
        self._rx_buffer = b""
        self._prompt_event.clear()
        await client.write_gatt_char(self._tx_uuid, (command + "\r").encode("ascii"), response=True)
        try:
            await asyncio.wait_for(self._prompt_event.wait(), timeout or config.OBD_RESPONSE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            pass
        return self._rx_buffer.decode("ascii", errors="ignore")

    async def _send_at(self, client: BleakClient, command: str, wait_sec: float = 0.3):
        await client.write_gatt_char(self._tx_uuid, (command + "\r").encode("ascii"), response=True)
        await asyncio.sleep(wait_sec)

    def _on_notify(self, sender, data: bytearray):
        self._rx_buffer += bytes(data)
        if len(self._rx_buffer) > 4096:   # 멀티프레임 응답(수백 글자)도 담을 수 있게
            self._rx_buffer = self._rx_buffer[-4096:]
        if b">" in data and self._prompt_event is not None:
            self._prompt_event.set()

    def stop(self):
        self._running = False
        self.wait(2000)
