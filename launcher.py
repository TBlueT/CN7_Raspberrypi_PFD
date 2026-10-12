"""
부팅 런처: 인터넷 연결되면 GitHub에서 최신 코드를 받아 업데이트한 뒤 main.py를
실행합니다. 오프라인이거나 업데이트 중 문제가 생기면 업데이트 전 상태(백업)로
안전하게 되돌려서 실행합니다.

동작 순서:
  1. 인터넷 연결 확인 (실제 소켓 연결 시도 - DNS 조회만으로는 신뢰 안 됨)
  2. 연결되면: 현재 코드 백업 -> git pull(이미 클론되어 있으면) 또는
     git clone(처음이면) -> 실패하면 백업으로 복구
  3. main.py 실행 (연결 안 됐으면 백업/현재 코드 그대로 바로 실행)

화면 (B737 스타일 부팅 체크리스트):
  NETWORK / BACKUP / UPDATE / LAUNCH 단계별 상태를 800x480 전체 화면에 표시하고,
  끝나면 3초 카운트다운 후 화면을 닫고 main.py 를 실행합니다.

구조 (부팅이 화면 문제로 막히지 않게):
  launcher.py          (부모, Qt 사용 안 함)
    └ launcher.py --ui (자식: 화면 + 업데이트 수행, 끝나면 종료 -> 화면 장치 해제)
  자식이 비정상 종료되면(Qt 초기화 실패 등) 부모가 콘솔 출력 방식으로 업데이트를
  다시 수행한 뒤 main.py 를 실행합니다.

화면 환경변수(QT_QPA_PLATFORM 등)는 서비스 설정의 값을 그대로 이어받습니다.
systemd로 자동 실행하려면 서비스의 ExecStart를 main.py 대신 이 파일로 지정하면 됩니다.
"""

import os
import shutil
import socket
import subprocess
import sys
import time

REPO_URL = "https://github.com/TBlueT/CN7_Raspberrypi_PFD.git"
PROJECT_DIR = os.path.expanduser("~/cn7")
BACKUP_DIR = os.path.expanduser("~/cn7_backup")

INTERNET_CHECK_HOST = "8.8.8.8"   # 구글 공개 DNS - 응답 여부만 확인, 조회 내용 없음
INTERNET_CHECK_PORT = 53
INTERNET_CHECK_TIMEOUT_SEC = 3

GIT_TIMEOUT_SEC = 120
LAUNCH_COUNTDOWN_SEC = 3
UI_PROCESS_TIMEOUT_SEC = GIT_TIMEOUT_SEC + 180   # 화면 프로세스 전체 제한 (백업 복사 포함)

STEP_NAMES = ("NETWORK", "BACKUP", "UPDATE", "LAUNCH")


# =====================================================================
# 업데이트 작업 (화면/콘솔 공용). report(event, **kw) 로 진행 상황을 알림
#   report("step", name=, status=, level=, detail=None)
#       level: "active"(진행 중) / "ok" / "warn" / "skip"
#   report("footer", text=)              맨 아래 상세 로그 한 줄
#   report("banner", text=, level=)      결과 문구
# =====================================================================
def has_internet() -> bool:
    """실제 소켓 연결을 시도해서 인터넷 연결 여부를 확인.
    (hostname 조회 방식은 라즈베리파이 환경에 따라 오프라인에서도 127.0.1.1
    같은 값이 나올 수 있어서 신뢰할 수 없음 - 직접 연결 시도가 더 확실함)"""
    try:
        with socket.create_connection(
            (INTERNET_CHECK_HOST, INTERNET_CHECK_PORT),
            timeout=INTERNET_CHECK_TIMEOUT_SEC,
        ):
            return True
    except OSError:
        return False


def backup_current() -> bool:
    """백업할 프로젝트가 있으면 복사하고 True, 없으면 False."""
    if not os.path.isdir(PROJECT_DIR):
        return False
    if os.path.isdir(BACKUP_DIR):
        shutil.rmtree(BACKUP_DIR)
    shutil.copytree(PROJECT_DIR, BACKUP_DIR, symlinks=True)
    return True


def restore_backup() -> bool:
    if not os.path.isdir(BACKUP_DIR):
        return False
    if os.path.isdir(PROJECT_DIR):
        shutil.rmtree(PROJECT_DIR)
    shutil.copytree(BACKUP_DIR, PROJECT_DIR, symlinks=True)
    return True


def _git_head():
    try:
        r = subprocess.run(["git", "-C", PROJECT_DIR, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _git_out(*args):
    try:
        r = subprocess.run(["git", "-C", PROJECT_DIR, *args],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _last_line(*texts) -> str:
    for text in texts:
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if lines:
            return lines[-1]
    return ""


def _error_line(text) -> str:
    """git 오류 출력에서 원인 줄(fatal:/error:) 우선, 없으면 마지막 줄."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for ln in lines:
        if ln.lower().startswith(("fatal:", "error:")):
            return ln
    return lines[-1] if lines else ""


def update_from_github(report):
    """이미 git 저장소면 pull, 아니면 clone.
    반환: (성공 여부, 이전 HEAD, 오류 첫 줄)"""
    is_git_repo = os.path.isdir(os.path.join(PROJECT_DIR, ".git"))
    old_head = _git_head() if is_git_repo else None
    try:
        if is_git_repo:
            report("step", name="UPDATE", status="GIT PULL", level="active")
            cmd = ["git", "-C", PROJECT_DIR, "pull", "--ff-only"]
        else:
            report("step", name="UPDATE", status="GIT CLONE", level="active")
            if os.path.isdir(PROJECT_DIR):
                shutil.rmtree(PROJECT_DIR)
            cmd = ["git", "clone", REPO_URL, PROJECT_DIR]
        report("footer", text="git pull --ff-only" if is_git_repo else f"git clone {REPO_URL}")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC)
        report("footer", text=_last_line(result.stdout, result.stderr))
        if result.returncode != 0:
            return False, old_head, _error_line(result.stderr) or f"git 종료 코드 {result.returncode}"
        return True, old_head, ""
    except subprocess.TimeoutExpired:
        return False, old_head, "git 명령 시간 초과"
    except Exception as exc:
        return False, old_head, f"{type(exc).__name__}: {exc}"


def run_update(report):
    """네트워크 확인 -> 백업 -> 업데이트(실패 시 복구). LAUNCH 단계 직전까지."""
    report("step", name="NETWORK", status="CHECK", level="active")
    report("footer", text=f"{INTERNET_CHECK_HOST}:{INTERNET_CHECK_PORT} 연결 확인 중")
    if not has_internet():
        report("step", name="NETWORK", status="OFFLINE", level="warn")
        report("step", name="BACKUP", status="SKIP", level="skip")
        report("step", name="UPDATE", status="SKIP", level="skip")
        report("footer", text=f"{INTERNET_CHECK_HOST}:{INTERNET_CHECK_PORT} 연결 안 됨 - 기존 코드로 실행")
        report("banner", text="오프라인 - 기존 코드로 실행", level="warn")
        return
    report("step", name="NETWORK", status="ONLINE", level="ok")

    report("step", name="BACKUP", status="COPY", level="active")
    report("footer", text=f"{PROJECT_DIR} -> {BACKUP_DIR}")
    try:
        backed_up = backup_current()
        report("step", name="BACKUP", status="DONE" if backed_up else "NONE",
               level="ok" if backed_up else "skip")
    except Exception as exc:
        backed_up = False
        report("step", name="BACKUP", status="FAIL", level="warn",
               detail=f"{type(exc).__name__}: {exc}")

    ok, old_head, error = update_from_github(report)
    if ok:
        new_head = _git_head()
        if old_head and new_head == old_head:
            report("step", name="UPDATE", status="UP TO DATE", level="ok", detail=f"{new_head}")
        else:
            count = _git_out("rev-list", "--count", f"{old_head}..{new_head}") if old_head else ""
            subject = _git_out("log", "-1", "--pretty=%s")
            head_text = f"{old_head} → {new_head}" if old_head else f"{new_head}"
            count_text = f"  ({count} commits)" if count else ""
            report("step", name="UPDATE", status="UPDATED", level="ok",
                   detail=f"{head_text}{count_text}  {subject}")
        report("banner", text="PFD 시작", level="ok")
        return

    if backed_up and restore_backup():
        report("step", name="UPDATE", status="FAIL → RESTORED", level="warn", detail=error)
        report("footer", text=f"백업({BACKUP_DIR})으로 복구 완료")
        report("banner", text="업데이트 실패 - 이전 버전으로 실행", level="warn")
    else:
        report("step", name="UPDATE", status="FAIL", level="warn", detail=error)
        report("banner", text="업데이트 실패 - 현재 코드로 실행", level="warn")


def run_main():
    main_path = os.path.join(PROJECT_DIR, "main.py")
    if not os.path.isfile(main_path):
        print(f"main.py를 찾을 수 없습니다: {main_path}", file=sys.stderr)
        return
    subprocess.run([sys.executable, main_path], cwd=PROJECT_DIR)


# =====================================================================
# 콘솔 모드 (화면을 못 띄울 때)
# =====================================================================
def console_report(event, **kw):
    if event == "step":
        detail = f"  ({kw['detail']})" if kw.get("detail") else ""
        print(f"[{kw['name']}] {kw['status']}{detail}")
    elif event == "footer":
        if kw.get("text"):
            print(f"    {kw['text']}")
    elif event == "banner":
        print(f"==> {kw['text']}")


# =====================================================================
# 화면 모드 (자식 프로세스: launcher.py --ui)
# =====================================================================
def run_ui() -> int:
    from PyQt6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
    from PyQt6.QtGui import QColor, QFont, QPainter, QPen
    from PyQt6.QtWidgets import QApplication, QWidget

    colors = {
        "active": QColor("#00c8c8"), "ok": QColor("#2fd646"), "warn": QColor("#f0997b"),
        "skip": QColor("#666666"), "idle": QColor("#666666"),
        "white": QColor("#e8e8e8"), "gray": QColor("#666666"), "border": QColor("#4a4a4a"),
    }

    class Worker(QThread):
        event = pyqtSignal(str, dict)

        def run(self):
            try:
                run_update(lambda ev, **kw: self.event.emit(ev, kw))
            except Exception as exc:   # 예기치 못한 오류여도 main.py 실행은 진행
                self.event.emit("banner", {"text": "런처 오류 - 현재 코드로 실행", "level": "warn"})
                self.event.emit("footer", {"text": f"{type(exc).__name__}: {exc}"})

    class StartupScreen(QWidget):
        def __init__(self):
            super().__init__()
            self.setStyleSheet("background:#000000;")
            self.steps = {n: {"status": "-", "level": "idle", "detail": None,
                              "t0": time.monotonic()} for n in STEP_NAMES}
            self.footer = ""
            self.banner = None
            self.countdown_end = None
            self._f_title = QFont("sans-serif", 16, QFont.Weight.Bold)
            self._f_step = QFont("monospace", 14, QFont.Weight.Bold)
            self._f_detail = QFont("sans-serif", 9)
            self._f_banner = QFont("sans-serif", 18, QFont.Weight.Bold)
            self._f_footer = QFont("monospace", 9)
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(100)

        def on_event(self, ev, kw):
            if ev == "step":
                st = self.steps[kw["name"]]
                if kw["level"] == "active" and (st["level"] != "active" or st["status"] != kw["status"]):
                    st["t0"] = time.monotonic()
                st.update(status=kw["status"], level=kw["level"], detail=kw.get("detail"))
            elif ev == "footer":
                self.footer = kw.get("text") or self.footer
            elif ev == "banner":
                self.banner = (kw["text"], kw["level"])
            self.update()

        def start_countdown(self):
            self.countdown_end = time.monotonic() + LAUNCH_COUNTDOWN_SEC
            self.steps["LAUNCH"].update(status="START", level="active")

        def _tick(self):
            if self.countdown_end is not None and time.monotonic() >= self.countdown_end:
                self._timer.stop()
                QApplication.instance().quit()
                return
            self.update()

        def _status_text(self, name, st):
            text = st["status"]
            if name == "LAUNCH" and self.countdown_end is not None:
                remain = max(0, int(self.countdown_end - time.monotonic() + 0.999))
                return f"{text}  {remain}"
            if st["level"] == "active":
                s = int(time.monotonic() - st["t0"])
                return f"{text}  {s // 60:02d}:{s % 60:02d}"
            return text

        def paintEvent(self, event):
            w, h = self.width(), self.height()
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.fillRect(0, 0, w, h, QColor("#000000"))

            p.setFont(self._f_title)
            p.setPen(QPen(colors["white"]))
            p.drawText(QRectF(0, 40, w, 30), Qt.AlignmentFlag.AlignCenter, "CN7 PFD  STARTUP")
            p.setPen(QPen(colors["border"], 1))
            p.drawLine(QPointF(w / 2 - 250, 82), QPointF(w / 2 + 250, 82))

            left = w / 2 - 230
            y = 120
            for name in STEP_NAMES:
                st = self.steps[name]
                dim = st["level"] in ("idle", "skip")
                p.setFont(self._f_step)
                p.setPen(QPen(colors["gray"] if dim else colors["white"]))
                p.drawText(QRectF(left, y, 130, 30),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
                p.setPen(QPen(colors["gray"]))
                p.drawText(QRectF(left + 130, y, 160, 30),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "." * 14)
                p.setPen(QPen(colors[st["level"]]))
                p.drawText(QRectF(left + 290, y, w - left - 300, 30),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           self._status_text(name, st))
                if st["detail"]:
                    p.setFont(self._f_detail)
                    p.setPen(QPen(colors["gray"]))
                    p.drawText(QRectF(left + 20, y + 26, w - left - 40, 18),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               st["detail"])
                    y += 18
                y += 44

            if self.banner:
                text, level = self.banner
                p.setFont(self._f_banner)
                p.setPen(QPen(colors[level]))
                p.drawText(QRectF(0, h - 110, w, 34), Qt.AlignmentFlag.AlignCenter, text)

            p.setPen(QPen(colors["border"], 1))
            p.drawLine(QPointF(0, h - 40), QPointF(w, h - 40))
            p.setFont(self._f_footer)
            p.setPen(QPen(colors["gray"]))
            p.drawText(QRectF(12, h - 36, w - 24, 30),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.footer)
            p.end()

    app = QApplication(sys.argv)
    screen = StartupScreen()
    screen.setWindowTitle("CN7 PFD Startup")
    screen.resize(800, 480)
    screen.showFullScreen()

    worker = Worker()
    worker.event.connect(screen.on_event)
    worker.finished.connect(screen.start_countdown)
    worker.start()

    app.exec()
    worker.wait(2000)
    return 0


# =====================================================================
# 진입점
# =====================================================================
def main():
    if "--ui" in sys.argv:
        sys.exit(run_ui())

    # 화면 모드는 자식 프로세스로 실행. 끝나면 화면 장치가 확실히 해제된 상태에서
    # main.py 를 띄울 수 있고, Qt 가 화면 초기화에 실패해 죽어도 부모는 계속 진행함.
    ui_ok = False
    try:
        result = subprocess.run([sys.executable, os.path.abspath(__file__), "--ui"],
                                timeout=UI_PROCESS_TIMEOUT_SEC)
        ui_ok = result.returncode == 0
        if not ui_ok:
            print(f"런처 화면 비정상 종료(code {result.returncode}) - 콘솔 모드로 진행",
                  file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("런처 화면 시간 초과 - 콘솔 모드로 진행", file=sys.stderr)
    except Exception as exc:
        print(f"런처 화면 실행 실패: {type(exc).__name__}: {exc} - 콘솔 모드로 진행",
              file=sys.stderr)

    if not ui_ok:
        run_update(console_report)

    run_main()


if __name__ == "__main__":
    main()
