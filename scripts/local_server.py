from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = ROOT / "public"
STATUS_PATH = ROOT / "data" / "local_refresh_status.json"
LOCK_PATH = ROOT / ".local_refresh.lock"
DEFAULT_DAILY_TIME = os.environ.get("MACROSCOPE_DAILY_REFRESH_TIME", "19:20")

STATE_LOCK = threading.Lock()
ACTIVE_THREAD: threading.Thread | None = None
STATE: dict[str, Any] = {
    "local_refresh_api": True,
    "state": "idle",
    "running": False,
    "progress": 0,
    "step": "等待刷新",
    "message": "可点击“刷新最新数据”立即更新",
    "started_at": None,
    "finished_at": None,
    "next_scheduled_at": None,
    "reason": None,
    "failed_steps": [],
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_persisted_state() -> dict[str, Any] | None:
    try:
        payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_persisted_state(payload: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, STATUS_PATH)


def update_state(**changes: Any) -> dict[str, Any]:
    with STATE_LOCK:
        STATE.update(changes)
        snapshot = dict(STATE)
    try:
        write_persisted_state(snapshot)
    except OSError as error:
        print(f"[本地刷新] 无法写入状态文件：{error}", flush=True)
    return snapshot


def status_snapshot() -> dict[str, Any]:
    with STATE_LOCK:
        memory_state = dict(STATE)
    disk_state = read_persisted_state()
    if not disk_state:
        return memory_state
    if memory_state.get("running"):
        return memory_state
    disk_marker = str(disk_state.get("started_at") or disk_state.get("finished_at") or "")
    memory_marker = str(memory_state.get("started_at") or memory_state.get("finished_at") or "")
    return disk_state if disk_marker >= memory_marker else memory_state


class RefreshLock:
    def __init__(self) -> None:
        self.handle: Any = None

    def acquire(self) -> bool:
        LOCK_PATH.touch(exist_ok=True)
        self.handle = LOCK_PATH.open("r+b")
        if LOCK_PATH.stat().st_size == 0:
            self.handle.write(b"\0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def run_step(label: str, arguments: list[str], timeout_seconds: int) -> tuple[bool, str]:
    print(f"\n[本地刷新] {label}", flush=True)
    command = [sys.executable, *arguments]
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    configure_ascii_ca_bundle(environment)
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"{label}超时"
    except OSError as error:
        return False, f"{label}无法启动：{error}"
    if result.returncode != 0:
        return False, f"{label}失败（退出码 {result.returncode}）"
    return True, f"{label}完成"


def configure_ascii_ca_bundle(environment: dict[str, str]) -> None:
    """Keep curl-based providers working when the project path contains Chinese text."""
    source = Path(sys.prefix) / "Lib" / "site-packages" / "certifi" / "cacert.pem"
    if not source.exists():
        return
    base = Path(
        environment.get("LOCALAPPDATA")
        or environment.get("TEMP")
        or environment.get("TMP")
        or str(ROOT)
    )
    destination_dir = base / "MacroScope"
    destination = destination_dir / "cacert.pem"
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
        if (
            not destination.exists()
            or destination.stat().st_size != source.stat().st_size
            or destination.stat().st_mtime < source.stat().st_mtime
        ):
            shutil.copyfile(source, destination)
    except OSError as error:
        print(f"[本地刷新] 无法准备证书兼容文件：{error}", flush=True)
        return
    for key in ("CURL_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        environment[key] = str(destination)


def run_refresh(reason: str) -> bool:
    refresh_lock = RefreshLock()
    if not refresh_lock.acquire():
        print("[本地刷新] 另一个刷新任务正在运行，本次不重复启动。", flush=True)
        return True

    steps = [
        ("检查数据文件", ["scripts/ensure_data_files.py"], 5 * 60, True),
        ("更新宏观与市场数据", ["scripts/update_data.py", "--mode", "all"], 45 * 60, False),
        ("更新市场资讯", ["scripts/update_messages.py"], 20 * 60, False),
        ("更新A股日频指标", ["scripts/update_ashare_daily.py", "--days", "95"], 60 * 60, False),
        ("重算大盘跟踪模型", ["scripts/update_market_tracking.py"], 15 * 60, False),
        ("重建本地网页", ["scripts/build_site.py"], 15 * 60, True),
        ("校验更新结果", ["scripts/validate_project.py"], 10 * 60, True),
    ]
    failed_steps: list[str] = []
    required_failure = False
    started_at = now_iso()
    update_state(
        state="running",
        running=True,
        progress=1,
        step="准备更新",
        message="正在准备本地数据刷新…",
        started_at=started_at,
        finished_at=None,
        reason=reason,
        failed_steps=[],
    )

    try:
        for index, (label, arguments, timeout_seconds, required) in enumerate(steps, start=1):
            progress = max(2, int((index - 1) / len(steps) * 100))
            update_state(
                state="running",
                running=True,
                progress=progress,
                step=label,
                message=f"正在{label}…",
            )
            ok, detail = run_step(label, arguments, timeout_seconds)
            if not ok:
                failed_steps.append(detail)
                print(f"[本地刷新] {detail}", flush=True)
                if required:
                    required_failure = True
                    break

        finished_at = now_iso()
        if required_failure:
            update_state(
                state="error",
                running=False,
                progress=100,
                step="更新未完成",
                message="网页重建或校验失败，请查看本窗口中的错误信息",
                finished_at=finished_at,
                failed_steps=failed_steps,
            )
            return False
        if failed_steps:
            update_state(
                state="partial",
                running=False,
                progress=100,
                step="刷新完成",
                message="部分公开数据源暂不可用，已用缓存数据完成网页刷新",
                finished_at=finished_at,
                failed_steps=failed_steps,
            )
            return True
        update_state(
            state="success",
            running=False,
            progress=100,
            step="刷新完成",
            message="最新数据已抓取并写入本地网页",
            finished_at=finished_at,
            failed_steps=[],
        )
        return True
    finally:
        refresh_lock.release()


def start_refresh(reason: str) -> bool:
    global ACTIVE_THREAD
    with STATE_LOCK:
        if ACTIVE_THREAD is not None and ACTIVE_THREAD.is_alive():
            return False
        ACTIVE_THREAD = threading.Thread(
            target=run_refresh,
            args=(reason,),
            name=f"macroscope-refresh-{reason}",
            daemon=True,
        )
        ACTIVE_THREAD.start()
        return True


def parse_daily_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as error:
        raise argparse.ArgumentTypeError("时间必须是 HH:MM，例如 19:20") from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise argparse.ArgumentTypeError("时间必须在 00:00 到 23:59 之间")
    return hour, minute


def next_daily_run(hour: int, minute: int) -> datetime:
    now = datetime.now().astimezone()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def scheduler_loop(hour: int, minute: int, stop_event: threading.Event) -> None:
    target = next_daily_run(hour, minute)
    update_state(next_scheduled_at=target.isoformat(timespec="seconds"))
    while not stop_event.is_set():
        remaining = (target - datetime.now().astimezone()).total_seconds()
        if remaining > 0:
            stop_event.wait(min(remaining, 60))
            continue
        start_refresh("daily")
        target += timedelta(days=1)
        update_state(next_scheduled_at=target.isoformat(timespec="seconds"))


class LocalRequestHandler(SimpleHTTPRequestHandler):
    server_version = "MacroScopeLocal/1.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/refresh-status":
            self.send_json(status_snapshot())
            return
        if path == "/api/health":
            self.send_json({"ok": True, "local_refresh_api": True})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/refresh":
            self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        started = start_refresh("manual")
        payload = status_snapshot()
        payload["accepted"] = started
        self.send_json(payload, HTTPStatus.ACCEPTED)

    def end_headers(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html", "/site-meta.json"}:
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, format_text: str, *args: Any) -> None:
        print(f"[本地网站] {self.address_string()} - {format_text % args}", flush=True)


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    parser = argparse.ArgumentParser(description="MacroScope 本地数据刷新与网站服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--skip-startup-refresh", action="store_true")
    parser.add_argument("--refresh-only", action="store_true")
    parser.add_argument("--daily-time", default=DEFAULT_DAILY_TIME)
    args = parser.parse_args()

    if args.refresh_only:
        return 0 if run_refresh("scheduled") else 1

    persisted = read_persisted_state()
    if persisted:
        with STATE_LOCK:
            STATE.update(persisted)
            STATE["local_refresh_api"] = True

    hour, minute = parse_daily_time(args.daily_time)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    stop_event = threading.Event()
    scheduler = threading.Thread(
        target=scheduler_loop,
        args=(hour, minute, stop_event),
        name="macroscope-daily-scheduler",
        daemon=True,
    )
    scheduler.start()

    server = LocalHTTPServer((args.host, args.port), LocalRequestHandler)
    local_url = f"http://{args.host}:{args.port}/"
    print(f"\nMacroScope 本地网站：{local_url}", flush=True)
    print(f"每日自动刷新时间：{hour:02d}:{minute:02d}（本机时区）", flush=True)
    print("关闭本窗口即可停止本地服务；Windows 每日任务不受影响。\n", flush=True)

    if args.open_browser:
        threading.Timer(0.7, webbrowser.open, args=(local_url,)).start()
    if not args.skip_startup_refresh:
        start_refresh("startup")

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n正在关闭本地网站…", flush=True)
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
