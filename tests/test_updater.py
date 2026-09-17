#!/usr/bin/env python3
"""Assert-based runnable self-check for updater."""

import sys
import tempfile
import time
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "script"))

from updater import (
    current_version,
    is_newer,
    load_cache,
    parse_semver,
    run_step,
    run_update,
    save_cache,
    show_banner,
)


def test_semver():
    assert parse_semver("0.1.0") == (0, 1, 0, "")
    assert parse_semver("v1.2.3") == (1, 2, 3, "")
    assert parse_semver("2.0.0-rc1") == (2, 0, 0, "rc1")
    assert is_newer("0.2.0", "0.1.0") is True
    assert is_newer("0.1.1", "0.1.0") is True
    assert is_newer("0.1.0", "0.1.0") is False
    assert is_newer("0.0.9", "0.1.0") is False


def test_cache():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cache.json"
        assert load_cache(p) is None
        save_cache("0.2.0", "https://example.com", p)
        c = load_cache(p)
        assert c and c["latest"] == "0.2.0"
        assert (time.time() - c["time"]) < 5


def test_ui_and_run():
    show_banner("0.1.0", "0.2.0")
    assert run_step(1, 1, "test", lambda: True) is True
    assert run_update("0.2.0", "0.1.0", dry_run=True) is True
    assert current_version() == "0.1.0"


def main():
    test_semver()
    test_cache()
    test_ui_and_run()
    print("All updater checks passed.")


if __name__ == "__main__":
    main()
