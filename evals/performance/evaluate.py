#!/usr/bin/env python3
"""Qualified 10k-Python-SLOC performance envelope for Code View."""

import json
from pathlib import Path
import statistics
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
HOST_EVAL = ROOT / "services/local-server/build/code-view-local-server-eval"


def generate(root: Path) -> int:
    for module_index in range(100):
        lines = ["import json", f"VALUE_{module_index} = {module_index}"]
        for function_index in range(49):
            lines.extend([
                f"def f{function_index}():",
                f"    return {'VALUE_' + str(module_index) if function_index == 0 else 'f' + str(function_index - 1) + '()'}",
            ])
        (root / f"module_{module_index:03}.py").write_text("\n".join(lines) + "\n")
    (root / "code-view.json").write_text(json.dumps({"entry": {"file": "module_099.py", "symbol": "f48"}}))
    return sum(len(path.read_text().splitlines()) for path in root.glob("*.py"))


def generate_import_heavy(root: Path) -> int:
    lines = []
    for index in range(2500):
        lines.extend((f"def f{index}():", "    pass"))
    lines.extend(f"import dependency_{index}" for index in range(5000))
    (root / "main.py").write_text("\n".join(lines) + "\n")
    (root / "code-view.json").write_text(json.dumps({"entry": "main.py"}))
    return len(lines)


def benchmark(root: Path) -> dict:
    runs = []
    for _ in range(5):
        completed = subprocess.run(
            [str(HOST_EVAL), "--benchmark", str(root)],
            check=True,
            capture_output=True,
            text=True,
        )
        runs.append(json.loads(completed.stdout))
    durations = [run["analysisImportMs"] for run in runs]
    return {
        "runs": durations,
        "p95": max(durations),
        "median": statistics.median(durations),
        "adjacencyP95Ms": max(run["adjacencyP95Ms"] for run in runs),
        "adjacencySamples": sum(run["adjacencySamples"] for run in runs),
        "nodes": runs[-1]["nodes"],
        "edges": runs[-1]["edges"],
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="code-view-10k-") as temporary, tempfile.TemporaryDirectory(prefix="code-view-import-heavy-10k-") as import_temporary:
        root, import_root = Path(temporary), Path(import_temporary)
        sloc, import_sloc = generate(root), generate_import_heavy(import_root)
        if not HOST_EVAL.is_file():
            raise SystemExit("host benchmark is not built; run the documented CMake build first")
        standard, import_heavy = benchmark(root), benchmark(import_root)
        passed = (
            sloc == import_sloc == 10_000
            and max(standard["p95"], import_heavy["p95"]) <= 5_000
            and max(standard["adjacencyP95Ms"], import_heavy["adjacencyP95Ms"]) <= 100
        )
        result = {
            "eval": "code-view/qualified-10k-python-sloc",
            "passed": passed,
            "sloc": sloc,
            "graph": {"nodes": standard["nodes"], "edges": standard["edges"]},
            "coldGraphMs": {"runs": standard["runs"], "p95": standard["p95"], "median": standard["median"], "budget": 5_000},
            "importHeavyColdGraphMs": {"runs": import_heavy["runs"], "p95": import_heavy["p95"], "median": import_heavy["median"], "budget": 5_000,
                                        "shape": {"twoLineFunctions": 2500, "uniqueImports": 5000},
                                        "graph": {"nodes": import_heavy["nodes"], "edges": import_heavy["edges"]}},
            "adjacencyQueryMs": {"samples": standard["adjacencySamples"] + import_heavy["adjacencySamples"],
                                 "p95": max(standard["adjacencyP95Ms"], import_heavy["adjacencyP95Ms"]), "budget": 100,
                                 "implementation": "GraphSnapshot.node"},
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
