from __future__ import annotations

import os
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
                "CODE_VIEW_SOURCE_ROOT": str(ROOT),
                "CODE_VIEW_BIN_DIR": str(bin_dir),
            }
            result = subprocess.run([str(INSTALLER)], cwd=ROOT, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Refusing to replace", result.stderr)
            self.assertEqual(existing.read_text(encoding="utf-8"), "#!/bin/sh\necho another-tool\n")

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
