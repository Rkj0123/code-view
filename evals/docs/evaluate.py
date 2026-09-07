#!/usr/bin/env python3
"""Periodic documentation contract evaluation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
command = [sys.executable, "-m", "unittest", "discover", "-s", "tests/docs", "-p", "test_*.py"]
result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
print(json.dumps({"eval": "code-view/documentation", "passed": result.returncode == 0, "output": result.stdout + result.stderr}, sort_keys=True))
sys.exit(result.returncode)
