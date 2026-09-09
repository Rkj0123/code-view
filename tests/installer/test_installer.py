from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "install.sh"
COMMAND = ROOT / "script/code-view-command.sh"


class InstallerTests(unittest.TestCase):
    def test_scripts_have_valid_shell_syntax_and_help(self) -> None:
        for script in (INSTALLER, COMMAND):
            result = subprocess.run(["sh", "-n", str(script)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(INSTALLER), "--help"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("code-view", result.stdout)
        result = subprocess.run([str(COMMAND), "--help"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("current directory", result.stdout)
        self.assertIn("for tool in cmake ninja node npm python3 git; do", INSTALLER.read_text(encoding="utf-8"))

    def test_installer_writes_a_command_without_touching_the_source_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory) / "bin"
            environment = os.environ | {
                "CODE_VIEW_SKIP_DEPENDENCIES": "1",
                "CODE_VIEW_SKIP_BUILD": "1",
                "CODE_VIEW_SOURCE_ROOT": str(ROOT),
                "CODE_VIEW_BIN_DIR": str(bin_dir),
            }
            result = subprocess.run([str(INSTALLER)], cwd=ROOT, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = bin_dir / "code-view"
            self.assertTrue(installed.is_file())
            self.assertTrue(os.access(installed, os.X_OK))
            help_result = subprocess.run([str(installed), "--help"], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("current directory", help_result.stdout)

    def test_command_passes_pwd_and_opens_the_startup_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_home = root / "code-view"
            fake_script = fake_home / "script"
            fake_script.mkdir(parents=True)
            launch_log = root / "launch.log"
            opened_url = root / "opened-url"
            fake_launcher = fake_script / "code-view.sh"
            fake_launcher.write_text(
                f'''#!/bin/sh
printf '%s\\n' "$1" > "{launch_log}"
printf '%s\\n' '{{"url":"http://127.0.0.1:12345/?token=test"}}'
''',
                encoding="utf-8",
            )
            fake_launcher.chmod(0o755)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_open = fake_bin / "open"
            fake_open.write_text(f'''#!/bin/sh
printf '%s' "$1" > "{opened_url}"
''', encoding="utf-8")
            fake_open.chmod(0o755)
            repository = root / "repository"
            repository.mkdir()
            environment = os.environ | {"CODE_VIEW_HOME": str(fake_home), "PATH": f"{fake_bin}:{os.environ['PATH']}"}
            result = subprocess.run([str(COMMAND), "--no-watch"], cwd=repository, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(launch_log.read_text(encoding="utf-8").strip()).resolve(), repository.resolve())
            self.assertEqual(opened_url.read_text(encoding="utf-8"), "http://127.0.0.1:12345/?token=test")
            self.assertIn("127.0.0.1:12345", result.stdout)

    def test_installer_does_not_replace_an_unrelated_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory) / "bin"
            bin_dir.mkdir()
            existing = bin_dir / "code-view"
            existing.write_text("#!/bin/sh\necho another-tool\n", encoding="utf-8")
            environment = os.environ | {
                "CODE_VIEW_SKIP_DEPENDENCIES": "1",
                "CODE_VIEW_SKIP_BUILD": "1",
                "CODE_VIEW_SOURCE_ROOT": str(ROOT),
                "CODE_VIEW_BIN_DIR": str(bin_dir),
            }
            result = subprocess.run([str(INSTALLER)], cwd=ROOT, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Refusing to replace", result.stderr)
            self.assertEqual(existing.read_text(encoding="utf-8"), "#!/bin/sh\necho another-tool\n")

    def install_fixture(self, root: Path, **overrides: str):
        source = root / "source"
        script = source / "script"
        script.mkdir(parents=True, exist_ok=True)
        command = script / "code-view-command.sh"
        if not command.exists():
            command.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            command.chmod(0o755)
        environment = os.environ | {
            "CODE_VIEW_SKIP_DEPENDENCIES": "1",
            "CODE_VIEW_SKIP_BUILD": "1",
            "CODE_VIEW_SOURCE_ROOT": str(source),
            "CODE_VIEW_BIN_DIR": str(root / "bin"),
        } | overrides
        return subprocess.run([str(INSTALLER)], env=environment, capture_output=True, text=True)

    def test_literal_launcher_paths_and_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "space ' quote \" $HOME $(touch INJECTED) `touch INJECTED` ; &\nend"
            result = self.install_fixture(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = ["a b", "$(touch INJECTED)", "'\";", "line\nbreak", ""]
            launched = subprocess.run([str(root / "bin/code-view"), *args], cwd=directory,
                                      capture_output=True, text=True)
            self.assertEqual(launched.returncode, 0, launched.stderr)
            self.assertEqual(launched.stdout, "\n".join(args) + "\n")
            self.assertFalse((Path(directory) / "INJECTED").exists())
            result = self.install_fixture(root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_refuses_symlinks_directories_and_embedded_markers(self) -> None:
        for kind in ("dangling", "symlink", "directory", "embedded-marker"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "bin/code-view"
                target.parent.mkdir()
                other = root / "other"
                if kind in ("dangling", "symlink"):
                    if kind == "symlink":
                        other.write_text("#!/bin/sh\n# Code View installed launcher\n")
                    target.symlink_to(other)
                elif kind == "directory":
                    target.mkdir()
                else:
                    target.write_text("#!/bin/sh\necho 'Code View installed launcher'\n")
                result = self.install_fixture(root)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("Refusing to replace", result.stderr)
                self.assertTrue(target.exists() or target.is_symlink())
                if kind in ("dangling", "symlink"):
                    self.assertEqual(target.readlink(), other)

    def test_build_must_succeed_before_publishing(self) -> None:
        for status in (0, 7):
            for existing in (False, True):
                with self.subTest(status=status, existing=existing), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    scripts = root / "source/script"
                    scripts.mkdir(parents=True)
                    build = scripts / "code-view.sh"
                    build.write_text(f'#!/bin/sh\n[ "$1" = --build-only ] || exit 9\necho built\nexit {status}\n')
                    build.chmod(0o755)
                    target = root / "bin/code-view"
                    old = "#!/bin/sh\n# Code View installed launcher\necho old\n"
                    if existing:
                        target.parent.mkdir()
                        target.write_text(old)
                    result = self.install_fixture(root, CODE_VIEW_SKIP_BUILD="0")
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertIn("built", result.stdout)
                    self.assertEqual("Code View installed." in result.stdout, status == 0)
                    if status:
                        self.assertEqual(target.read_text() if existing else target.exists(), old if existing else False)
                    else:
                        self.assertTrue(target.is_file())

    def test_homebrew_shellenv_mapping_and_version_repair(self) -> None:
        for old_node, old_cmake in ((False, False), (True, True)):
            with self.subTest(old_node=old_node), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fake = root / "fake"
                dependencies = root / "dependencies"
                dependencies.mkdir()
                formula_bin = root / "formula/bin"
                fake.mkdir()
                formula_bin.mkdir(parents=True)
                log = root / "brew.log"
                def executable(path: Path, body: str):
                    path.write_text("#!/bin/sh\n" + body + "\n")
                    path.chmod(0o755)
                for tool in ("ninja", "git"):
                    executable(dependencies / tool, "exit 0")
                executable(dependencies / "python3", "exit 1" if old_node else "exit 0")
                executable(formula_bin / "python3", "exit 0")
                executable(dependencies / "node", "echo v18.0.0" if old_node else "echo v22.0.0")
                executable(dependencies / "cmake", "echo cmake version 3.20.0" if old_cmake else "echo cmake version 3.21.0")
                # Simulate missing npm without depending on the host's installed npm.
                executable(dependencies / "npm", "exit 127")
                executable(formula_bin / "node", "echo v24.0.0")
                executable(formula_bin / "cmake", "echo cmake version 4.0.0")
                executable(fake / "brew", f"""printf '%s\\n' "$*" >> {shlex.quote(str(log))}
case "$1" in
 shellenv) printf '%s\\n' {shlex.quote('export PATH=' + shlex.quote(str(dependencies)) + ':"$PATH"')} ;;
 --prefix) printf '%s\\n' {shlex.quote(str(formula_bin.parent))} ;;
 install) if [ "$2" = node ]; then printf '#!/bin/sh\\nexit 0\\n' > {shlex.quote(str(formula_bin / 'npm'))}; chmod +x {shlex.quote(str(formula_bin / 'npm'))}; fi ;;
 list) exit {0 if old_node else 1} ;;
 outdated) {'echo qthttpserver' if old_node else 'exit 0'} ;;
esac""")
                scripts = root / "source/script"
                scripts.mkdir(parents=True)
                executable(scripts / "code-view-command.sh", 'node --version\nprintf "%s\\n" "$CODE_VIEW_PYTHON"')
                result = self.install_fixture(root, CODE_VIEW_SKIP_DEPENDENCIES="0", PATH=f"{fake}:{os.environ['PATH']}")
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = log.read_text().splitlines()
                self.assertEqual(calls[0], "shellenv")
                self.assertIn("install node", calls)
                self.assertNotIn("install npm", calls)
                self.assertIn("upgrade qthttpserver" if old_node else "install qthttpserver", calls)
                self.assertEqual("install cmake" in calls, old_cmake)
                # A new terminal must retain the repaired tool selection.
                launched = subprocess.run([str(root / "bin/code-view")],
                                          env=os.environ | {"PATH": f"{dependencies}:{fake}:{os.environ['PATH']}"},
                                          capture_output=True, text=True)
                self.assertEqual(launched.returncode, 0, launched.stderr)
                self.assertEqual(launched.stdout.splitlines(), ["v24.0.0", str(formula_bin / "python3")])
                # shellenv updates only the installer's environment, not its caller.
                result = self.install_fixture(root, CODE_VIEW_SKIP_DEPENDENCIES="0",
                                              CODE_VIEW_BIN_DIR=str(dependencies), PATH=f"{fake}:{os.environ['PATH']}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("export PATH=", result.stdout)
                if not old_node:
                    executable(dependencies / "npm", "exit 0")
                    executable(dependencies / "python3", "exit 1")
                    log.write_text("")
                    result = self.install_fixture(root, CODE_VIEW_SKIP_DEPENDENCIES="0", PATH=f"{fake}:{os.environ['PATH']}")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("install python", log.read_text().splitlines())

    def test_pinned_python_is_passed_to_host_without_overriding_custom_analyzer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "script"
            scripts.mkdir()
            shutil.copy2(ROOT / "script/code-view.sh", scripts / "code-view.sh")
            (root / "services/web-canvas/node_modules").mkdir(parents=True)
            host = root / "services/local-server/build/code-view-local-server"
            host.parent.mkdir(parents=True)
            host.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            host.chmod(0o755)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for name in ("npm", "cmake"):
                tool = fake_bin / name
                tool.write_text("#!/bin/sh\nexit 0\n")
                tool.chmod(0o755)
            env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "CODE_VIEW_PYTHON": "/chosen/python3"}
            for custom in (False, True):
                extra = ["--analyzer-arg", "/custom/analyzer"] if custom else []
                result = subprocess.run([str(scripts / "code-view.sh"), directory, *extra],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = result.stdout.splitlines()
                self.assertEqual(args[args.index("--analyzer-arg") + 1], "/custom/analyzer" if custom else "/chosen/python3")
                self.assertEqual(args.count("--analyzer-arg"), 1 if custom else 2)

    def test_command_returns_a_failed_host_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_home = root / "code-view"
            fake_script = fake_home / "script"
            fake_script.mkdir(parents=True)
            fake_launcher = fake_script / "code-view.sh"
            fake_launcher.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            fake_launcher.chmod(0o755)
            repository = root / "repository"
            repository.mkdir()
            environment = os.environ | {"CODE_VIEW_HOME": str(fake_home)}
            result = subprocess.run([str(COMMAND)], cwd=repository, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
