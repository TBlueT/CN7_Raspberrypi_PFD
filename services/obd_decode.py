"""
OBD 응답 해석 (순수 함수 - Qt/BLE 와 무관하게 테스트 가능)

1) ELM327 응답 텍스트 -> 데이터 바이트 (멀티프레임 "0: .. 1: .." 형식 포함)
2) 바이트에서 비트 위치(bix)로 값 추출 (OBDb signalsets 규칙)
3) 차량 시스템 화면 항목별 해석기 (POLL_ITEMS)

비트 위치 규칙 (OBDb, CN7 실차 응답 10,224개로 검증됨):
  응답에서 "62 DID"(Mode 01 이면 "41 PID")를 뺀 나머지 바이트가 기준이고,
  bix 는 그 첫 바이트의 최상위 비트를 0 으로 세는 big-endian 비트 번호.
  값 = raw(부호 있으면 2의 보수) x mul / div + add, null 값이면 None, min/max 로 제한.

출처: https://github.com/OBDb/Hyundai-Elantra signalsets/v3/default.json
"""

import re

import config

_ERROR_WORDS = ("NO DATA", "ERROR", "UNABLE", "STOPPED", "BUFFER FULL", "?")
_LENGTH_LINE = re.compile(r"^[0-9A-F]{3}$")
_FRAME_LINE = re.compile(r"^([0-9A-F]):\s*(.*)$")


def parse_elm_response(text: str):
    """ELM327 응답 텍스트를 데이터 바이트로 변환. 오류/빈 응답이면 None.
    - 단일 프레임: "62 E0 01 .." 그대로
    - 멀티 프레임(CAF 켜짐): "02C" (길이) 다음 "0: ..", "1: .." 줄들 -> 순서대로 이어붙이고 길이로 자름"""
    if not text:
        return None
    upper = text.upper().replace(">", "")
    if any(word in upper for word in _ERROR_WORDS):
        return None
    length = None
    frames = {}
    plain = []
    for raw_line in re.split(r"[\r\n]+", upper):
        line = raw_line.strip()
        if not line or line.startswith("SEARCHING"):
            continue
        if _LENGTH_LINE.match(line.replace(" ", "")) and length is None and not frames and not plain:
            length = int(line.replace(" ", ""), 16)
            continue
        m = _FRAME_LINE.match(line)
        if m:
            frames[int(m.group(1), 16)] = m.group(2)
        else:
            plain.append(line)
    if frames:
        hex_text = "".join(frames[i] for i in sorted(frames))
    else:
        hex_text = "".join(plain)
    hex_text = re.sub(r"[^0-9A-F]", "", hex_text)
    if len(hex_text) < 2:
        return None
    data = bytes.fromhex(hex_text[: len(hex_text) // 2 * 2])
    if length is not None:
        if len(data) < length:      # 멀티프레임 일부가 빠짐 -> 잘린 값으로 해석하지 않음
            return None
        data = data[:length]
    return data


def payload_after(data, prefix: bytes):
    """data 안에서 prefix("62 01 04" 등) 를 찾아 그 뒤 바이트 반환. 없으면 None."""
    if data is None:
        return None
    i = data.find(prefix)
    return None if i < 0 else data[i + len(prefix):]


def extract_raw(payload: bytes, bix: int, length: int, signed=False, little=False):
    """bix(최상위 비트=0, big-endian 비트 번호)에서 length 비트 정수 추출. 범위 밖이면 None."""
    if payload is None or bix < 0 or bix + length > len(payload) * 8:
        return None
    if little:
        if bix % 8 or length % 8:
            return None
        chunk = payload[bix // 8: bix // 8 + length // 8]
        raw = int.from_bytes(chunk, "little")
    else:
        whole = int.from_bytes(payload, "big")
        total = len(payload) * 8
        raw = (whole >> (total - bix - length)) & ((1 << length) - 1)
    if signed and raw & (1 << (length - 1)):
        raw -= 1 << length
    return raw


def decode(payload, bix, length, mul=1.0, div=1.0, add=0.0, signed=False, little=False,
           vmin=None, vmax=None, nullmin=None, nullmax=None):
    """OBDb 신호 하나 해석. null 값(nullmin/nullmax 와 같음)이면 None."""
    raw = extract_raw(payload, bix, length, signed, little)
    if raw is None:
        return None
    value = raw * mul / div + add
    if (nullmin is not None and value == nullmin) or (nullmax is not None and value == nullmax):
        return None
    if vmin is not None:
        value = max(vmin, value)
    if vmax is not None:
        value = min(vmax, value)
    return value


# ---------------------------------------------------------------------------
# 항목별 해석기: payload("62 DID"/"41 PID" 뒤 바이트) -> {키: 값}
# ---------------------------------------------------------------------------

def _steer_brake(p):
    return {
        "steer_deg": decode(p, 200, 16, div=10, signed=True, vmin=-3276.8, vmax=3276.7),
        "brake_pct": decode(p, 256, 16, div=86.7, signed=True, vmax=100),
    }


def _body(p):
    locked = config.SYS_DOOR_LOCKED_BIT
    fl = extract_raw(p, 36, 1)
    fr = extract_raw(p, 37, 1)
    rev = extract_raw(p, 35, 1)
    return {
        "lock_fl": None if fl is None else fl == locked,
        "lock_fr": None if fr is None else fr == locked,
        "reverse": None if rev is None else rev == 1,
    }


def _gear(p):
    if config.OBD_GEAR_LAYOUT == "2025":
        value = decode(p, 216, 8, vmax=15)
    else:
        value = decode(p, 188, 4, vmax=15)
    return {"gear": None if value is None else int(value)}


def _fuel(p):
    return {"fuel_l": decode(p, 32, 8, div=2, vmax=127.5, nullmax=127.5)}


_TIRE_BIX = {"FL": 32, "FR": 72, "RL": 112, "RR": 152}   # 공기압 위치. 온도 +8, 경고 +24


def _tpms(p):
    tires = {}
    for name, bix in _TIRE_BIX.items():
        pressure = decode(p, bix, 8, div=5, vmax=51, nullmin=0)
        temp = decode(p, bix + 8, 8, add=-50, vmin=-50, vmax=100, nullmin=-50)
        warn = extract_raw(p, bix + 24, 8)
        tires[name] = (pressure, temp, bool(warn) if warn is not None else False)
    return {"tires": tires}


def _oil(p):
    return {"oil_c": decode(p, 272, 8, mul=3, div=4, add=-48, vmin=-48, vmax=143)}


def _load(p):
    return {"load_pct": decode(p, 0, 8, mul=100, div=255)}


def _coolant(p):
    return {"coolant_c": decode(p, 0, 8, add=-40)}


def _batt(p):
    return {"batt_v": decode(p, 0, 16, div=1000)}


def _sat(p):
    return {"sat_c": decode(p, 0, 8, add=-40)}


# 이름: (요청 헤더(None=표준 7DF), 응답 헤더, 명령, 응답 접두 바이트, 해석기)
# 주기는 config.OBD_SYS_INTERVALS[이름]
POLL_ITEMS = {
    "steer_brake": ("7D1", "7D9", "220104", bytes.fromhex("620104"), _steer_brake),
    "body":        ("770", "778", "22BC04", bytes.fromhex("62BC04"), _body),
    "gear":        ("7E1", "7E9", "2201A4", bytes.fromhex("6201A4"), _gear),
    "load":        (None, None, "0104", bytes.fromhex("4104"), _load),
    "coolant":     (None, None, "0105", bytes.fromhex("4105"), _coolant),
    "batt":        (None, None, "0142", bytes.fromhex("4142"), _batt),
    "oil":         ("7E0", "7E8", "22E001", bytes.fromhex("62E001"), _oil),
    "sat":         (None, None, "0146", bytes.fromhex("4146"), _sat),
    "fuel":        ("7C6", "7CE", "22B002", bytes.fromhex("62B002"), _fuel),
    "tpms":        ("7A0", "7A8", "22C00B", bytes.fromhex("62C00B"), _tpms),
}


def decode_item(name: str, text: str):
    """항목 이름 + ELM 응답 텍스트 -> {키: 값}. 응답이 없거나 형식이 다르면 None."""
    _, _, _, prefix, decoder = POLL_ITEMS[name]
    payload = payload_after(parse_elm_response(text), prefix)
    if payload is None:
        return None
    values = decoder(payload)
    if all(v is None for v in values.values()):
        return None
    return values
