#!/usr/bin/env python3
"""Code View auto-updater: checks GitHub, shows minimal TUI, rebuilds."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

REPO = "Rkj0123/code-view"
ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = Path.home() / ".cache" / "code-view" / "update_check.json"
EXIT_UPDATED = 10
SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def current_version() -> str:
    f = ROOT / "VERSION"
    return f.read_text("utf-8").strip() if f.is_file() else "0.1.0"


def parse_semver(v: str) -> Tuple[int, int, int, str]:
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-(.+))?$", v.strip())
    if not m:
        return (0, 0, 0, v)
    return (int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0), m.group(4) or "")


def is_newer(candidate: str, current: str) -> bool:
    c = parse_semver(candidate)
    cur = parse_semver(current)
    if c[:3] > cur[:3]:
        return True
    if c[:3] == cur[:3] and cur[3] and not c[3]:
        return True
    return False


def load_cache(path: Path = CACHE_FILE) -> Optional[dict]:
    try:
        if path.is_file():
            return json.loads(path.read_text("utf-8"))
    except Exception:
        pass
    return None


def save_cache(latest: str, url: str = "", path: Path = CACHE_FILE):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"time": time.time(), "latest": latest, "url": url}), "utf-8")
    except Exception:
        pass


def fetch_latest(timeout: float = 2.5) -> Tuple[Optional[str], Optional[str]]:
    headers = {"User-Agent": f"code-view/{current_version()}", "Accept": "application/vnd.github.v3+json"}
    for url, key in [
        (f"https://api.github.com/repos/{REPO}/releases/latest", "tag_name"),
        (f"https://api.github.com/repos/{REPO}/tags", "name"),
    ]:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                item = data[0] if isinstance(data, list) and data else data
                tag = str(item.get(key, "")).lstrip("v")
                if tag:
                    return tag, item.get("html_url", f"https://github.com/{REPO}")
        except Exception:
            continue
    return None, None


def check_updates(force: bool = False) -> Tuple[bool, str, str]:
    cur = current_version()
    ttl = int(os.environ.get("CODE_VIEW_UPDATE_INTERVAL", 3600))
    if not force and ttl > 0:
        c = load_cache()
        if c and (time.time() - c.get("time", 0)) < ttl and c.get("latest"):
            return is_newer(c["latest"], cur), c["latest"], cur

    latest, url = fetch_latest()
    if latest:
        save_cache(latest, url or "")
        return is_newer(latest, cur), latest, cur
    return False, cur, cur


# UI helpers
IS_TTY = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def color(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if IS_TTY else text


def show_banner(cur: str, latest: str):
    if not sys.stdout.isatty():
        print(f"[Code View] Update available: v{cur} -> v{latest}")
        return
    box = "─" * 46
    print(f"\n{color('36', '  ╭' + box + '╮')}")
    print(f"{color('36', '  │')} {color('1;33', '▲ Code View Update Available'):<55}{color('36', '│')}")
    print(f"{color('36', '  │')} {color('1', f'v{cur}  →  v{latest}'):<55}{color('36', '│')}")
    print(f"{color('36', '  ╰' + box + '╯')}\n")


def run_step(idx: int, total: int, title: str, task: Callable[[], bool]) -> bool:
    prefix = f"  [{idx}/{total}] "
    if not sys.stdout.isatty():
        sys.stdout.write(f"{prefix}{title}...")
        sys.stdout.flush()
        ok = task()
        print(" OK" if ok else " FAILED")
        return ok

    res = [False]
    t = threading.Thread(target=lambda: res.__setitem__(0, task()))
    t.start()
    f_idx = 0
    while t.is_alive():
        sys.stdout.write(f"\r{prefix}{color('36', SPINNER[f_idx % len(SPINNER)])} {title}...")
        sys.stdout.flush()
        f_idx += 1
        time.sleep(0.08)
    t.join()
    ok = res[0]
    mark = color("32", "✓") if ok else color("31", "✗ (failed)")
    sys.stdout.write(f"\r{prefix}{mark} {title}\033[K\n")
    sys.stdout.flush()
    return ok


# Update execution
def git_clean() -> bool:
    try:
        res = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True)
        return not any(l for l in res.stdout.splitlines() if not l.startswith("?? ."))
    except Exception:
        return True


def apply_update(latest: str, dry_run: bool = False) -> bool:
    if dry_run:
        time.sleep(0.5)
        return True
    try:
        subprocess.run(["git", "fetch", "--tags"], cwd=ROOT, capture_output=True, check=True, timeout=60)
        tag = f"v{latest.lstrip('v')}"
        if subprocess.run(["git", "rev-parse", "--verify", f"refs/tags/{tag}"], cwd=ROOT, capture_output=True).returncode == 0:
            subprocess.run(["git", "checkout", f"tags/{tag}"], cwd=ROOT, capture_output=True, check=True)
        else:
            subprocess.run(["git", "pull", "--ff-only"], cwd=ROOT, capture_output=True, check=True)
        return True
    except Exception:
        return False


def build_web(dry_run: bool = False) -> bool:
    if dry_run:
        time.sleep(0.5)
        return True
    web = ROOT / "services" / "web-canvas"
    if not web.is_dir():
        return True
    try:
        if not (web / "node_modules").is_dir():
            subprocess.run(["npm", "ci"], cwd=web, capture_output=True, check=True, timeout=120)
        subprocess.run(["npm", "run", "build"], cwd=web, capture_output=True, check=True, timeout=120)
        return True
    except Exception:
        return False


def build_server(dry_run: bool = False) -> bool:
    if dry_run:
        time.sleep(0.5)
        return True
    host = ROOT / "services" / "local-server"
    if not host.is_dir():
        return True
    try:
        subprocess.run(["cmake", "--build", str(host / "build")], cwd=ROOT, capture_output=True, check=True, timeout=180)
        return True
    except Exception:
        return False


def run_update(latest: str, cur: str, dry_run: bool = False) -> bool:
    if not dry_run and not git_clean():
        print(f"{color('33', '  ⚠️  Local uncommitted changes present. Skipping auto-update.')}\n")
        return False

    show_banner(cur, latest)
    steps = [
        ("Checking environment", lambda: True),
        (f"Fetching release v{latest}", lambda: apply_update(latest, dry_run)),
        ("Building web canvas", lambda: build_web(dry_run)),
        ("Compiling local server", lambda: build_server(dry_run)),
    ]
    for idx, (name, fn) in enumerate(steps, 1):
        if not run_step(idx, len(steps), name, fn):
            print(f"\n{color('31', '  ⚠️  Update interrupted. Launching existing version...')}\n")
            return False

    save_cache(latest)
    if sys.stdout.isatty():
        print(f"\n{color('32', f'  ✨ Successfully updated Code View to v{latest}!')}")
        print(f"{color('36', '  🚀 Launching Code View...')}\n")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="Code View Updater")
    p.add_argument("--check", action="store_true")
    p.add_argument("--update", action="store_true")
    p.add_argument("--check-and-update", action="store_true")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--version", action="store_true")
    args = p.parse_args()

    if args.version:
        print(current_version())
        return 0
    if args.demo:
        run_update("0.2.0", "0.1.0", dry_run=True)
        return 0

    avail, latest, cur = check_updates(force=args.update)
    if args.check:
        print(f"Update available: v{cur} -> v{latest}" if avail else f"Code View is up to date (v{cur}).")
        return 0

    if avail:
        return EXIT_UPDATED if run_update(latest, cur, dry_run=args.dry_run) else 0

    if args.update:
        print(f"Code View is already up to date (v{cur}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
