"""Lab A finish line: does each tap's health show its "specialty"
(its most-poured beer)? Run from the repo root:

    uv run scripts/lab_a_check.py

Green means: /api/taps/{id} now reports each tap's most-poured beer,
and it's the right one for every tap — so the query is real, not hardcoded.
Then open the dashboard and confirm the specialty shows on each tap card.
"""

import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"

# Fields the health payload already had before this lab — a specialty added by
# the participant is any *other* value that names a drink.
BASE_HEALTH_FIELDS = {
    "id", "name", "floor", "has_telemetry",
    "brew_count", "last_brew", "last_maintenance", "recent_errors",
}


def get_json(path: str, timeout: float = 3.0):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return json.loads(resp.read())


def api_reachable() -> bool:
    try:
        return "total_brews" in get_json("/api/stats")
    except Exception:
        return False


def start_app_if_needed() -> subprocess.Popen | None:
    if api_reachable():
        return None
    proc = subprocess.Popen(
        [shutil.which("uv") or "uv", "run", "start"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        if api_reachable():
            return proc
        if proc.poll() is not None:
            break
        time.sleep(0.4)
    proc.terminate()
    print("[FAIL] could not start the app — does `uv run start` work? Did you run `uv run seed`?")
    sys.exit(1)


def expected_specialties(db_path: str) -> dict[int, tuple[str, str]]:
    """Ground truth from the database: {tap_id: (drink_key, drink_label)}
    for every tap that has at least one pour. Ties break on drink name so
    the answer is deterministic."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT b.tap_id, dt.name AS key, dt.label AS label, COUNT(*) AS n
        FROM brew_events b
        JOIN drink_types dt ON dt.name = b.drink_type
        GROUP BY b.tap_id, dt.id
        """
    ).fetchall()
    conn.close()
    best: dict[int, tuple[int, str, str]] = {}
    for r in rows:
        mid = r["tap_id"]
        cur = best.get(mid)
        # higher count wins; on a tie the alphabetically-first drink key wins
        if cur is None or r["n"] > cur[0] or (r["n"] == cur[0] and r["key"] < cur[1]):
            best[mid] = (r["n"], r["key"], r["label"])
    return {mid: (key, label) for mid, (_n, key, label) in best.items()}


def string_leaves(value) -> list[str]:
    """Every string found anywhere in a JSON-ish structure."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in string_leaves(v)]
    if isinstance(value, list):
        return [s for v in value for s in string_leaves(v)]
    return []


def find_specialty(health: dict, key: str, label: str) -> str | None:
    """Look for the tap's specialty among fields the participant added.
    Returns 'label' or 'key' depending on which form they exposed, or None."""
    added = {k: v for k, v in health.items() if k not in BASE_HEALTH_FIELDS}
    leaves = [s.strip() for s in string_leaves(added)]
    if label in leaves:  # the display label, exactly ("NEIPA", "Barleywine")
        return "label"
    lowered = [s.lower() for s in leaves]
    if label.lower() in lowered or key.lower() in lowered:
        return "key"  # right drink, but the raw/lowercased key form
    return None


def main() -> int:
    if not Path("pyproject.toml").exists():
        print("[FAIL] run this from the repo root (pyproject.toml not found)")
        return 1
    if not Path("brewops.db").exists():
        print("[FAIL] brewops.db not found — run `uv run seed` first.")
        return 1

    expected = expected_specialties("brewops.db")
    proc = start_app_if_needed()
    try:
        showed_key_only = []
        for tap_id, (key, label) in sorted(expected.items()):
            try:
                health = get_json(f"/api/taps/{tap_id}")
            except urllib.error.HTTPError as e:
                print(f"[FAIL] GET /api/taps/{tap_id} failed ({e.code})")
                return 1
            form = find_specialty(health, key, label)
            name = health.get("name", f"tap {tap_id}")
            if form is None:
                print(f"[FAIL] {name}: no specialty in the health payload.")
                print(f"       Expected its most-poured beer, {label!r}, to appear in")
                print(f"       /api/taps/{tap_id}. Add it in get_tap_health,")
                print("       then render it on the tap card in the frontend.")
                return 1
            print(f"[OK] {name}: specialty = {label}")
            if form == "key":
                showed_key_only.append(name)

        print()
        if showed_key_only:
            print("[TIP] The specialty is correct, but you're exposing the raw key")
            print(f"      (e.g. 'neipa') rather than the display label ('NEIPA') for:")
            print(f"      {', '.join(showed_key_only)}. Consider showing the label people read.")
            print()
        print("[DONE] Lab A complete! Now open http://localhost:8123 and confirm each")
        print("       tap card shows its Specialty — that's the part the API can't")
        print("       check for you.")
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
