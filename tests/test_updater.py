#!/usr/bin/env python3
"""Runnable self-check test suite for Code View updater and versioning logic.

Follows the Ponytail standard: zero test frameworks, zero fixtures,
pure assert-based self-contained verification.
"""

import sys
import tempfile
import time
from pathlib import Path

# Add script directory to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "script"))

from updater import (
    CacheManager,
    TerminalUI,
    get_current_version,
    is_newer_version,
    parse_semver,
    run_update_sequence,
)


def test_parse_semver():
    assert parse_semver("0.1.0") == (0, 1, 0, "")
    assert parse_semver("v0.1.0") == (0, 1, 0, "")
    assert parse_semver("v1.2.3") == (1, 2, 3, "")
    assert parse_semver("2.0.0-rc1") == (2, 0, 0, "rc1")
    assert parse_semver("v3.14") == (3, 14, 0, "")
    assert parse_semver("invalid") == (0, 0, 0, "invalid")
    print("✓ test_parse_semver passed")


def test_is_newer_version():
    # Newer
    assert is_newer_version("0.2.0", "0.1.0") is True
    assert is_newer_version("0.1.1", "0.1.0") is True
    assert is_newer_version("1.0.0", "0.9.9") is True
    assert is_newer_version("v1.0.1", "v1.0.0") is True
    assert is_newer_version("1.0.0", "1.0.0-rc1") is True

    # Same or Older
    assert is_newer_version("0.1.0", "0.1.0") is False
    assert is_newer_version("0.0.9", "0.1.0") is False
    assert is_newer_version("0.1.0", "0.2.0") is False
    assert is_newer_version("v1.0.0", "v1.0.0") is False
    assert is_newer_version("1.0.0-rc1", "1.0.0") is False
    print("✓ test_is_newer_version passed")


def test_current_version_read():
    ver = get_current_version(root_dir)
    assert ver == "0.1.0", f"Expected 0.1.0, got {ver}"
    print("✓ test_current_version_read passed")


def test_cache_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "cache.json"
        cache = CacheManager(cache_path)

        # Initially empty
        assert cache.load() is None

        # Save entry
        cache.save("0.2.0", "https://github.com/Rkj0123/code-view/releases/tag/v0.2.0")
        data = cache.load()
        assert data is not None
        assert data["latest_version"] == "0.2.0"
        assert "releases/tag/v0.2.0" in data["release_url"]
        assert (time.time() - data["last_check"]) < 5
    print("✓ test_cache_manager passed")


def test_terminal_ui_non_tty():
    tui = TerminalUI(is_tty=False)
    # Banner output
    tui.show_banner("0.1.0", "0.2.0")

    # Step execution
    executed = []
    ok = tui.run_step(1, 2, "Test Step 1", lambda: (executed.append(1), True)[1])
    assert ok is True
    assert executed == [1]

    # Failure step
    failed_step = tui.run_step(2, 2, "Test Step 2", lambda: False)
    assert failed_step is False

    tui.show_success("0.2.0")
    tui.show_failure("Test Step 2", "Simulated error")
    print("✓ test_terminal_ui_non_tty passed")


def test_update_sequence_dry_run():
    success = run_update_sequence("0.2.0", "0.1.0", dry_run=True)
    assert success is True, "dry_run update sequence should succeed"
    print("✓ test_update_sequence_dry_run passed")


def main():
    print("Running updater test suite...")
    test_parse_semver()
    test_is_newer_version()
    test_current_version_read()
    test_cache_manager()
    test_terminal_ui_non_tty()
    test_update_sequence_dry_run()
    print("\nAll 6 updater checks PASSED successfully!")


if __name__ == "__main__":
    main()
