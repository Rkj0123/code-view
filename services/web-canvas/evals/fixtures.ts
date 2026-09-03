import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

export function analyzerFixture(tenFiles = false) {
  const temporary = tenFiles ? mkdtempSync(join(tmpdir(), "code-view-ten-files-")) : undefined;
  try {
    if (temporary) {
      const imports = Array.from({ length: 9 }, (_, index) => `from module_${index + 1} import run as run_${index + 1}`);
      const calls = Array.from({ length: 9 }, (_, index) => `run_${index + 1}()`);
      writeFileSync(join(temporary, "main.py"), `${imports.join("\n")}\n\ndef main():\n    return ${calls.join(" + ")}\n\ndef start():\n    return main()\n\nif __name__ == '__main__':\n    start()\n`);
      for (let index = 1; index <= 9; index += 1) writeFileSync(join(temporary, `module_${index}.py`), `def helper():\n    return ${index}\n\ndef run():\n    return helper()\n`);
    }
    const root = temporary ?? resolve(process.cwd(), "../../tests/fixtures/tic-tac-toe");
    const graph = JSON.parse(execFileSync("python3", [
      resolve(process.cwd(), "../python-analyzer/analyzer.py"),
      root,
    ], { encoding: "utf8" }));
    const sourceByPath = Object.fromEntries(graph.nodes.filter((node: { kind: string }) => node.kind === "file").map((node: { path: string }) => [node.path, readFileSync(join(root, node.path), "utf8")])) as Record<string, string>;
    return { graph, sourceByPath };
  } finally {
    if (temporary) rmSync(temporary, { recursive: true });
  }
}
