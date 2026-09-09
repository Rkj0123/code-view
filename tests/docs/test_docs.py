from __future__ import annotations

import json
import re
import struct
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
PUBLIC_DOCS = [
    README,
    ROOT / "CODE_VIEW.md",
    ROOT / "NOTICE.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "docs/USAGE.md",
    ROOT / "docs/TECHNICAL.md",
    ROOT / "docs/AI_AGENT_SETUP.md",
    ROOT / "docs/verification-log.md",
]
SCREENSHOTS = [
    ROOT / "docs/images/01-entry-focus-light.png",
    ROOT / "docs/images/02-repository-overview-dark.png",
    ROOT / "docs/images/03-inspector-light.png",
    ROOT / "docs/images/04-filters-light.png",
    ROOT / "docs/images/05-git-compare-dark.png",
    ROOT / "docs/images/06-command-approval-light.png",
]


class DocumentationContractTests(unittest.TestCase):
    def test_document_set_and_screenshots_exist(self) -> None:
        for path in PUBLIC_DOCS + SCREENSHOTS:
            self.assertTrue(path.is_file(), path)

    def test_local_markdown_links_resolve(self) -> None:
        markdown_link = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
        for document in PUBLIC_DOCS:
            for raw_target in markdown_link.findall(document.read_text(encoding="utf-8")):
                target = raw_target.strip().split()[0].strip("<>")
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                relative = target.split("#", 1)[0]
                self.assertTrue((document.parent / relative).is_file(), f"{document}: {target}")
                if "#" in target:
                    headings = re.findall(r"^#{1,6} (.+)$", (document.parent / relative).read_text(), re.M)
                    anchors = {re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-") for heading in headings}
                    self.assertIn(target.split("#", 1)[1], anchors, f"{document}: {target}")

    def test_readme_is_standalone(self) -> None:
        text = README.read_text(encoding="utf-8")
        # Historical attribution belongs in licensing, not the product walkthrough.
        lowered = text.split("## License and community")[0].lower()
        for stale in ("sourcetrail", "coatisoftware", "github.com/coatisoftware", "docs/readme/user_interface.png"):
            self.assertNotIn(stale, lowered)
        self.assertIn("./script/code-view.sh \"$PWD/tests/fixtures/tic-tac-toe\"", text)
        self.assertIn("curl -fsSL https://raw.githubusercontent.com/Rkj0123/code-view/main/install.sh | bash", text)
        self.assertIn("\ncode-view\n", text)
        self.assertTrue((ROOT / "install.sh").is_file())
        self.assertTrue((ROOT / "script/code-view-command.sh").is_file())
        self.assertIn("docs/USAGE.md", text)
        self.assertIn("docs/TECHNICAL.md", text)
        self.assertIn("docs/AI_AGENT_SETUP.md", text)

    def test_public_docs_do_not_overstate_scope_or_safety(self) -> None:
        text = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC_DOCS).lower()
        self.assertIn("python is the first", text)
        self.assertIn("java and go", text)
        self.assertIn("future", text)
        self.assertIn("never imports or executes", text)
        self.assertIn("not sandboxed", text)

    def test_prompt_contains_verified_setup_and_safety_steps(self) -> None:
        text = (ROOT / "docs/AI_AGENT_SETUP.md").read_text(encoding="utf-8")
        self.assertIn("brew install cmake ninja qthttpserver node python", text)
        self.assertIn("curl -fsSL https://raw.githubusercontent.com/Rkj0123/code-view/main/install.sh | bash", text)
        self.assertIn('cd "<PYTHON_REPOSITORY>"', text)
        self.assertIn("\n   code-view\n", text)
        self.assertIn("Do not add --allow-command", text)
        self.assertIn("Do not run the target repository", text)
        self.assertIn("<PYTHON_REPOSITORY>", text)
        self.assertNotIn("3.10+.10+", text)

    def test_config_and_approval_examples_have_expected_contract_versions(self) -> None:
        config = json.loads((ROOT / "contracts/code-view-config-v1.example.json").read_text(encoding="utf-8"))
        self.assertEqual(config["schemaVersion"], 1)
        self.assertEqual(config["languages"], ["python"])
        self.assertEqual(config["index"]["tests"], "exclude")
        self.assertEqual(config["index"]["modules"], "hide")
        self.assertEqual(config["index"]["externalPackages"], "collapse")
        approval_schema = json.loads((ROOT / "contracts/code-view-launch-approval-v2.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(approval_schema["properties"]["schemaVersion"]["const"], "code-view.launch-approval/v2")
        compact_technical = (ROOT / "docs/TECHNICAL.md").read_text(encoding="utf-8").replace(" ", "")
        self.assertIn('"schemaVersion":"code-view.launch-approval/v2"', compact_technical)

    def test_screenshots_are_pngs_with_real_dimensions(self) -> None:
        for path in SCREENSHOTS:
            data = path.read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", path)
            width, height = struct.unpack(">II", data[16:24])
            self.assertGreater(width, 800, path)
            self.assertGreater(height, 500, path)

    def test_setup_scripts_parse(self) -> None:
        scripts = (ROOT / "script/code-view.sh", ROOT / "script/code-view-gate.sh", ROOT / "script/code-view-eval.sh")
        for script in scripts:
            completed = subprocess.run(["sh", "-n", str(script)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
