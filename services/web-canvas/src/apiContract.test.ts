import { describe, expect, it } from "vitest";
import { normalizeGraphPayload } from "./apiContract";

// Mirrors contracts/code-view-graph-v1.schema.json rather than the UI model.
const graph = {
  schemaVersion: "code-view.graph/v1",
  project: { root: "." as const, languages: ["python"], entryNodeId: "n_222222222222222222222222", entryReachableNodeIds: ["n_222222222222222222222222"] },
  nodes: [
    { id: "n_111111111111111111111111", kind: "file" as const, language: "python", name: "main.py", qualifiedName: "main.py", path: "main.py", range: { start: { line: 1, column: 1 }, end: { line: 5, column: 1 } }, signature: null, docstring: null, external: false, unresolved: false, reason: null, modifiers: [] },
    { id: "n_222222222222222222222222", kind: "function" as const, language: "python", name: "main", qualifiedName: "main.main", path: "main.py", range: { start: { line: 2, column: 1 }, end: { line: 4, column: 1 } }, signature: "def main()", docstring: "Entry point", external: false, unresolved: false, reason: null, modifiers: ["entry", "stale"] },
    { id: "n_333333333333333333333333", kind: "external" as const, language: null, name: "requests", qualifiedName: "requests", path: null, range: null, signature: null, docstring: null, external: true, unresolved: false, reason: "dependency boundary", modifiers: [] },
  ],
  edges: [
    { id: "e_111111111111111111111111", kind: "contains" as const, language: "python", source: "n_111111111111111111111111", target: "n_222222222222222222222222", path: "main.py", range: { start: { line: 2, column: 1 }, end: { line: 4, column: 1 } }, reason: null },
    { id: "e_222222222222222222222222", kind: "api_calls" as const, language: "python", source: "n_222222222222222222222222", target: "n_333333333333333333333333", path: "main.py", range: { start: { line: 3, column: 3 }, end: { line: 3, column: 17 } }, reason: "target outside repository" },
  ],
  diagnostics: [],
};

const envelope = {
  generation: 7,
  projectName: "fixture",
  config: { schemaVersion: 1 },
  comparison: {
    base: "HEAD",
    target: "WORKTREE",
    nodes: { modified: [{ id: "n_222222222222222222222222", current: graph.nodes[1], base: graph.nodes[1] }] },
    edges: { added: [{ id: "e_222222222222222222222222", current: graph.edges[1] }] },
  },
  graph,
};

describe("normalizeGraphPayload", () => {
  it("maps a real analyzer document once at the host boundary", () => {
    const normalized = normalizeGraphPayload(envelope);
    expect(normalized.entryKey).toBe("n_222222222222222222222222");
    expect(normalized.nodes.find((node) => node.label === "main")).toMatchObject({ parent: "n_111111111111111111111111", stale: true, entry: true, diff: "modified", summary: "Entry point", source: { path: "main.py" } });
    expect(normalized.nodes.find((node) => node.label === "requests")).toMatchObject({ kind: "external_package", resolution: "external", boundaryReason: "dependency boundary" });
    expect(normalized.edges.find((edge) => edge.kind === "api_calls")).toMatchObject({ resolution: "external", diff: "added", locations: [{ path: "main.py" }], boundaryReason: "target outside repository" });
  });

  it("rejects schema versions the client cannot interpret", () => {
    expect(() => normalizeGraphPayload({ ...envelope, graph: { ...graph, schemaVersion: "code-view.graph/v2" } })).toThrow("Unsupported graph schemaVersion: code-view.graph/v2");
  });

  it("carries code-view canvas choices across the host boundary", () => {
    const normalized = normalizeGraphPayload({
      ...envelope,
      config: { includeTests: true, modules: "show", initialView: "whole-repo", relationships: ["calls", "test_covers"], gitBase: "main" },
    });
    expect(normalized.preferences).toEqual({
      initialView: "repository",
      showModules: true,
      includeTests: true,
      relationships: ["calls", "test_covers"],
      gitBase: "main",
    });
  });

  it("makes range-less file boundaries readable", () => {
    const rangeLess = structuredClone(envelope);
    rangeLess.graph.nodes[0].range = null;
    const file = normalizeGraphPayload(rangeLess).nodes.find((node) => node.kind === "file");
    expect(file?.source).toEqual({ path: "main.py", start: { line: 1, column: 1 }, end: { line: 400, column: 1 } });
  });

  it("caps long symbol previews to the host source window", () => {
    const longRange = structuredClone(envelope);
    longRange.graph.nodes[0].range!.end.line = 900;
    expect(normalizeGraphPayload(longRange).nodes[0].source?.end).toEqual({ line: 400, column: 1 });
  });

  it("parents unresolved boundaries to their source file for collapsing", () => {
    const unresolved = structuredClone(envelope) as unknown as { graph: { nodes: Array<Record<string, unknown>> } };
    unresolved.graph.nodes[2] = {
      ...unresolved.graph.nodes[2], kind: "unresolved", external: false, unresolved: true,
      name: "dynamic()", qualifiedName: "main::<unresolved>:dynamic", path: "main.py",
      range: { start: { line: 3, column: 1 }, end: { line: 3, column: 10 } }, reason: "dynamic target",
    };
    expect(normalizeGraphPayload(unresolved).nodes.find((node) => node.kind === "unresolved")?.parent)
      .toBe("n_111111111111111111111111");
  });
});
