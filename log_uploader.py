"""
실행 로그 저장 + 온라인일 때 GitHub 이슈로 자동 업로드

1) 저장 (main.py 가 시작할 때 start_file_log() 호출)
   콘솔 출력(stdout/stderr: [CAN]/[IMU] 로그, 오류 메시지, 프로그램이 죽을 때의 Traceback)을
   그대로 화면에 내보내면서 LOG_DIR/run_YYYYMMDD_HHMMSS.log 에도 시각을 붙여 저장.
   LOG_DIR 은 코드 폴더(~/cn7) 밖이라 git pull 에 영향 없음.
   같은 줄 생략: LOG_DEDUP_WINDOW_SEC(60초) 안에 이미 기록한 줄이 또 나오면 파일에는 쓰지 않고
   횟수만 셌다가, 시간이 지나면 "... 반복 생략 N번 (시각~시각): 내용" 한 줄로 남김.
   번갈아 반복되는 줄(재연결 시도 → 실패 → 재시도 …)도 줄어듦. Traceback 은 줄이지 않음.
   (콘솔 출력은 그대로)

2) 업로드 (upload_pending_logs())
   인터넷이 될 때만, 아직 안 올린 로그들을 모아 "새 이슈 하나"로 올림.
   - 위: 실행별 요약 표 + 문제/상태 줄([CAN]/[IMU]/오류/Traceback, 같은 줄 반복은 접음)
   - 아래: 실행별 마지막 줄들 (접힌 상세)
   올린 로그는 LOG_DIR/sent/ 로 옮기고, 오래된 것은 LOG_KEEP_SENT 개만 남김.
   호출하는 곳: launcher.py (부팅 시 LOGS 단계), main.py (실행 중 LOG_UPLOAD_RETRY_MIN 분마다)

토큰: GitHub fine-grained token (이 저장소만, 권한 Issues: Read and write)을
  ~/.config/cn7/github_token 파일에 넣거나 환경변수 CN7_GITHUB_TOKEN 으로 지정.
  저장소에 올라가면 안 되므로 코드 폴더 밖에 둠. 없으면 업로드만 건너뜀.

표준 라이브러리만 사용 (런처가 Qt/설정 오류와 무관하게 쓸 수 있게).
config.py 를 읽을 수 없으면 아래 기본값 사용.
"""

import datetime
import json
import os
import re
import shutil
import socket
import atexit
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

_DEFAULTS = {
    "LOG_DIR": "~/cn7_logs",
    "LOG_MAX_FILE_MB": 5,
    "LOG_KEEP_SENT": 30,
    "LOG_UPLOAD_ENABLED": True,
    "LOG_UPLOAD_REPO": "TBlueT/CN7_Raspberrypi_PFD",
    "LOG_UPLOAD_TOKEN_FILE": "~/.config/cn7/github_token",
    "LOG_UPLOAD_API": "https://api.github.com",
    "LOG_UPLOAD_RETRY_MIN": 10,
    "LOG_DEDUP_WINDOW_SEC": 60,
}
_BODY_LIMIT = 60000        # GitHub 이슈 본문 최대 65536자 - 여유 둠
_TAIL_LINES = 200
_IMPORTANT = re.compile(r"\[CAN\]|\[IMU\]|\[LOG\]|Traceback|Error|Exception|오류|실패|미지원|끊김")
_STAMP = re.compile(r"^\d\d:\d\d:\d\d ")


def _cfg(name):
    try:
        import config
        value = getattr(config, name)
    except Exception:
        value = _DEFAULTS[name]
    return os.path.expanduser(value) if isinstance(value, str) and value.startswith("~") else value


def log_dir() -> str:
    return _cfg("LOG_DIR")


def has_internet(timeout=3.0) -> bool:
    try:
        with socket.create_connection(("8.8.8.8", 53), timeout=timeout):
            return True
    except OSError:
        return False


# =====================================================================
# 1) 로그 파일 저장
# =====================================================================
class _FileLog:
    """로그 파일 기록: 줄마다 시각, 같은 줄 반복 생략, 크기 제한. stdout/stderr 가 공유."""

    def __init__(self, f, limit_bytes, dedup_sec, clock=time.time):
        self._f = f
        self._limit = limit_bytes
        self._dedup = dedup_sec
        self._clock = clock
        self._lock = threading.Lock()
        self._bytes = 0
        self._full = False
        self._in_traceback = False
        self._recent = {}   # 줄 내용 -> {"written": 마지막 기록 시각, "n": 생략 수, "first", "last"}

    def _stamp(self, t):
        return datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")

    def _emit(self, text):
        if self._full:
            return
        try:
            self._f.write(text)
            self._f.flush()                 # 전원이 갑자기 꺼져도 남도록 매번 기록
            self._bytes += len(text.encode("utf-8", "replace"))
            if self._bytes > self._limit:
                self._f.write("... (로그 크기 제한 도달 - 이후 기록 안 함)\n")
                self._f.flush()
                self._full = True
        except Exception:
            pass

    def _flush_suppressed(self, now, force=False):
        """생략 기간이 끝난 줄들의 반복 횟수를 한 줄씩 기록"""
        for text, r in list(self._recent.items()):
            if r["n"] and (force or now - r["written"] >= self._dedup):
                self._emit(f"{self._stamp(now)} ... 반복 생략 {r['n']}번 "
                           f"({self._stamp(r['first'])}~{self._stamp(r['last'])}): {text[:120]}\n")
                r["n"] = 0
            if not r["n"] and now - r["written"] >= self._dedup:
                del self._recent[text]

    def write_line(self, line):
        with self._lock:
            now = self._clock()
            self._flush_suppressed(now)
            body = line.rstrip()
            # Traceback 블록은 그대로 (구조가 깨지면 안 됨): "Traceback" ~ 들여쓰기 없는 예외 줄
            if body.startswith("Traceback"):
                self._in_traceback = True
            elif self._in_traceback and body and not body[0].isspace():
                self._in_traceback = False
                self._emit(f"{self._stamp(now)} {line}\n")
                return
            if self._dedup > 0 and body and not self._in_traceback:
                r = self._recent.get(body)
                if r is not None and now - r["written"] < self._dedup:
                    if not r["n"]:
                        r["first"] = now
                    r["n"] += 1
                    r["last"] = now
                    return
                self._recent[body] = {"written": now, "n": 0, "first": now, "last": now}
            self._emit(f"{self._stamp(now)} {line}\n")

    def close(self):
        with self._lock:
            self._flush_suppressed(self._clock(), force=True)


class _Tee:
    """원래 스트림에는 그대로 쓰고, 완성된 줄만 파일 로그로 넘김"""

    def __init__(self, stream, filelog):
        self._stream = stream
        self._filelog = filelog
        self._buf = ""

    def write(self, text):
        try:
            self._stream.write(text)
        except Exception:
            pass
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._filelog.write_line(line)
        return len(text)

    def flush_partial(self):
        if self._buf:
            self._filelog.write_line(self._buf)
            self._buf = ""

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


_active_log = None


def start_file_log(project_dir=None):
    """stdout/stderr 를 로그 파일에도 기록. 만든 파일 경로 반환 (실패하면 None - 앱은 계속)."""
    global _active_log
    try:
        directory = log_dir()
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, datetime.datetime.now().strftime("run_%Y%m%d_%H%M%S.log"))
        f = open(path, "a", encoding="utf-8")
    except Exception as exc:
        print(f"[LOG] 로그 파일을 만들 수 없음: {exc}", file=sys.stderr)
        return None
    filelog = _FileLog(f, int(_cfg("LOG_MAX_FILE_MB") * 1024 * 1024), float(_cfg("LOG_DEDUP_WINDOW_SEC") or 0))
    out, err = _Tee(sys.stdout, filelog), _Tee(sys.stderr, filelog)
    sys.stdout, sys.stderr = out, err

    def _at_exit():                         # 끝날 때 남은 반복 횟수/줄 기록 (죽을 때도 실행됨)
        out.flush_partial()
        err.flush_partial()
        filelog.close()
    atexit.register(_at_exit)
    _active_log = path
    commit = _git_head(project_dir or os.path.dirname(os.path.abspath(__file__)))
    print(f"[LOG] 실행 시작 {datetime.datetime.now():%Y-%m-%d %H:%M:%S}  커밋 {commit or '?'}  로그 {path}",
          file=sys.stderr)
    return path


def _git_head(project_dir):
    try:
        r = subprocess.run(["git", "-C", project_dir, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


# =====================================================================
# 2) 업로드
# =====================================================================
def pending_logs():
    """아직 안 올린 로그 파일들 (오래된 순, 지금 쓰고 있는 파일 제외)"""
    directory = log_dir()
    if not os.path.isdir(directory):
        return []
    files = sorted(os.path.join(directory, n) for n in os.listdir(directory)
                   if n.startswith("run_") and n.endswith(".log"))
    active = os.path.abspath(_active_log) if _active_log else None
    return [f for f in files if os.path.abspath(f) != active]


def _read_token():
    token = os.environ.get("CN7_GITHUB_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(_cfg("LOG_UPLOAD_TOKEN_FILE"), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _collapse_repeats(lines):
    """시각만 다른 같은 줄이 연속되면 한 줄 + (N번 반복) 으로"""
    out, prev, count = [], None, 0
    for line in lines:
        key = _STAMP.sub("", line)
        if key == prev:
            count += 1
            continue
        if count > 1:
            out.append(f"         ... 위 줄 {count}번 반복")
        out.append(line)
        prev, count = key, 1
    if count > 1:
        out.append(f"         ... 위 줄 {count}번 반복")
    return out


def summarize_log(path):
    """로그 파일 -> (요약 dict, 문제/상태 줄 목록, 마지막 줄들)"""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = [l.rstrip("\n") for l in f]
    important, in_trace = [], False
    for line in lines:
        body = _STAMP.sub("", line)
        if body.startswith("Traceback"):
            in_trace = True
        elif in_trace and not (body.startswith(" ") or body.startswith("\t")):
            important.append(line)       # Traceback 마지막 줄 (예외 내용)
            in_trace = False
            continue
        if in_trace or _IMPORTANT.search(body):
            important.append(line)
    stamps = [m.group(0).strip() for m in (_STAMP.match(l) for l in lines) if m]
    name = os.path.basename(path)
    try:
        start = datetime.datetime.strptime(name[4:19], "%Y%m%d_%H%M%S")
    except ValueError:
        start = None
    duration = None
    if start and stamps:
        end = datetime.datetime.combine(start.date(), datetime.datetime.strptime(stamps[-1], "%H:%M:%S").time())
        if end < start:
            end += datetime.timedelta(days=1)
        duration = end - start
    info = {"name": name, "start": start, "duration": duration, "lines": len(lines),
            "problems": sum(1 for l in important if re.search(r"Traceback|Error|Exception|오류|실패|미지원|끊김", l))}
    return info, _collapse_repeats(important), lines[-_TAIL_LINES:]


def build_issue(paths, commit=None):
    """로그 파일들 -> (제목, 본문)"""
    summaries = [summarize_log(p) for p in paths]
    now = datetime.datetime.now()
    total_problems = sum(s[0]["problems"] for s in summaries)
    title = f"[실차 로그] {now:%Y-%m-%d %H:%M} 업로드 · 실행 {len(paths)}회 · 문제 {total_problems}줄"
    head = [f"CN7 PFD 가 온라인 연결 시 자동으로 올린 로그입니다. (업로드 커밋 {commit or '?'})", "",
            "| 실행 | 시작 | 길이 | 줄 수 | 문제 줄 |", "|---|---|---|---|---|"]
    for info, _, _ in summaries:
        start = f"{info['start']:%m-%d %H:%M}" if info["start"] else "-"
        dur = f"{int(info['duration'].total_seconds() // 60)}분" if info["duration"] else "-"
        head.append(f"| `{info['name']}` | {start} | {dur} | {info['lines']} | {info['problems']} |")
    head.append("")
    budget = _BODY_LIMIT - len("\n".join(head)) - 200
    per_file = max(2000, budget // max(1, len(summaries)))
    sections = []
    for info, important, tail in summaries:
        imp = "\n".join(important) or "(없음)"
        tail_text = "\n".join(tail)
        imp_budget = int(per_file * 0.6)
        if len(imp) > imp_budget:
            half = imp_budget // 2
            imp = imp[:half] + "\n...(중간 생략)...\n" + imp[-half:]
        tail_budget = per_file - len(imp) - 300
        if len(tail_text) > tail_budget:
            tail_text = "...(앞부분 생략)...\n" + tail_text[-max(0, tail_budget):]
        sections.append("\n".join([
            f"### {info['name']}", "", "**문제 / 상태 줄**", "```text", imp, "```", "",
            f"<details><summary>마지막 {min(_TAIL_LINES, info['lines'])}줄</summary>", "", "```text",
            tail_text, "```", "</details>", ""]))
    body = "\n".join(head + sections)
    if len(body) > _BODY_LIMIT:
        body = body[:_BODY_LIMIT - 40] + "\n```\n...(본문 길이 제한으로 잘림)"
    return title, body


def _post_issue(title, body, token):
    url = f"{_cfg('LOG_UPLOAD_API').rstrip('/')}/repos/{_cfg('LOG_UPLOAD_REPO')}/issues"
    req = urllib.request.Request(
        url, data=json.dumps({"title": title, "body": body}).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "cn7-pfd-log-uploader",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("number"), data.get("html_url")


def _mark_sent(paths):
    sent_dir = os.path.join(log_dir(), "sent")
    os.makedirs(sent_dir, exist_ok=True)
    for p in paths:
        shutil.move(p, os.path.join(sent_dir, os.path.basename(p)))
    keep = int(_cfg("LOG_KEEP_SENT"))
    old = sorted(os.listdir(sent_dir))
    for name in old[:-keep] if keep > 0 else old:
        try:
            os.remove(os.path.join(sent_dir, name))
        except OSError:
            pass


_upload_lock = threading.Lock()


def upload_pending_logs(check_internet=True, commit=None):
    """안 올린 로그를 새 이슈 하나로 올림. 결과 (상태, 설명) 반환:
    "uploaded"(이슈 번호/주소) / "none"(올릴 로그 없음) / "offline" / "no_token" / "disabled" / "failed"(이유)"""
    if not _cfg("LOG_UPLOAD_ENABLED"):
        return "disabled", "LOG_UPLOAD_ENABLED = False"
    if not _upload_lock.acquire(blocking=False):
        return "none", "다른 업로드 진행 중"
    try:
        paths = pending_logs()
        # 내용이 없는(시작 줄만 있는) 로그는 올리지 않고 정리
        empty = [p for p in paths if os.path.getsize(p) < 200]
        paths = [p for p in paths if p not in empty]
        if empty:
            _mark_sent(empty)
        if not paths:
            return "none", "새 로그 없음"
        token = _read_token()
        if not token:
            return "no_token", f"토큰 없음 ({_cfg('LOG_UPLOAD_TOKEN_FILE')} 또는 CN7_GITHUB_TOKEN)"
        if check_internet and not has_internet():
            return "offline", "인터넷 연결 안 됨"
        try:
            title, body = build_issue(paths, commit)
            number, url = _post_issue(title, body, token)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:150]
            return "failed", f"HTTP {exc.code}: {detail}"
        except Exception as exc:
            return "failed", f"{type(exc).__name__}: {exc}"
        _mark_sent(paths)
        return "uploaded", f"로그 {len(paths)}개 -> 이슈 #{number} {url}"
    finally:
        _upload_lock.release()


def start_background_uploader():
    """실행 중 LOG_UPLOAD_RETRY_MIN 분마다: 인터넷이 되고 안 올린 로그가 있으면 업로드.
    (부팅 때 오프라인이었다가 나중에 핫스팟 등으로 연결된 경우용, 지금 실행 로그는 제외)"""
    interval = float(_cfg("LOG_UPLOAD_RETRY_MIN")) * 60
    if not _cfg("LOG_UPLOAD_ENABLED") or interval <= 0:
        return None
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                if not pending_logs():
                    continue
                status, detail = upload_pending_logs()
            except Exception as exc:     # 업로드 문제로 스레드가 죽지 않게
                status, detail = "failed", f"{type(exc).__name__}: {exc}"
            if status in ("uploaded", "failed", "no_token"):
                print(f"[LOG] 로그 업로드 {status}: {detail}", file=sys.stderr)

    threading.Thread(target=loop, name="log-uploader", daemon=True).start()
    return stop
