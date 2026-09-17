#!/usr/bin/env python3
"""Code View Auto-Updater and Versioning Engine.

Checks for updates against GitHub, displays a polished TUI during updates,
and rebuilds the runtime before moving ahead.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

GITHUB_REPO = "Rkj0123/code-view"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"
RAW_GITHUB_BASE = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"
DEFAULT_CACHE_TTL = 3600  # 1 hour
NETWORK_TIMEOUT = 2.5     # seconds
EXIT_CODE_UPDATED = 10


def get_root_dir() -> Path:
    """Return root repository directory."""
    return Path(__file__).resolve().parent.parent


def get_current_version(root_dir: Optional[Path] = None) -> str:
    """Read local VERSION file or fallback to 0.1.0."""
    root = root_dir or get_root_dir()
    version_file = root / "VERSION"
    if version_file.is_file():
        content = version_file.read_text(encoding="utf-8").strip()
        if content:
            return content
    return "0.1.0"


def parse_semver(version_str: str) -> Tuple[int, int, int, str]:
    """Parse version string like 'v1.2.3' or '0.1.0-beta' into comparable tuple."""
    cleaned = version_str.strip().lstrip("v")
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-(.+))?$", cleaned)
    if not match:
        return (0, 0, 0, cleaned)
    major = int(match.group(1) or 0)
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    extra = match.group(4) or ""
    return (major, minor, patch, extra)


def is_newer_version(candidate: str, current: str) -> bool:
    """Return True if candidate is strictly newer than current according to SemVer."""
    c_maj, c_min, c_pat, c_ext = parse_semver(candidate)
    curr_maj, curr_min, curr_pat, curr_ext = parse_semver(current)
    if (c_maj, c_min, c_pat) > (curr_maj, curr_min, curr_pat):
        return True
    if (c_maj, c_min, c_pat) == (curr_maj, curr_min, curr_pat):
        # Empty extra (release) is newer than pre-release with extra
        if curr_ext and not c_ext:
            return True
        if c_ext and curr_ext and c_ext > curr_ext:
            return True
    return False


@dataclass
class UpdateCheckResult:
    available: bool
    latest_version: str
    current_version: str
    release_url: Optional[str] = None
    release_notes: Optional[str] = None
    source: str = "network"


class CacheManager:
    """Manages update check caching to minimize network latency and rate limits."""

    def __init__(self, cache_file: Optional[Path] = None):
        self.cache_file = cache_file or (Path.home() / ".cache" / "code-view" / "update_check.json")

    def load(self) -> Optional[dict]:
        try:
            if self.cache_file.is_file():
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    def save(self, latest_version: str, release_url: Optional[str] = None):
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "last_check": time.time(),
                "latest_version": latest_version,
                "release_url": release_url or "",
            }
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception:
            pass


def fetch_latest_version(timeout: float = NETWORK_TIMEOUT) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Query GitHub Releases API, with fallbacks to Tags API and raw VERSION."""
    headers = {
        "User-Agent": f"code-view-updater/{get_current_version()}",
        "Accept": "application/vnd.github.v3+json",
    }

    # 1. Try Releases API
    try:
        req = urllib.request.Request(f"{GITHUB_API_BASE}/releases/latest", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                tag = data.get("tag_name", "").lstrip("v")
                url = data.get("html_url")
                body = data.get("body")
                if tag:
                    return tag, url, body
    except Exception:
        pass

    # 2. Try Tags API
    try:
        req = urllib.request.Request(f"{GITHUB_API_BASE}/tags", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list) and data:
                    tag = data[0].get("name", "").lstrip("v")
                    if tag:
                        return tag, f"https://github.com/{GITHUB_REPO}/releases/tag/v{tag}", None
    except Exception:
        pass

    # 3. Try raw VERSION file
    try:
        req = urllib.request.Request(f"{RAW_GITHUB_BASE}/VERSION", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                ver = resp.read().decode("utf-8").strip()
                if ver:
                    return ver, f"https://github.com/{GITHUB_REPO}", None
    except Exception:
        pass

    return None, None, None


def check_for_updates(force: bool = False) -> UpdateCheckResult:
    """Check if an update is available, using cache unless force=True."""
    current = get_current_version()
    cache_ttl = int(os.environ.get("CODE_VIEW_UPDATE_INTERVAL", DEFAULT_CACHE_TTL))

    cache = CacheManager()
    if not force and cache_ttl > 0:
        cached_data = cache.load()
        if cached_data:
            last_check = cached_data.get("last_check", 0)
            cached_ver = cached_data.get("latest_version", "")
            if (time.time() - last_check) < cache_ttl and cached_ver:
                is_newer = is_newer_version(cached_ver, current)
                return UpdateCheckResult(
                    available=is_newer,
                    latest_version=cached_ver,
                    current_version=current,
                    release_url=cached_data.get("release_url"),
                    source="cache",
                )

    latest, release_url, notes = fetch_latest_version()
    if latest:
        cache.save(latest, release_url)
        return UpdateCheckResult(
            available=is_newer_version(latest, current),
            latest_version=latest,
            current_version=current,
            release_url=release_url,
            release_notes=notes,
            source="network",
        )

    return UpdateCheckResult(
        available=False,
        latest_version=current,
        current_version=current,
        source="unavailable",
    )


class TerminalUI:
    """Renders a polished, modern Terminal UI with live spinner and status bars."""

    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, is_tty: Optional[bool] = None):
        self.is_tty = sys.stdout.isatty() if is_tty is None else is_tty
        self.use_color = self.is_tty and "NO_COLOR" not in os.environ

    def _c(self, code: str, text: str) -> str:
        if not self.use_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def cyan(self, text: str) -> str:
        return self._c("36", text)

    def bold(self, text: str) -> str:
        return self._c("1", text)

    def green(self, text: str) -> str:
        return self._c("32", text)

    def yellow(self, text: str) -> str:
        return self._c("33", text)

    def red(self, text: str) -> str:
        return self._c("31", text)

    def dim(self, text: str) -> str:
        return self._c("2", text)

    def show_banner(self, current: str, latest: str):
        """Render the update notification box."""
        title = " ▲ Code View Update Available "
        version_line = f"  v{current}  →  v{latest}  "
        info_line = "  Updating local runtime and dependencies...  "

        content_width = max(len(title), len(version_line), len(info_line), 48)
        border = "─" * (content_width + 2)

        if self.is_tty:
            sys.stdout.write("\n")
            sys.stdout.write(self.cyan(f"  ╭{border}╮\n"))
            sys.stdout.write(self.cyan("  │") + self.bold(self.yellow(f"{title:<{content_width + 2}}")) + self.cyan("│\n"))
            sys.stdout.write(self.cyan("  │") + self.bold(f"{version_line:<{content_width + 2}}") + self.cyan("│\n"))
            sys.stdout.write(self.cyan("  │") + self.dim(f"{info_line:<{content_width + 2}}") + self.cyan("│\n"))
            sys.stdout.write(self.cyan(f"  ╰{border}╯\n\n"))
            sys.stdout.flush()
        else:
            print(f"[Code View] Update available: v{current} -> v{latest}")
            print("[Code View] Starting automated update...")

    def run_step(self, step_idx: int, total_steps: int, title: str, task: Callable[[], bool]) -> bool:
        """Execute a step with a live spinner or sequential logging."""
        prefix = f"  [{step_idx}/{total_steps}] "
        if not self.is_tty:
            sys.stdout.write(f"{prefix}{title}...")
            sys.stdout.flush()
            ok = task()
            if ok:
                sys.stdout.write(" OK\n")
            else:
                sys.stdout.write(" FAILED\n")
            sys.stdout.flush()
            return ok

        # TTY mode with live spinner
        frame_idx = 0
        import threading
        result = [None]

        def target():
            try:
                result[0] = task()
            except Exception as e:
                result[0] = False

        thread = threading.Thread(target=target)
        thread.start()

        while thread.is_alive():
            frame = self.cyan(self.SPINNER_FRAMES[frame_idx % len(self.SPINNER_FRAMES)])
            sys.stdout.write(f"\r{prefix}{frame} {title}...")
            sys.stdout.flush()
            frame_idx += 1
            time.sleep(0.08)

        thread.join()
        success = bool(result[0])
        if success:
            sys.stdout.write(f"\r{prefix}{self.green('✓')} {title}\033[K\n")
        else:
            sys.stdout.write(f"\r{prefix}{self.red('✗')} {title} {self.red('(failed)')}\033[K\n")
        sys.stdout.flush()
        return success

    def show_success(self, latest: str):
        """Render update complete notice."""
        if self.is_tty:
            sys.stdout.write("\n" + self.green(f"  ✨ Successfully updated Code View to v{latest}!\n"))
            sys.stdout.write(self.cyan("  🚀 Moving ahead to launch Code View...\n\n"))
            sys.stdout.flush()
        else:
            print(f"[Code View] Successfully updated to v{latest}. Launching...")

    def show_failure(self, step_name: str, message: Optional[str] = None):
        """Render failure notice."""
        if self.is_tty:
            sys.stdout.write("\n" + self.red(f"  ⚠️  Update could not be completed during step: {step_name}\n"))
            if message:
                sys.stdout.write(self.dim(f"     Details: {message}\n"))
            sys.stdout.write(self.yellow("  Continuing with existing version...\n\n"))
            sys.stdout.flush()
        else:
            print(f"[Code View] Update failed at {step_name}: {message or 'unknown error'}")
            print("[Code View] Continuing with existing version...")


class UpdateRunner:
    """Executes the Git and build operations for the update."""

    def __init__(self, root_dir: Optional[Path] = None, dry_run: bool = False):
        self.root = root_dir or get_root_dir()
        self.dry_run = dry_run

    def check_git_clean(self) -> Tuple[bool, str]:
        """Verify git status is clean so user changes are never clobbered."""
        if not (self.root / ".git").exists():
            return True, "not a git repo"
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
            )
            dirty_files = [line for line in res.stdout.strip().splitlines() if line and not line.startswith("?? .codebase-memory") and not line.startswith("?? .playwright-cli")]
            if dirty_files:
                return False, f"{len(dirty_files)} uncommitted file(s) present"
            return True, ""
        except Exception as e:
            return False, str(e)

    def find_remote(self) -> str:
        """Find appropriate git remote (origin, upstream, or first remote)."""
        try:
            res = subprocess.run(["git", "remote"], cwd=self.root, capture_output=True, text=True, check=True)
            remotes = res.stdout.strip().splitlines()
            if "origin" in remotes:
                return "origin"
            if "upstream" in remotes:
                return "upstream"
            if remotes:
                return remotes[0]
        except Exception:
            pass
        return "origin"

    def fetch_and_checkout(self, target_version: str) -> bool:
        """Fetch remote tags and checkout the release tag or commit."""
        if self.dry_run:
            time.sleep(0.5)
            return True

        if not (self.root / ".git").exists():
            return True

        remote = self.find_remote()
        try:
            # 1. Fetch tags from remote
            subprocess.run(
                ["git", "fetch", "--tags", remote],
                cwd=self.root,
                capture_output=True,
                check=True,
                timeout=60,
            )

            # 2. Checkout tag if exists, otherwise pull tracking branch
            tag = f"v{target_version.lstrip('v')}"
            tag_check = subprocess.run(
                ["git", "rev-parse", "--verify", f"refs/tags/{tag}"],
                cwd=self.root,
                capture_output=True,
            )
            if tag_check.returncode == 0:
                subprocess.run(["git", "checkout", f"tags/{tag}"], cwd=self.root, capture_output=True, check=True)
            else:
                subprocess.run(["git", "pull", "--ff-only", remote, "main"], cwd=self.root, capture_output=True, check=True)
            return True
        except Exception:
            return False

    def build_web_canvas(self) -> bool:
        """Build web-canvas frontend assets."""
        if self.dry_run:
            time.sleep(0.5)
            return True

        web_dir = self.root / "services" / "web-canvas"
        if not web_dir.is_dir():
            return True

        try:
            if not (web_dir / "node_modules").is_dir():
                subprocess.run(["npm", "ci"], cwd=web_dir, capture_output=True, check=True, timeout=120)
            subprocess.run(["npm", "run", "build"], cwd=web_dir, capture_output=True, check=True, timeout=120)
            return True
        except Exception:
            return False

    def build_local_server(self) -> bool:
        """Configure and compile local C++ server."""
        if self.dry_run:
            time.sleep(0.5)
            return True

        host_dir = self.root / "services" / "local-server"
        build_dir = host_dir / "build"
        if not host_dir.is_dir():
            return True

        try:
            subprocess.run(
                ["cmake", "-S", str(host_dir), "-B", str(build_dir), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON"],
                cwd=self.root,
                capture_output=True,
                check=True,
                timeout=120,
            )
            subprocess.run(
                ["cmake", "--build", str(build_dir)],
                cwd=self.root,
                capture_output=True,
                check=True,
                timeout=180,
            )
            return True
        except Exception:
            return False


def run_update_sequence(latest_version: str, current_version: str, dry_run: bool = False) -> bool:
    """Run full interactive TUI update process."""
    tui = TerminalUI()
    runner = UpdateRunner(dry_run=dry_run)

    # Pre-check git cleanliness (skipped during dry-run/demo)
    if not dry_run:
        clean, reason = runner.check_git_clean()
        if not clean:
            tui.show_banner(current_version, latest_version)
            tui.show_failure("Git workspace check", f"Skipping auto-update: {reason}")
            return False

    tui.show_banner(current_version, latest_version)

    steps = [
        ("Checking repository environment", lambda: True),
        (f"Fetching release v{latest_version}", lambda: runner.fetch_and_checkout(latest_version)),
        ("Building web canvas interface", lambda: runner.build_web_canvas()),
        ("Compiling local server runtime", lambda: runner.build_local_server()),
    ]

    total = len(steps)
    for idx, (title, action) in enumerate(steps, 1):
        ok = tui.run_step(idx, total, title, action)
        if not ok:
            tui.show_failure(title)
            return False

    # Mark update as completed in cache
    CacheManager().save(latest_version)
    tui.show_success(latest_version)
    return True


def run_demo_tui():
    """Demonstrate the TUI visuals and animation."""
    print("Running Code View TUI Demo...")
    run_update_sequence("0.2.0", "0.1.0", dry_run=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Code View Updater")
    parser.add_argument("--check", action="store_true", help="Check for updates and print result")
    parser.add_argument("--update", action="store_true", help="Force update check and run update if available")
    parser.add_argument("--check-and-update", action="store_true", help="Auto-check using cache TTL and update if newer")
    parser.add_argument("--demo", action="store_true", help="Show demo TUI animation")
    parser.add_argument("--dry-run", action="store_true", help="Simulate update steps without changing files")
    parser.add_argument("--version", action="store_true", help="Print current version")

    args = parser.parse_args()

    if args.version:
        print(get_current_version())
        return 0

    if args.demo:
        run_demo_tui()
        return 0

    # Auto-update mode or explicit update mode
    force = args.update or ("--force" in sys.argv)
    result = check_for_updates(force=force)

    if args.check:
        if result.available:
            print(f"Update available: v{result.current_version} -> v{result.latest_version}")
            if result.release_url:
                print(f"Release: {result.release_url}")
            return 0
        print(f"Code View is up to date (v{result.current_version}).")
        return 0

    if result.available:
        success = run_update_sequence(result.latest_version, result.current_version, dry_run=args.dry_run)
        if success:
            return EXIT_CODE_UPDATED
        return 0

    # If user explicitly requested --update and already on latest
    if args.update:
        print(f"Code View is already up to date (v{result.current_version}).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
