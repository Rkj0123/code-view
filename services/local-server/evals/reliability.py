#!/usr/bin/env python3
"""Local, deterministic lifecycle and file-boundary eval. No project code is imported."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "services/local-server/build/code-view-local-server"
ANALYZER = ROOT / "services/python-analyzer/analyzer.py"
OUTSIDE = b"CODE_VIEW_OUTSIDE_BOUNDARY_SENTINEL"
results: list[dict[str, object]] = []


def check(name: str, passed: bool, **details: object) -> None:
    results.append({"name": name, "passed": bool(passed), **details})


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def gone(pid: int) -> bool:
    deadline = time.monotonic() + 2
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not alive(pid)


def read_pid(path: Path) -> int:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.01)
    raise RuntimeError("child PID was not published")


def stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def host(repo: Path, web: Path, *, allow: bool = False,
         env: dict[str, str] | None = None) -> tuple[subprocess.Popen[str], int, str]:
    argv = [str(SERVER), "--repo", str(repo), "--web-root", str(web), "--no-watch",
            "--analyzer-arg", sys.executable, "--analyzer-arg", str(ANALYZER)]
    if allow:
        argv.append("--allow-command")
    process = subprocess.Popen(argv, cwd=repo, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], 10)
    if not ready:
        stop(process)
        raise RuntimeError("host startup timed out")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError(f"host startup failed: {process.stderr.read() if process.stderr else ''}")
    parsed = urlparse(json.loads(line)["url"])
    assert parsed.port is not None
    return process, parsed.port, parse_qs(parsed.query)["token"][0]


def request(port: int, token: str, path: str, method: str = "GET",
            body: dict[str, object] | None = None) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def file_boundaries(repo: Path, web: Path, outside: Path) -> None:
    process, port, token = host(repo, web)
    stop_event = threading.Event()

    def toggle(target: Path) -> None:
        temporary = target.with_name(".swap-" + target.name)
        index = 0
        while not stop_event.is_set():
            temporary.unlink(missing_ok=True)
            if index % 2:
                temporary.symlink_to(outside)
            else:
                temporary.write_bytes(b"SAFE\n")
            os.replace(temporary, target)
            index += 1

    def read_many(path: str) -> tuple[int, int]:
        leaks = successes = 0
        for _ in range(1_000):
            status, body = request(port, token, path)
            leaks += OUTSIDE in body
            successes += status == 200
        return leaks, successes

    threads = [threading.Thread(target=toggle, args=(path,), daemon=True)
               for path in (repo / "main.py", web / "assets/race.txt")]
    try:
        for thread in threads:
            thread.start()
        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = ["/api/v1/source?path=main.py&startLine=1&endLine=2"] * 2 + ["/assets/race.txt"] * 2
            rows = list(pool.map(read_many, paths))
        stop_event.set()
        for thread in threads:
            thread.join()
        for name, values in (("source", rows[:2]), ("static", rows[2:])):
            leaks = sum(row[0] for row in values)
            successes = sum(row[1] for row in values)
            check(name + " atomic replacement confinement", leaks == 0 and successes > 0,
                  requests=2_000, leaks=leaks, successfulReads=successes)
        for target, endpoint in ((repo / "main.py", "/api/v1/source?path=main.py"),
                                 (web / "assets/race.txt", "/assets/race.txt")):
            target.unlink(missing_ok=True)
            os.mkfifo(target)
            started = time.monotonic()
            status, _ = request(port, token, endpoint)
            check(target.name + " FIFO rejected without a writer", status != 200,
                  durationMs=round((time.monotonic() - started) * 1_000, 3))
            target.unlink()
            target.write_bytes(b"SAFE\n")
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=1)
        stop(process)


def shutdown(repo: Path, web: Path) -> None:
    command = repo / "command.py"
    command.write_text("import pathlib, subprocess, time\n"
                       "child = subprocess.Popen(['/bin/sleep', '30'], start_new_session=True)\n"
                       "pathlib.Path('child.pid').write_text(str(child.pid))\n"
                       "time.sleep(30)\n")
    (repo / "code-view.json").write_text(json.dumps({"entry": {"file": "main.py", "command": {
        "argv": [sys.executable, str(command)], "mode": "manual"}}}))
    for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
        pid_file = repo / "child.pid"
        pid_file.unlink(missing_ok=True)
        process, port, token = host(repo, web, allow=True)
        leader = child = 0
        try:
            _, launch_body = request(port, token, "/api/v1/launch")
            generation = json.loads(launch_body)["generation"]
            denied, _ = request(port, token, "/api/v1/launch/start", "POST")
            unbound, _ = request(port, token, "/api/v1/launch/approve", "POST")
            approved, _ = request(port, token, "/api/v1/launch/approve", "POST", {
                "schemaVersion": "code-view.launch-approval/v2", "generation": generation})
            started, body = request(port, token, "/api/v1/launch/start", "POST")
            leader = int(json.loads(body).get("pid") or 0)
            reused, _ = request(port, token, "/api/v1/launch/start", "POST")
            check(shutdown_signal.name + " command guards", denied == reused == unbound == 409 and approved == started == 200)
            child = read_pid(pid_file)
            process.send_signal(shutdown_signal)
            process.wait(timeout=3)
            check(shutdown_signal.name + " cleans leader and separate-session child", gone(leader) and gone(child))
        finally:
            stop(process)
            for pid in (child, leader):
                if alive(pid):
                    os.kill(pid, signal.SIGKILL)

    wrapper = repo / "slow-analyzer.py"
    wrapper.write_text("import pathlib, subprocess, sys, time\n"
                       "child = subprocess.Popen(['/bin/sleep', '30'])\n"
                       "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
                       "time.sleep(30)\n")
    for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
        pid_file = repo / "initial-child.pid"
        pid_file.unlink(missing_ok=True)
        process = subprocess.Popen([str(SERVER), "--repo", str(repo), "--no-watch",
                                    "--analyzer-arg", sys.executable, "--analyzer-arg", str(wrapper),
                                    "--analyzer-arg", str(pid_file)], text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child = 0
        try:
            child = read_pid(pid_file)
            process.send_signal(shutdown_signal)
            process.wait(timeout=3)
            check(shutdown_signal.name + " interrupts initial analysis and cleans its child", gone(child))
        finally:
            stop(process)
            if alive(child):
                os.kill(child, signal.SIGKILL)
    (repo / "code-view.json").unlink()


def displayed_approval(repo: Path, web: Path) -> None:
    for form in ("startCommand", "entry.command"):
        config_path = repo / "code-view.json"
        marker = repo / "unseen-command-ran"
        marker.unlink(missing_ok=True)

        def configure(argv: list[str]) -> None:
            command = {"argv": argv, "mode": "manual"}
            config = {"entry": {"file": "main.py", "command": command}} if form == "entry.command" else {
                "entry": {"file": "main.py"}, "startCommand": command}
            config_path.write_text(json.dumps(config))

        configure([sys.executable, "-c", "pass"])
        process, port, token = host(repo, web, allow=True)
        try:
            _, body = request(port, token, "/api/v1/launch")
            displayed = json.loads(body)["generation"]
            configure([sys.executable, "-c", f"import pathlib,time; pathlib.Path({str(marker)!r}).touch(); time.sleep(30)"])
            status, _ = request(port, token, "/api/v1/reindex", "POST")
            assert status == 200
            deadline = time.monotonic() + 5
            current = displayed
            while current == displayed and time.monotonic() < deadline:
                _, body = request(port, token, "/api/v1/launch")
                current = json.loads(body)["generation"]
                time.sleep(0.01)
            assert current > displayed, "replacement config did not index"
            stale, _ = request(port, token, "/api/v1/launch/approve", "POST", {
                "schemaVersion": "code-view.launch-approval/v2", "generation": displayed})
            denied, _ = request(port, token, "/api/v1/launch/start", "POST")
            check(form + " stale displayed command cannot authorize its replacement",
                  stale == denied == 409 and not marker.exists(), displayed=displayed, current=current)
            for invalid in ({"generation": current}, {"schemaVersion": "code-view.launch-approval/v2", "generation": str(current)},
                            {"schemaVersion": "code-view.launch-approval/v2", "generation": current, "argv": []}):
                assert request(port, token, "/api/v1/launch/approve", "POST", invalid)[0] == 409
            approved, _ = request(port, token, "/api/v1/launch/approve", "POST", {
                "schemaVersion": "code-view.launch-approval/v2", "generation": current})
            started, _ = request(port, token, "/api/v1/launch/start", "POST")
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            stopped, _ = request(port, token, "/api/v1/launch/stop", "POST")
            check(form + " newly reviewed command starts and stops",
                  approved == started == stopped == 200 and marker.exists())

            marker.unlink(missing_ok=True)
            approved, _ = request(port, token, "/api/v1/launch/approve", "POST", {
                "schemaVersion": "code-view.launch-approval/v2", "generation": current})
            config_path.write_text('{"unknownKey":true}')
            assert request(port, token, "/api/v1/reindex", "POST")[0] == 200
            deadline = time.monotonic() + 5
            failed = {}
            while time.monotonic() < deadline:
                _, body = request(port, token, "/api/v1/status")
                failed = json.loads(body)
                if failed.get("lastError"):
                    break
                time.sleep(0.01)
            _, body = request(port, token, "/api/v1/launch")
            failed_launch = json.loads(body)
            old_start, _ = request(port, token, "/api/v1/launch/start", "POST")
            fresh_approval, _ = request(port, token, "/api/v1/launch/approve", "POST", {
                "schemaVersion": "code-view.launch-approval/v2", "generation": current})
            check(form + " failed reindex preserves facts but rejects old and fresh command approval",
                  approved == 200 and failed.get("generation") == current and failed.get("state") == "ready" and
                  bool(failed.get("lastError")) and failed.get("nodeCount", 0) > 0 and
                  not failed_launch.get("approved") and not failed_launch.get("allowLaunch") and
                  old_start == fresh_approval == 409 and not marker.exists(),
                  generation=failed.get("generation"), oldStartStatus=old_start,
                  freshApprovalStatus=fresh_approval, markerExists=marker.exists())

            configure([sys.executable, "-c", f"import pathlib,time; pathlib.Path({str(marker)!r}).touch(); time.sleep(30)"])
            assert request(port, token, "/api/v1/reindex", "POST")[0] == 200
            deadline = time.monotonic() + 5
            repaired = current
            while repaired == current and time.monotonic() < deadline:
                _, body = request(port, token, "/api/v1/launch")
                repaired = json.loads(body)["generation"]
                time.sleep(0.01)
            assert repaired > current, "repaired config did not index"
            old_start, _ = request(port, token, "/api/v1/launch/start", "POST")
            old_approval, _ = request(port, token, "/api/v1/launch/approve", "POST", {
                "schemaVersion": "code-view.launch-approval/v2", "generation": current})
            approved, _ = request(port, token, "/api/v1/launch/approve", "POST", {
                "schemaVersion": "code-view.launch-approval/v2", "generation": repaired})
            started, _ = request(port, token, "/api/v1/launch/start", "POST")
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            stopped, _ = request(port, token, "/api/v1/launch/stop", "POST")
            check(form + " repaired index requires and accepts a new command review",
                  old_start == old_approval == 409 and approved == started == stopped == 200 and marker.exists(),
                  oldGeneration=current, repairedGeneration=repaired)
        finally:
            stop(process)
            config_path.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)


def git_boundary(repo: Path, web: Path) -> None:
    def git(*argv: str) -> None:
        subprocess.run(["/usr/bin/git", "-C", str(repo), *argv], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    git("init", "-q")
    git("config", "user.name", "Code View Eval")
    git("config", "user.email", "eval@example.invalid")
    git("add", "main.py")
    git("commit", "-qm", "baseline")
    marker = repo.parent / "fake-git-executed"
    fake = repo / "git"
    fake.write_text(f"#!/bin/sh\nprintf hit >> '{marker}'\nexec /usr/bin/git \"$@\"\n")
    fake.chmod(0o700)
    original = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (repo / "main.py", repo / ".git/index")}
    env = os.environ.copy()
    env["PATH"] = ".:" + env.get("PATH", "")
    process, port, token = host(repo, web, env=env)
    try:
        statuses = [request(port, token, path)[0] for path in (
            "/api/v1/graph?view=repository&base=HEAD", "/api/v1/git/revision?ref=HEAD",
            "/api/v1/compare?base=HEAD&target=WORKTREE")]
        check("Git uses system executable despite repository PATH", statuses == [200, 200, 200] and not marker.exists())
        check("Git inspection preserves source and index bytes", all(
            hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in original.items()))
    finally:
        stop(process)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="code-view-host-eval-") as directory:
        temp = Path(directory)
        repo, web = temp / "repo", temp / "web"
        repo.mkdir()
        (web / "assets").mkdir(parents=True)
        (repo / "main.py").write_text("def main():\n    return 1\n")
        (web / "index.html").write_text("<!doctype html><title>Code View eval</title>\n")
        (web / "assets/race.txt").write_text("SAFE\n")
        outside = temp / "outside.txt"
        outside.write_bytes(OUTSIDE)
        file_boundaries(repo, web, outside)
        shutdown(repo, web)
        displayed_approval(repo, web)
        git_boundary(repo, web)
    passed = sum(bool(row["passed"]) for row in results)
    print(json.dumps({"eval": "local-server-reliability", "score": passed,
                      "threshold": len(results), "results": results}, sort_keys=True))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
