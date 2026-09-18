"""
소니 DSC-QX10 Wi-Fi 라이브뷰 캡처

QX10은 일반 USB 웹캠이 아니라 자체 Wi-Fi로 Sony Camera Remote API를 통해
JPEG 프레임을 스트리밍합니다. cv2.VideoCapture와 똑같은 인터페이스
(isOpened(), read() -> (성공여부, BGR 프레임))로 감싸서, camera_depth_reader.py
쪽 코드를 최소한만 바꾸면 되게 만들었습니다.

동작 순서:
  1. SSDP(M-SEARCH)로 같은 Wi-Fi 네트워크에서 카메라를 찾음 (LOCATION 헤더가
     가리키는 UPnP 기기 설명 XML의 URL을 얻음)
  2. 그 XML을 파싱해서 Camera Remote API의 실제 엔드포인트(액션 URL)를 얻음
  3. JSON-RPC로 "startLiveview" 호출 -> 라이브뷰 스트림 URL을 받음
  4. 그 URL에 HTTP GET을 걸어두고, 응답 스트림에서 공통헤더(8바이트) +
     페이로드헤더(128바이트, 그 중 4~6바이트가 JPEG 크기, 7바이트가 패딩
     크기) + JPEG 데이터 + 패딩을 반복해서 읽어 프레임 하나씩 뽑아냄

주의: 이 파일은 실제 QX10 없이는 이 샌드박스에서 끝까지 테스트 못 했습니다.
프레임 파싱 부분(_read_one_frame)은 공개된 Sony Camera Remote API 라이브뷰
프로토콜 문서/예제 코드 기준으로 작성했고, 가짜 바이트 스트림으로 단위
테스트는 했습니다. 실기에서 다음을 확인하세요:
  - SSDP 탐색이 실제로 카메라를 찾는지 (안 되면 카메라 IP를 config에 직접
    지정하는 SONY_QX10_FIXED_ENDPOINT_URL 사용)
  - startLiveview 응답 구조가 예상과 같은지 (버전에 따라
    startLiveviewWithSize가 필요할 수 있음)
"""

import http.client
import json
import socket
import struct
import time
import urllib.parse
import xml.etree.ElementTree as ET

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None


SSDP_MULTICAST_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_SEARCH_TARGET = "urn:schemas-sony-com:service:ScalarWebAPI:1"


class SonyQX10Capture:
    """cv2.VideoCapture와 같은 인터페이스: isOpened(), read(), release()."""

    def __init__(self, discovery_timeout_sec: float = 5.0,
                 fixed_endpoint_url: str = None):
        self._discovery_timeout_sec = discovery_timeout_sec
        self._fixed_endpoint_url = fixed_endpoint_url
        self._conn = None
        self._stream_response = None
        self._opened = False

    def isOpened(self) -> bool:
        return self._opened

    def open(self) -> bool:
        try:
            api_endpoint = self._fixed_endpoint_url or self._discover_camera()
            if not api_endpoint:
                return False

            liveview_url = self._start_liveview(api_endpoint)
            if not liveview_url:
                return False

            parsed = urllib.parse.urlparse(liveview_url)
            self._conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80,
                                                      timeout=10)
            path = parsed.path
            if parsed.query:
                path += "?" + parsed.query
            self._conn.request("GET", path)
            self._stream_response = self._conn.getresponse()

            self._opened = True
            return True
        except (OSError, http.client.HTTPException) as exc:
            print(f"[SonyQX10] 연결 실패: {type(exc).__name__}: {exc}")
            return False

    def _discover_camera(self):
        """SSDP M-SEARCH로 카메라를 찾아서 Camera Remote API 액션 URL을 반환."""
        message = "\r\n".join([
            "M-SEARCH * HTTP/1.1",
            f"HOST: {SSDP_MULTICAST_ADDR}:{SSDP_PORT}",
            'MAN: "ssdp:discover"',
            "MX: 3",
            f"ST: {SSDP_SEARCH_TARGET}",
            "", "",
        ]).encode("utf-8")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self._discovery_timeout_sec)
        sock.sendto(message, (SSDP_MULTICAST_ADDR, SSDP_PORT))

        location_url = None
        deadline = time.time() + self._discovery_timeout_sec
        while time.time() < deadline:
            try:
                data, _addr = sock.recvfrom(8192)
            except socket.timeout:
                break
            text = data.decode("utf-8", errors="ignore")
            for line in text.split("\r\n"):
                if line.lower().startswith("location:"):
                    location_url = line.split(":", 1)[1].strip()
                    break
            if location_url:
                break
        sock.close()

        if not location_url:
            return None

        return self._fetch_action_url_from_device_xml(location_url)

    def _fetch_action_url_from_device_xml(self, location_url: str):
        """UPnP 기기 설명 XML에서 Camera Remote API의 액션 URL을 뽑아낸다."""
        parsed = urllib.parse.urlparse(location_url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=5)
        conn.request("GET", parsed.path or "/")
        resp = conn.getresponse()
        xml_bytes = resp.read()
        conn.close()

        ns = {
            "d": "urn:schemas-upnp-org:device-1-0",
            "av": "urn:schemas-sony-com:av",
        }
        root = ET.fromstring(xml_bytes)
        for service in root.iter("{urn:schemas-sony-com:av}X_ScalarWebAPI_Service"):
            service_type = service.find("av:X_ScalarWebAPI_ServiceType", ns)
            action_url = service.find("av:X_ScalarWebAPI_ActionList_URL", ns)
            if service_type is not None and action_url is not None:
                if service_type.text == "camera":
                    return action_url.text.rstrip("/") + "/camera"
        return None

    def _start_liveview(self, api_endpoint: str):
        """JSON-RPC startLiveview 호출, 라이브뷰 스트림 URL을 반환."""
        payload = json.dumps({
            "method": "startLiveview",
            "params": [],
            "id": 1,
            "version": "1.0",
        }).encode("utf-8")

        parsed = urllib.parse.urlparse(api_endpoint)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=10)
        conn.request("POST", parsed.path, body=payload,
                      headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()

        result = body.get("result")
        if not result:
            print(f"[SonyQX10] startLiveview 응답 이상: {body}")
            return None
        return result[0]

    def read(self):
        if not self._opened or self._stream_response is None:
            return False, None
        try:
            frame = self._read_one_frame(self._stream_response)
            if frame is None:
                return False, None
            return True, frame
        except (OSError, http.client.HTTPException) as exc:
            print(f"[SonyQX10] 프레임 읽기 실패: {type(exc).__name__}: {exc}")
            self._opened = False
            return False, None

    @staticmethod
    def _read_one_frame(stream):
        """공통헤더(8B) + 페이로드헤더(128B) + JPEG + 패딩 구조에서 JPEG 한 장을 뽑음."""
        common_header = _read_exact(stream, 8)
        if common_header is None:
            return None

        payload_header = _read_exact(stream, 128)
        if payload_header is None:
            return None

        jpeg_size = struct.unpack(">I", b"\x00" + payload_header[4:7])[0]
        padding_size = payload_header[7]

        jpeg_data = _read_exact(stream, jpeg_size)
        if jpeg_data is None:
            return None
        if padding_size:
            _read_exact(stream, padding_size)

        if cv2 is None:
            return None
        arr = np.frombuffer(jpeg_data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return frame

    def release(self):
        self._opened = False
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None


def _read_exact(stream, n: int):
    """스트림에서 정확히 n바이트를 읽음 (부족하면 None)."""
    if n <= 0:
        return b""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
