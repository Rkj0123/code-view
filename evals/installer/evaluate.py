#!/usr/bin/env python3
"""Periodic installer and current-directory command evaluation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
command = [sys.executable, "-m", "unittest", "discover", "-v", "-s", "tests/installer", "-p", "test_*.py"]
result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
print(json.dumps({"eval": "code-view/installer", "passed": result.returncode == 0, "acceptance": ["dependency mapping and versions", "Homebrew PATH", "literal launcher paths", "existing command preservation", "build before publication"], "output": result.stdout + result.stderr}, sort_keys=True))
sys.exit(result.returncode)
