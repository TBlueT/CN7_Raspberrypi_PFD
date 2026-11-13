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
from services.obd_decode import POLL_ITEMS, decode_item, incomplete_multiframe, parse_supported_pids

# "41 0D XX" (표준 PID 0D 응답: 속도, km/h) 패턴. 사이사이 공백/개행/프롬프트(>) 섞여도 매칭되게 함.
_SPEED_RESPONSE_PATTERN = re.compile(r"41\s*0D\s*([0-9A-Fa-f]{2})")
# "41 0C AA BB" (표준 PID 0C 응답: 엔진 RPM = (AA*256 + BB) / 4)
_RPM_RESPONSE_PATTERN = re.compile(r"41\s*0C\s*([0-9A-Fa-f]{2})\s*([0-9A-Fa-f]{2})")

_DEFAULT_HEADER = "7DF"   # 표준 OBD 기능 주소 (모든 ECU 에 묻기, ATZ 직후 기본값)
_VOLT_PATTERN = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*V", re.IGNORECASE)


def parse_atrv(text: str):
    """ATRV 응답("12.6V") -> 전압. 형식이 다르거나 범위(5~20V) 밖이면 None"""
    m = _VOLT_PATTERN.search(text or "")
    if not m:
        return None
    volt = float(m.group(1))
    return volt if 5.0 <= volt <= 20.0 else None


def _response_header(header: str):
    """요청 헤더 -> 응답 헤더 (11bit 진단: +8). 7DF(기능 주소)는 응답이 여러 곳이라 None(필터 해제)"""
    if header is None or header == _DEFAULT_HEADER:
        return None
    return f"{int(header, 16) + 8:03X}"


def new_sys_schedule():
    """항목별 폴링 상태: 다음 요청 시각, 연속 실패 수, 미지원 여부, 한 번이라도 성공했는지
    + 진단 통계(ok/끊긴 응답 앞부분으로 성공/total/응답시간 합/마지막 실패 응답)"""
    return {name: {"next": 0.0, "fails": 0, "unsupported": False, "ever_ok": False,
                   "ok": 0, "partial": 0, "total": 0, "time_sum": 0.0, "last_fail": ""}
            for name in POLL_ITEMS if name in config.OBD_SYS_INTERVALS}


def pick_due(schedule: dict, now: float, exclude=None):
    """요청할 때가 된 항목 중 '주기 대비' 가장 많이 밀린 것 (없으면 None).
    전부 밀려 있을 때(요청 처리량보다 요구량이 많을 때) 주기가 짧은 항목이 굶지 않고,
    모든 항목이 주기에 비례해서 고르게 느려지도록 함."""
    best, best_ratio = None, None
    for name, st in schedule.items():
        if st["next"] > now or (exclude and name in exclude):
            continue
        interval = config.OBD_SYS_UNSUPPORTED_RETRY_SEC if st["unsupported"] else config.OBD_SYS_INTERVALS[name]
        ratio = (now - st["next"]) / interval
        if best_ratio is None or ratio > best_ratio:
            best, best_ratio = name, ratio
    return best


def update_after_poll(schedule: dict, name: str, ok: bool, now: float, transient: bool = False):
    """요청 결과 반영 -> 다음 요청 시각. 미지원으로 막 바뀌었으면 True 반환 (로그용).
    한 번이라도 응답한 항목은 실패해도 일시적인 것으로 보고 원래 주기로 계속 요청
    (예전에는 3번 연속 실패면 60초 쉬어서 '들어오다 끊기는' 원인이 됐음)."""
    st = schedule[name]
    newly_unsupported = False
    if not ok and transient:
        # 긴 응답이 중간에 끊긴 것 = 차는 이 명령을 지원함 (전송만 실패) -> 미지원 판정에 안 셈
        # (실차 2026-11-10: 조향각이 처음 3번 끊겨서 미지원 처리 -> 60초 동안 안 받던 문제)
        st["next"] = now + config.OBD_SYS_INTERVALS[name]
        return False
    if ok:
        st["fails"] = 0
        st["unsupported"] = False
        st["ever_ok"] = True
    else:
        st["fails"] += 1
        if (st["fails"] >= config.OBD_SYS_FAIL_LIMIT and not st["unsupported"]
                and not st["ever_ok"]):
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
        # 진단 (OBD_DIAG_INTERVAL_SEC 마다 요약 로그)
        self._diag_start = time.monotonic()
        self._diag_speed = 0
        self._diag_late = 0        # 제한시간 넘겨서 늦게 온 응답
        self._diag_noprompt = 0    # 끝내 프롬프트가 안 온 응답
        self._diag_retry = 0       # 끊긴 긴 응답 재요청 수
        self._diag_last_time = 0.0
        self._sys_schedule = new_sys_schedule()
        # 어댑터 기능 (자동 판단, 안 되면 기존 방식)
        self._resp_count_ok = None  # 응답 개수 지정('010D1') 지원 여부 (None = 아직 모름)
        self._atrv_ok = None        # ATRV(어댑터가 직접 재는 배터리 전압) 지원 여부
        self._atrv_fails = 0
        self._fc_ok = False         # 흐름 제어 주소 지정(ATFCSH) 사용 중
        self._wide_filter = False   # 응답 필터 7xx 전체 (헤더 바꿀 때 ATCRA 생략)
        self._item_cmd = {}         # 항목 -> 대안 명령 (예: 냉각수 0105 미지원 -> 0167)
        self._fail_logged = set()   # 실패 응답 전체를 로그로 남긴 항목 (실행당 한 번)
        self._partial_logged = set()  # 끊긴 응답 앞부분으로 처음 읽었을 때 로그 남긴 항목 (실행당 한 번)

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
            info = (await self._request(client, "ATI")).replace("\r", " ").replace(">", "").strip()
            self.connection_error.emit(f"어댑터 정보(ATI): {info!r}")
            await self._probe_atrv(client)
            await self._init_wide_filter(client)
            if config.OBD_COMPACT_OUTPUT:
                reply = await self._request(client, "ATS0")
                self.connection_error.emit("응답 공백 제거(ATS0) " + ("미지원" if "?" in reply else "사용"))
            await self._init_flow_control(client)
            # 응답 개수 지정 확인 (되면 그 자리에서 타이밍 고정) + 지원 PID 확인
            await self._std_request(client, "010D", _SPEED_RESPONSE_PATTERN.search)
            await self._read_supported_pids(client)
            await self._run_startup_probes(client)
            self.connection_error.emit(f"초기화 완료, 속도/RPM 폴링 시작 (헤더 {self._base_header})")

            cycle = 0
            while self._running and client.is_connected:
                await self._ensure_header(client, None, None)
                text = await self._std_request(client, "010D", _SPEED_RESPONSE_PATTERN.search)
                match = _SPEED_RESPONSE_PATTERN.search(text)
                if match:
                    self.speed_updated.emit(float(int(match.group(1), 16)))
                    self._diag_speed += 1

                if config.OBD_POLL_RPM:
                    text = await self._std_request(client, "010C", _RPM_RESPONSE_PATTERN.search)
                    match = _RPM_RESPONSE_PATTERN.search(text)
                    if match:
                        rpm = (int(match.group(1), 16) * 256 + int(match.group(2), 16)) / 4.0
                        self.rpm_updated.emit(rpm)

                if config.OBD_SYS_ENABLED:
                    cycle += 1
                    await self._poll_cycle_items(client, cycle)

                self._maybe_log_diag()
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
        if not self._wide_filter:
            await self._request(client, f"ATCRA{rx_header}" if rx_header else "ATCRA")
        if self._fc_ok:
            await self._request(client, f"ATFCSH{header}")
        self._header = header
        return True

    async def _init_wide_filter(self, client: BleakClient):
        """응답 필터를 7xx 전체로 (ATCF700 + ATCM700: 위 3비트만 7 이면 통과). 물리 주소로 묻기 때문에
        답하는 건 그 모듈뿐이라 안전하고, 헤더를 바꿀 때 ATCRA 를 생략할 수 있음."""
        if not config.OBD_WIDE_RX_FILTER or not self._header_switch_ok or self._base_header == _DEFAULT_HEADER:
            return
        r1 = await self._request(client, "ATCF700")
        r2 = await self._request(client, "ATCM700")
        if "?" in r1 or "?" in r2:
            await self._request(client, f"ATCRA{_response_header(self._base_header)}")
            self.connection_error.emit("응답 필터 7xx(ATCF/ATCM) 미지원 -> 헤더마다 ATCRA")
            return
        self._wide_filter = True
        self.connection_error.emit("응답 필터 7xx 전체 (ATCF700 + ATCM700) -> 헤더 전환 시 ATCRA 생략")

    async def _run_startup_probes(self, client: BleakClient):
        """확인용 요청을 한 번씩 보내 응답을 로그로 남김 (대체 명령 찾기용, 화면에는 안 씀)"""
        if not self._header_switch_ok:
            return
        for header, command in config.OBD_STARTUP_PROBES:
            if not await self._ensure_header(client, header, _response_header(header)):
                continue
            text = await self._request(client, command, config.OBD_MODE22_TIMEOUT_SEC)
            self.connection_error.emit(f"확인용 요청 {header} {command} 응답: {text.strip()[:300]!r}")
        await self._ensure_header(client, None, None)

    def _fast_items(self):
        """매 바퀴 요청 항목들 (설정이 꺼져 있으면 빈 목록)"""
        if not config.OBD_FAST_EVERY_CYCLES:
            return []
        return [n for n in config.OBD_FAST_ITEMS if n in self._sys_schedule]

    def _uses_base_header(self, name):
        header = POLL_ITEMS[name][0]
        return header is None or header == self._base_header or (name == "batt" and self._atrv_ok)

    async def _poll_cycle_items(self, client: BleakClient, cycle: int):
        """한 바퀴 항목 요청. 헤더 전환을 줄이도록 기본 헤더(엔진 ECU) 항목을 먼저 하고
        다른 모듈 항목은 뒤로: [기본 헤더 빠른 항목, 기본 헤더 일반 항목] -> [다른 헤더 빠른/일반 항목]"""
        now = time.monotonic()
        fast = []
        if self._fast_items() and cycle % config.OBD_FAST_EVERY_CYCLES == 0:
            for name in self._fast_items():
                st = self._sys_schedule[name]
                if not st["unsupported"] or st["next"] <= now:
                    fast.append(name)
        normal = []
        for _ in range(config.OBD_SYS_ITEMS_PER_CYCLE):
            name = pick_due(self._sys_schedule, now, exclude=set(self._fast_items()) | set(normal))
            if name is None:
                break
            normal.append(name)
        order = ([n for n in fast + normal if self._uses_base_header(n)]
                 + [n for n in fast if not self._uses_base_header(n)]
                 + [n for n in normal if not self._uses_base_header(n)])
        for name in order:
            await self._poll_sys_item(client, name)

    async def _init_flow_control(self, client: BleakClient):
        """긴 응답(멀티프레임): 첫 프레임을 받은 어댑터가 ECU 에 '계속 보내라'(흐름 제어 30 BS STmin)를
        보내면 ECU 가 나머지 프레임을 보냄. OBD_FLOW_CONTROL 이면 STmin 을 넣어 천천히 보내게 함
        (ATFCSD + ATFCSM1, 헤더 바꿀 때마다 ATFCSH). 기본은 끔(어댑터 자동).
        ATFCSM2(주소 자동)는 실차에서 문잠금/연료까지 NO DATA 가 돼서 쓰지 않음."""
        if not config.OBD_FLOW_CONTROL or not self._header_switch_ok:
            return
        stmin = max(0, min(0x7F, int(config.OBD_FC_STMIN_MS)))
        data = f"3000{stmin:02X}"
        replies = [await self._request(client, cmd) for cmd in
                   (f"ATFCSH{self._header}", f"ATFCSD{data}", "ATFCSM1")]
        if any("?" in r for r in replies):
            await self._request(client, "ATFCSM0")
            self.connection_error.emit("흐름 제어 지정 미지원 -> 자동 모드 유지")
            return
        self._fc_ok = True       # 헤더 바꿀 때마다 ATFCSH 도 같이
        self.connection_error.emit(f"흐름 제어: 30 00 {stmin:02X} (프레임 간격 {stmin}ms, ATFCSM1, 헤더마다 지정)")

    async def _apply_fixed_timing(self, client: BleakClient):
        """적응형 타이밍(ATAT1, 기본)은 빠른 엔진 응답에 맞춰 대기 시간을 줄여서, 느린 모듈의
        나머지 프레임을 못 기다리고 첫 프레임만 남는 원인이 됨 -> ATAT0 + ATST 고정.
        응답 개수 지정이 될 때만 적용 (그래야 표준 PID 요청이 대기 시간만큼 느려지지 않음)."""
        if not config.OBD_FIXED_TIMING:
            return
        value = max(1, min(255, round(config.OBD_ELM_TIMEOUT_MS / 4)))
        r1 = await self._request(client, "ATAT0")
        r2 = await self._request(client, f"ATST{value:02X}")
        if "?" in r1 or "?" in r2:
            self.connection_error.emit(f"타이밍 고정 미지원 (ATAT0 {r1.strip()[:10]!r}, ATST {r2.strip()[:10]!r})")
        else:
            self.connection_error.emit(f"타이밍 고정: ATAT0 + ATST{value:02X} ({value * 4}ms)")

    async def _read_supported_pids(self, client: BleakClient):
        """기본 헤더 ECU 의 지원 PID 목록(0100/0120/0140/0160)을 읽어 로그로 남기고,
        냉각수 0105 가 없고 0167 이 있으면 냉각수는 0167 로 요청."""
        supported = set()
        for base in (0x00, 0x20, 0x40, 0x60):
            command = f"01{base:02X}"
            text = await self._std_request(client, command,
                                           lambda t, b=base: parse_supported_pids(t, b) is not None)
            pids = parse_supported_pids(text, base)
            if pids is None:
                if base == 0x00:
                    self.connection_error.emit(f"지원 PID 목록 못 읽음 ({text.strip()[:30]!r})")
                    return
                break
            supported |= pids
            if base + 0x20 not in pids:
                break
        listed = " ".join(f"{p:02X}" for p in sorted(supported) if p % 0x20)
        self.connection_error.emit(f"지원 PID ({self._header}): {listed}")
        if 0x05 not in supported and 0x67 in supported:
            self._item_cmd["coolant"] = "0167"
            self.connection_error.emit("냉각수: 0105 미지원, 0167(센서 1/2) 지원 -> 0167 로 요청")
        elif 0x05 not in supported:
            self.connection_error.emit("냉각수: 0105/0167 둘 다 지원 목록에 없음")

    async def _ensure_header(self, client: BleakClient, header, rx_header):
        """요청 헤더 전환 (같으면 생략). header=None 이면 기본 헤더(속도/RPM/표준 PID)."""
        if header is None:
            header = self._base_header
            rx_header = _response_header(header)
        if self._header == header:
            return True
        return await self._switch_header(client, header, rx_header)

    async def _probe_atrv(self, client: BleakClient):
        """ATRV(OBD 포트 16번 핀 전압을 어댑터가 직접 측정) 지원 확인.
        되면 배터리 전압은 ECU 에 묻지 않고(0142) 이걸로 읽음 - 헤더 전환도 필요 없음."""
        if not config.OBD_USE_ATRV:
            self._atrv_ok = False
            return
        reply = await self._request(client, "ATRV")
        volt = parse_atrv(reply)
        self._atrv_ok = volt is not None
        if self._atrv_ok:
            self.connection_error.emit(f"ATRV 지원 ({volt:.1f}V) -> 배터리 전압은 어댑터에서 직접 읽음")
        else:
            self.connection_error.emit(f"ATRV 미지원 ({reply.strip()[:30]!r}) -> 배터리 전압은 0142 로 요청")

    async def _std_request(self, client: BleakClient, command: str, is_ok, timeout: float = None) -> str:
        """응답이 하나인 표준 PID(01xx) 요청. 어댑터가 응답 개수 지정을 지원하면 명령 끝에 '1'
        을 붙여서(예: 010D1) 응답 하나를 받는 즉시 끝내게 함 -> 다른 ECU 응답을 기다리는
        시간만큼 빨라짐. 지원 여부는 처음 요청들로 자동 판단:
        '1' 붙인 요청이 성공 -> 지원 / 실패했는데 그냥 요청은 성공 -> 미지원(이후 기존 방식)."""
        if self._resp_count_ok is False or not config.OBD_USE_RESPONSE_COUNT:
            return await self._request(client, command, timeout)
        text = await self._request(client, command + "1", timeout)
        if self._resp_count_ok:
            return text
        if is_ok(text):
            self._resp_count_ok = True
            self.connection_error.emit(f"응답 개수 지정 지원 ({command}1) -> 표준 PID 요청에 사용")
            await self._apply_fixed_timing(client)
            return text
        plain = await self._request(client, command, timeout)
        if is_ok(plain):
            self._resp_count_ok = False
            self.connection_error.emit(f"응답 개수 지정 미지원 ({command}1 -> {text.strip()[:30]!r}) -> 기존 방식")
        return plain

    async def _poll_sys_item(self, client: BleakClient, name: str):
        header, rx_header, command, _, _ = POLL_ITEMS[name]
        command = self._item_cmd.get(name, command)
        if name == "batt" and self._atrv_ok:
            command = "ATRV"
            text = await self._request(client, "ATRV")
            volt = parse_atrv(text)
            values = None if volt is None else {"batt_v": volt}
            self._atrv_fails = 0 if volt is not None else self._atrv_fails + 1
            if self._atrv_fails >= config.OBD_SYS_FAIL_LIMIT:
                self._atrv_ok = False
                self.connection_error.emit(f"ATRV 응답 이상 ({text.strip()[:30]!r}) -> 배터리 전압은 0142 로 요청")
        elif await self._ensure_header(client, header, rx_header):
            if command.startswith("01"):
                text = await self._std_request(client, command,
                                               lambda t: decode_item(name, t, command) is not None)
            else:
                text = await self._request(client, command, config.OBD_MODE22_TIMEOUT_SEC)
                retries = 0 if name in self._fast_items() else config.OBD_MODE22_RETRY
                while (retries > 0 and incomplete_multiframe(text)    # 중간에 끊겨서 값을 못 읽음 -> 바로 다시
                       and decode_item(name, text, command) is None):
                    retries -= 1
                    self._diag_retry += 1
                    text = await self._request(client, command, config.OBD_MODE22_TIMEOUT_SEC)
            values = decode_item(name, text, command)
            if incomplete_multiframe(text) and config.OBD_SETTLE_AFTER_CUT_SEC:
                # 끊긴 응답의 나머지가 늦게 와서 다음 요청 응답에 섞이지 않게 잠깐 기다렸다가 버림
                await asyncio.sleep(config.OBD_SETTLE_AFTER_CUT_SEC)
                self._rx_buffer = b""
        else:
            text = "?"
            values = None
        st = self._sys_schedule[name]
        st["total"] += 1
        st["time_sum"] += self._diag_last_time
        if values is not None:
            st["ok"] += 1
            if incomplete_multiframe(text):           # 끊긴 응답의 앞부분으로 읽음
                st["partial"] += 1
                if name not in self._partial_logged:
                    self._partial_logged.add(name)
                    self.connection_error.emit(
                        f"{name}({header or self._base_header} {command}) 끊긴 응답 앞부분으로 값 읽음: "
                        f"{values} / 응답 {text.strip()[:300]!r}")
            self.sys_values_updated.emit(name, values)
        else:
            st["last_fail"] = text.strip().replace("\r", " ")[:40]
            if name not in self._fail_logged:     # 실패 응답 전체를 실행당 한 번 남김 (원인 분석용)
                self._fail_logged.add(name)
                self.connection_error.emit(
                    f"{name}({header or self._base_header} {command}) 첫 실패 응답 전체: {text.strip()[:300]!r}")
        transient = values is None and incomplete_multiframe(text)
        if update_after_poll(self._sys_schedule, name, values is not None, time.monotonic(), transient):
            self.connection_error.emit(
                f"{name}({header or self._base_header} {command}) 응답 없음 -> 미지원으로 보고 "
                f"{config.OBD_SYS_UNSUPPORTED_RETRY_SEC:.0f}초마다 재시도")

    async def _request(self, client: BleakClient, command: str, timeout: float = None) -> str:
        """명령을 보내고 프롬프트('>')가 올 때까지 기다린 뒤 받은 응답 문자열을 반환.

        제한시간(timeout, 기본 OBD_RESPONSE_TIMEOUT_SEC) 안에 프롬프트가 안 오면
        OBD_LATE_PROMPT_WAIT_SEC 만큼 더 기다림: ELM327 은 응답 중에 새 명령을 받으면
        하던 일을 STOPPED 로 끊고 새 명령도 버리기 때문에, 바로 다음 명령을 보내면
        이후 요청들이 줄줄이 실패함 (값이 들어오다 끊기는 원인). 늦게 온 응답도 그대로 사용."""
        self._rx_buffer = b""
        self._prompt_event.clear()
        t0 = time.monotonic()
        await client.write_gatt_char(self._tx_uuid, (command + "\r").encode("ascii"), response=True)
        try:
            await asyncio.wait_for(self._prompt_event.wait(), timeout or config.OBD_RESPONSE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            self._diag_late += 1
            try:
                await asyncio.wait_for(self._prompt_event.wait(), config.OBD_LATE_PROMPT_WAIT_SEC)
            except asyncio.TimeoutError:
                self._diag_noprompt += 1
        self._diag_last_time = time.monotonic() - t0
        return self._rx_buffer.decode("ascii", errors="ignore")

    def _maybe_log_diag(self):
        """OBD_DIAG_INTERVAL_SEC 마다 한 줄 요약: 속도 갱신 주기, 늦은/무응답 수, 항목별 성공률·응답시간"""
        if not config.OBD_DIAG_INTERVAL_SEC:
            return
        now = time.monotonic()
        span = now - self._diag_start
        if span < config.OBD_DIAG_INTERVAL_SEC:
            return
        parts = [f"속도 {self._diag_speed}회({span / max(1, self._diag_speed):.2f}s)",
                 f"늦은응답 {self._diag_late}", f"무응답 {self._diag_noprompt}", f"재요청 {self._diag_retry}"]
        for name, st in self._sys_schedule.items():
            if st["total"] == 0:
                parts.append(f"{name} -")
                continue
            item = f"{name} {st['ok']}/{st['total']} {st['time_sum'] / st['total']:.2f}s"
            if st["partial"]:
                item += f" (앞부분 {st['partial']})"
            if st["ok"] < st["total"]:
                item += f" 실패응답={st['last_fail']!r}"
            if st["unsupported"]:
                item += " (미지원)"
            parts.append(item)
            st["ok"] = st["partial"] = st["total"] = 0
            st["time_sum"] = 0.0
        self.connection_error.emit(f"{span:.0f}초 요약: " + " | ".join(parts))
        self._diag_start = now
        self._diag_speed = self._diag_late = self._diag_noprompt = self._diag_retry = 0

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
