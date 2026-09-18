"""
부팅 런처: 인터넷 연결되면 GitHub에서 최신 코드를 받아 업데이트한 뒤 main.py를
실행합니다. 오프라인이거나 업데이트 중 문제가 생기면 업데이트 전 상태(백업)로
안전하게 되돌려서 실행합니다.

동작 순서:
  1. 인터넷 연결 확인 (실제 소켓 연결 시도 - DNS 조회만으로는 신뢰 안 됨)
  2. 연결되면: 현재 코드 백업 -> git pull(이미 클론되어 있으면) 또는
     git clone(처음이면) -> 실패하면 백업으로 복구
  3. main.py 실행 (연결 안 됐으면 백업/현재 코드 그대로 바로 실행)

systemd로 자동 실행하려면 pfd.service의 ExecStart를 main.py 대신 이 파일로
바꾸면 됩니다.
"""

import os
import shutil
import socket
import subprocess
import sys

REPO_URL = "https://github.com/TBlueT/CN7_Raspberrypi_PFD.git"
PROJECT_DIR = os.path.expanduser("~/cn7")
BACKUP_DIR = os.path.expanduser("~/cn7_backup")

INTERNET_CHECK_HOST = "8.8.8.8"   # 구글 공개 DNS - 응답 여부만 확인, 조회 내용 없음
INTERNET_CHECK_PORT = 53
INTERNET_CHECK_TIMEOUT_SEC = 3

GIT_TIMEOUT_SEC = 120


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


def backup_current():
    if not os.path.isdir(PROJECT_DIR):
        return
    if os.path.isdir(BACKUP_DIR):
        shutil.rmtree(BACKUP_DIR)
    shutil.copytree(PROJECT_DIR, BACKUP_DIR)
    print(f"백업 완료: {PROJECT_DIR} -> {BACKUP_DIR}")


def restore_backup():
    if not os.path.isdir(BACKUP_DIR):
        print("백업이 없어서 복구를 건너뜁니다 (현재 상태 그대로 실행)")
        return
    if os.path.isdir(PROJECT_DIR):
        shutil.rmtree(PROJECT_DIR)
    shutil.copytree(BACKUP_DIR, PROJECT_DIR)
    print(f"백업으로 복구 완료: {BACKUP_DIR} -> {PROJECT_DIR}")


def update_from_github() -> bool:
    """이미 git 저장소면 pull, 아니면 clone. 성공 여부(bool) 반환."""
    try:
        is_git_repo = os.path.isdir(os.path.join(PROJECT_DIR, ".git"))

        if is_git_repo:
            result = subprocess.run(
                ["git", "-C", PROJECT_DIR, "pull", "--ff-only"],
                capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC,
            )
        else:
            if os.path.isdir(PROJECT_DIR):
                shutil.rmtree(PROJECT_DIR)
            result = subprocess.run(
                ["git", "clone", REPO_URL, PROJECT_DIR],
                capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC,
            )

        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
        return result.returncode == 0

    except subprocess.TimeoutExpired:
        print("git 명령 시간 초과", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"업데이트 중 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def run_main():
    main_path = os.path.join(PROJECT_DIR, "main.py")
    if not os.path.isfile(main_path):
        print(f"main.py를 찾을 수 없습니다: {main_path}", file=sys.stderr)
        return
    subprocess.run([sys.executable, main_path])


def main():
    print("인터넷 연결 확인 중...")
    if has_internet():
        print("인터넷 연결됨 - 최신 코드 업데이트 시도")
        backup_current()
        if update_from_github():
            print("업데이트 완료")
        else:
            print("업데이트 실패 - 백업으로 복구")
            restore_backup()
    else:
        print("인터넷 연결 안 됨 - 기존 코드로 바로 실행")

    run_main()


if __name__ == "__main__":
    main()
