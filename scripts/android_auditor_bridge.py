from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_PORT = 9888


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge for AndroidWeb auditor")
    parser.add_argument("--check", action="store_true", help="Run startup health check")
    parser.add_argument("--prompt", default="", help="Optional prompt text")
    parser.add_argument(
        "--tool-path",
        default=r"C:\Users\she__\Documents\Antigravity Projects\AndroidWeb\android-controller.exe",
        help="Path to android-controller executable",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Controller port")
    parser.add_argument("--timeout", type=float, default=12.0, help="Startup timeout in seconds")
    args = parser.parse_args()

    exe_path = Path(args.tool_path)
    if not exe_path.exists():
        print(f"android_auditor_missing: {exe_path}")
        return 2

    if not args.check:
        print(f"android_auditor_ready: {exe_path}")
        return 0

    return run_health_check(exe_path=exe_path, port=args.port, timeout_seconds=args.timeout)


def run_health_check(exe_path: Path, port: int, timeout_seconds: float) -> int:
    process = subprocess.Popen(
        [str(exe_path)],
        cwd=str(exe_path.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    started = False
    deadline = time.time() + max(1.0, timeout_seconds)
    try:
        while time.time() < deadline:
            if process.poll() is not None:
                break
            if _is_port_open(port):
                started = True
                break
            time.sleep(0.3)
    finally:
        _terminate_process(process)

    if started:
        print(f"android_auditor_ready: port {port}")
        return 0

    stderr_output = ""
    if process.stderr is not None:
        try:
            stderr_output = process.stderr.read().strip()
        except Exception:
            stderr_output = ""
    reason = stderr_output or "startup_timeout"
    print(f"android_auditor_unhealthy: {reason[:200]}")
    return 1


def _is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


if __name__ == "__main__":
    sys.exit(main())
