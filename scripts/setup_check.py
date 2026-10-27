"""Pre-work setup check. Run from the repo root:

    uv run scripts/setup_check.py

Verifies your environment end to end and prints your completion code.
"""

import json
import shutil
import subprocess
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
COMPLETION_CODE = "BERTHA-APPROVES-8123"

results: list[tuple[str, bool, str]] = []


def report(step: str, ok: bool, detail: str = "") -> bool:
    results.append((step, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {step}" + (f" — {detail}" if detail else ""))
    return ok


def get_json(path: str, timeout: float = 3.0):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return json.loads(resp.read())


def api_is_brewops() -> bool:
    try:
        stats = get_json("/api/stats")
        return "total_brews" in stats
    except Exception:
        return False


def check_python_and_uv() -> bool:
    version_ok = sys.version_info >= (3, 11)
    report(
        "Python 3.11+",
        version_ok,
        f"running {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )
    uv = shutil.which("uv")
    report("uv on PATH", uv is not None, uv or "install from https://docs.astral.sh/uv/")
    return version_ok and uv is not None


def check_seed() -> bool:
    proc = subprocess.run(
        [shutil.which("uv") or "uv", "run", "seed"],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        return report("uv run seed", False, (proc.stderr or proc.stdout).strip()[:300])
    conn = sqlite3.connect("brewops.db")
    brews = conn.execute("SELECT COUNT(*) FROM brew_events").fetchone()[0]
    taps = conn.execute("SELECT COUNT(*) FROM taps").fetchone()[0]
    conn.close()
    ok = brews > 1000 and taps == 4
    return report("uv run seed", ok, f"{brews} brew events, {taps} taps")


def check_api_smoke() -> bool:
    if api_is_brewops():
        return report("API smoke test", True, f"found BrewOps already running on port {PORT}")

    # is something else squatting on the port?
    try:
        urllib.request.urlopen(BASE + "/", timeout=2)
        return report(
            "API smoke test", False,
            f"port {PORT} is in use by something that isn't BrewOps. "
            f"Stop whatever is on port {PORT} and rerun this check.",
        )
    except urllib.error.URLError:
        pass  # port free — start the app ourselves

    proc = subprocess.Popen(
        [shutil.which("uv") or "uv", "run", "start"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if api_is_brewops():
                stats = get_json("/api/stats")
                return report(
                    "API smoke test", True,
                    f"started app, /api/stats reports {stats['total_brews']} brews",
                )
            if proc.poll() is not None:
                return report("API smoke test", False, "app exited before it became reachable")
            time.sleep(0.4)
        return report("API smoke test", False, "app did not become reachable within 30s")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def check_marker() -> bool:
    marker = Path("data") / ".setup-marker"
    if not marker.exists():
        return report(
            "marker file", False,
            "data/.setup-marker is missing — it should contain the name of the "
            "model that ran this check (this proves your agent can edit files)",
        )
    content = marker.read_text(encoding="utf-8").strip()
    return report("marker file", bool(content), f"contains: {content[:60]}" if content else "file is empty")


def main() -> int:
    print("BrewOps setup check")
    print("-------------------")
    if not Path("pyproject.toml").exists():
        print("[FAIL] run this from the repo root (pyproject.toml not found)")
        return 1

    ok = check_python_and_uv()
    ok = check_seed() and ok
    ok = check_api_smoke() and ok
    ok = check_marker() and ok

    print()
    if ok:
        print("All checks passed.")
        print(f"Your completion code: {COMPLETION_CODE}")
        return 0
    failed = [step for step, passed, _ in results if not passed]
    print(f"{len(failed)} check(s) failed: {', '.join(failed)}")
    print("Fix the above and rerun: uv run scripts/setup_check.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
