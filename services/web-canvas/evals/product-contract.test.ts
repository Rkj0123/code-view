import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { buildVisibleGraph, defaultFilters, filtersFromPreferences, largeRepositoryOverview, neighborhood, selectedEdge } from "../src/graphModel";
import { mockGraph } from "../src/mockData";
import { bootstrapCapabilityToken } from "../src/auth";
import { normalizeIndexStatus, normalizeLaunchConfiguration } from "../src/api";
import { nodeKinds, relationKinds, type GraphDocument, type GraphNode } from "../src/types";

describe("web canvas product contract eval", () => {
  it("keeps every canonical relationship selectable and only test coverage off", () => {
    const filters = defaultFilters();
    expect(relationKinds).toEqual(["contains", "imports", "calls", "inherits", "constructs", "reads", "writes", "decorates", "type_uses", "api_calls", "test_covers"]);
    expect(relationKinds.filter((kind) => !filters.relations[kind])).toEqual(["test_covers"]);
  });

  it("shows useful symbols but hides package/module groups and tests by default", () => {
    const filters = defaultFilters();
    expect(filters.nodes.package).toBe(false);
    expect(filters.nodes.module).toBe(false);
    for (const kind of ["file", "class", "function", "method", "variable", "external_package", "unresolved"] as const) expect(filters.nodes[kind]).toBe(true);
    expect(filters.includeTests).toBe(false);
    const visible = buildVisibleGraph(mockGraph, filters, "repository", new Set());
    expect(visible.nodes.some((node) => node.kind === "module" || node.kind === "package" || node.test)).toBe(false);
  });

  it("honors explicit config selections without changing defaults", () => {
    const filters = filtersFromPreferences({
      initialView: "repository", showModules: true, includeTests: true,
      relationships: ["calls", "test_covers"], gitBase: "baseline",
    });
    expect(filters.nodes.module && filters.nodes.package && filters.includeTests).toBe(true);
    expect(relationKinds.filter((kind) => filters.relations[kind])).toEqual(["calls", "test_covers"]);
  });

  it("entry focus traverses the complete statically reachable graph, not four levels", () => {
    const extraNodes: GraphNode[] = Array.from({ length: 7 }, (_, index) => ({ id: `deep:${index}`, kind: "function", label: `deep_${index}`, qualifiedName: `deep_${index}`, resolution: "resolved", test: false, diff: "unchanged" }));
    const document: GraphDocument = {
      ...structuredClone(mockGraph),
      entryKey: extraNodes[0].id,
      nodes: extraNodes,
      edges: extraNodes.slice(1).map((node, index) => ({ id: `deep-edge:${index}`, kind: "calls", source: extraNodes[index].id, target: node.id, resolution: "resolved", confidence: "exact", count: 1, diff: "unchanged" })),
    };
    const visible = buildVisibleGraph(document, defaultFilters(), "entry", new Set());
    expect(visible.nodes.map((node) => node.id)).toContain("deep:6");
  });

  it("changed-only scope removes unchanged semantic nodes while retaining structural ancestors", () => {
    const filters = defaultFilters();
    filters.changedOnly = true;
    const visible = buildVisibleGraph(mockGraph, filters, "repository", new Set());
    expect(visible.nodes.some((node) => node.id === "py:function:src.rules.winner")).toBe(false);
    expect(visible.nodes.some((node) => node.diff !== "unchanged")).toBe(true);
  });

  it("keeps all runtime assets local and the selector vocabulary complete", () => {
    const css = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");
    const html = readFileSync(`${process.cwd()}/index.html`, "utf8");
    expect(`${css}\n${html}`).not.toMatch(/https?:\/\//);
    expect(new Set(nodeKinds)).toEqual(new Set(["directory", "file", "module", "package", "class", "function", "method", "variable", "external_package", "unresolved"]));
  });

  it("keeps the 390px toolbar reachable and exposes fit and reset accessibly", () => {
    const css = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");
    const app = readFileSync(`${process.cwd()}/src/App.tsx`, "utf8");
    expect(css).toContain("grid-template-columns: 38px minmax(0, 1fr) auto");
    expect(css).toContain(".search-trigger kbd, .generation { display: none; }");
    expect(app).toContain('aria-label="Fit graph"');
    expect(app).toContain('aria-label="Reset graph view"');
    expect(app).toContain('aria-label="Toggle graph filters"');
    expect(app).toContain('aria-label="Toggle selection details"');
  });

  it("removes capability secrets from the URL and rejects partial launch state", () => {
    let cleaned = "";
    expect(bootstrapCapabilityToken(true, "http://127.0.0.1:4173/?token=secret", (url) => { cleaned = url; })).toBe("secret");
    expect(cleaned).toBe("/");
    expect(() => normalizeLaunchConfiguration({ started: true, pid: 1234 })).toThrow("Invalid launch configuration response");
  });

  it("retains failed index status even when the last valid generation is ready", () => {
    for (const state of ["ready", "indexing", "error"]) {
      const status = { generation: 7, state, lastError: "Unknown code-view.json field: unknown" };
      expect(normalizeIndexStatus(status)).toEqual(status);
    }
  });

  it("rejects unbound launch responses instead of approving an unseen command", () => {
    const launch = { display: "python main.py", argv: ["python", "main.py"], cwd: "/repo", mode: "manual", allowLaunch: true, running: false };
    expect(() => normalizeLaunchConfiguration(launch)).toThrow();
    expect(normalizeLaunchConfiguration({ ...launch, generation: 7 }).generation).toBe(7);
  });

  it("keeps graph navigation and relayout motion-free, including reduced-motion users", () => {
    const canvas = readFileSync(`${process.cwd()}/src/components/GraphCanvas.tsx`, "utf8");
    expect(canvas).not.toMatch(/cy\.animate\(/);
    expect(canvas).toContain("animate: false");
    expect(canvas).toContain("cy.center(nodes.first())");
  });

  it("filters a 5,000-symbol repository inside the local interaction budget", () => {
    const nodes: GraphNode[] = Array.from({ length: 5_000 }, (_, index) => ({ id: `large:${index}`, kind: "function", label: `symbol_${index}`, qualifiedName: `symbol_${index}`, resolution: "resolved", test: false, diff: "unchanged" }));
    const document: GraphDocument = {
      ...structuredClone(mockGraph),
      entryKey: nodes[0].id,
      nodes,
      edges: nodes.slice(1).map((node, index) => ({ id: `large-edge:${index}`, kind: "calls", source: nodes[index].id, target: node.id, resolution: "resolved", confidence: "exact", count: 1, diff: "unchanged" })),
    };
    const started = performance.now();
    const visible = buildVisibleGraph(document, defaultFilters(), "repository", new Set());
    expect(visible.nodes).toHaveLength(5_000);
    expect(performance.now() - started).toBeLessThan(1_000);
  });

  it("preserves evidence when collapsed relationships aggregate", () => {
    const original = mockGraph.edges.find((edge) => edge.kind === "constructs")!;
    const document = { ...mockGraph, edges: [...mockGraph.edges, { ...original, id: "second-construct", target: "py:method:src.game.Game.__init__" }] };
    const visible = buildVisibleGraph(document, defaultFilters(), "repository", new Set(["file:src/game.py"]));
    const aggregate = visible.edges.find((edge) => edge.kind === "constructs" && edge.target === "file:src/game.py")!;
    expect(aggregate.count).toBe(2);
    expect(aggregate.locations).toHaveLength(2);
    expect(selectedEdge(document, visible.edges, aggregate.id)).toEqual(aggregate);
    expect(neighborhood({ ...document, edges: visible.edges }, "file:src/game.py").inbound.length).toBeGreaterThan(0);
  });

  it("uses collapsed file boundaries for large repository orientation", () => {
    const file = mockGraph.nodes.find((node) => node.id === "file:src/game.py")!;
    const symbol = mockGraph.nodes.find((node) => node.id === "py:method:src.game.Game.play")!;
    const document = { ...structuredClone(mockGraph), nodes: [file, ...Array.from({ length: 1_000 }, (_, index) => ({ ...symbol, id: `symbol:${index}`, parent: file.id }))] };
    const collapsed = largeRepositoryOverview(document);
    expect(collapsed).toEqual(new Set([file.id]));
    expect(buildVisibleGraph(document, defaultFilters(), "repository", collapsed).nodes).toHaveLength(1);
  });

  it("groups external symbols without losing aggregate evidence", () => {
    const external = mockGraph.nodes.find((node) => node.kind === "external_package")!;
    const original = mockGraph.edges.find((edge) => edge.kind === "api_calls" && edge.target === external.id)!;
    const document = {
      ...mockGraph,
      nodes: [...mockGraph.nodes, { ...external, id: "external:rich.console", qualifiedName: "rich.console" }],
      edges: [...mockGraph.edges, { ...original, id: "second-rich", target: "external:rich.console" }],
    };
    const visible = buildVisibleGraph(document, defaultFilters(), "repository", new Set());
    expect(visible.nodes.filter((node) => node.kind === "external_package")).toHaveLength(1);
    expect(visible.edges.find((edge) => edge.target === "group:external:rich")?.count).toBe(2);
  });
});
