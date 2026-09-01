"""
인포카 BLE OBD 어댑터(FFF0 서비스, FFF1=notify RX, FFF2=write TX)를 통해
표준 OBD PID 요청-응답으로 차량 속도만 가져옵니다.

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

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from PyQt6.QtCore import QThread, pyqtSignal

import config

# "41 0D XX" (표준 PID 0D 응답: 속도, km/h) 패턴. 사이사이 공백/개행/프롬프트(>) 섞여도 매칭되게 함.
_SPEED_RESPONSE_PATTERN = re.compile(r"41\s*0D\s*([0-9A-Fa-f]{2})")


class CanMonitorThread(QThread):
    speed_updated = pyqtSignal(float)                      # km/h
    scc_status_updated = pyqtSignal(bool, bool, float)      # main_on, active, set_speed_kph (지금은 미사용)
    lfa_status_updated = pyqtSignal(bool)                   # active (지금은 미사용)
    hda_curve_updated = pyqtSignal(bool)                    # active (지금은 미사용)
    connection_error = pyqtSignal(str)

    def __init__(self, address=None, tx_uuid=None, rx_uuid=None, parent=None):
        super().__init__(parent)
        self._address = address or config.OBD_BLE_ADDRESS
        self._tx_uuid = tx_uuid or config.OBD_BLE_TX_CHAR_UUID
        self._rx_uuid = rx_uuid or config.OBD_BLE_RX_CHAR_UUID
        self._running = False
        self._rx_buffer = b""

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

        try:
            await client.start_notify(self._rx_uuid, self._on_notify)

            await self._send_at(client, "ATZ", wait_sec=1.0)
            await self._send_at(client, "ATE0")
            await self._send_at(client, "ATL0")
            await self._send_at(client, "ATSP6")   # 현대차 표준: CAN 11bit 500kbps 고정
            self.connection_error.emit("초기화 완료, 속도 폴링 시작")

            while self._running and client.is_connected:
                self._rx_buffer = b""
                await client.write_gatt_char(self._tx_uuid, b"010D\r", response=True)
                await asyncio.sleep(0.4)  # 응답 도착 대기

                match = _SPEED_RESPONSE_PATTERN.search(
                    self._rx_buffer.decode("ascii", errors="ignore"))
                if match:
                    speed_kph = int(match.group(1), 16)
                    self.speed_updated.emit(float(speed_kph))

                await asyncio.sleep(0.3)  # 폴링 간격 (너무 빠르면 어댑터가 못 따라갈 수 있음)
        finally:
            await client.disconnect()

    async def _send_at(self, client: BleakClient, command: str, wait_sec: float = 0.3):
        await client.write_gatt_char(self._tx_uuid, (command + "\r").encode("ascii"), response=True)
        await asyncio.sleep(wait_sec)

    def _on_notify(self, sender, data: bytearray):
        self._rx_buffer += bytes(data)
        if len(self._rx_buffer) > 256:
            self._rx_buffer = self._rx_buffer[-256:]

    def stop(self):
        self._running = False
        self.wait(2000)
