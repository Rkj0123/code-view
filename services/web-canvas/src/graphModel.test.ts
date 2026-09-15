import { describe, expect, it } from "vitest";
import { mockGraph } from "./mockData";
import { buildVisibleGraph, defaultFilters, filtersFromPreferences, inspectionNeighborhood, largeRepositoryOverview, neighborhood, searchNodes, selectedEdge, selectionFocusTarget } from "./graphModel";
import type { GraphNode, GraphEdge } from "./types";

describe("graph model", () => {
  it("excludes tests, modules, and test coverage by default", () => {
    const visible = buildVisibleGraph(mockGraph, defaultFilters(), "repository", new Set());
    expect(visible.nodes.some((node) => node.test)).toBe(false);
    expect(visible.nodes.some((node) => node.kind === "module")).toBe(false);
    expect(visible.edges.some((edge) => edge.kind === "test_covers")).toBe(false);
  });

  it("includes tests only when both controls allow them", () => {
    const filters = defaultFilters();
    filters.includeTests = true;
    filters.relations.test_covers = true;
    const visible = buildVisibleGraph(mockGraph, filters, "repository", new Set());
    expect(visible.nodes.some((node) => node.id === "py:function:tests.test_winner")).toBe(true);
    expect(visible.edges.some((edge) => edge.kind === "test_covers")).toBe(true);
  });

  it("applies explicit repository canvas preferences", () => {
    const filters = filtersFromPreferences({
      initialView: "repository",
      showModules: true,
      includeTests: true,
      relationships: ["calls", "test_covers"],
      gitBase: "main",
    });
    expect(filters.nodes.module).toBe(true);
    expect(filters.nodes.package).toBe(true);
    expect(filters.includeTests).toBe(true);
    expect(Object.entries(filters.relations).filter(([, enabled]) => enabled).map(([kind]) => kind)).toEqual(["calls", "test_covers"]);
  });

  it("collapses descendants and aggregates their edges at the group", () => {
    const visible = buildVisibleGraph(mockGraph, defaultFilters(), "repository", new Set(["file:src/game.py"]));
    expect(visible.nodes.some((node) => node.id === "py:class:src.game.Game")).toBe(false);
    expect(visible.edges.some((edge) => edge.target === "file:src/game.py" && edge.kind === "constructs")).toBe(true);
  });

  it("projects hidden module endpoints to visible file boundaries", () => {
    const imported = mockGraph.edges.find((edge) => edge.kind === "imports")!;
    const sourceFile = mockGraph.nodes.find((node) => node.id === "file:src/main.py")!;
    const sourceModule = mockGraph.nodes.find((node) => node.id === "py:module:src.main")!;
    const targetFile = mockGraph.nodes.find((node) => node.id === "file:src/game.py")!;
    const targetModule = mockGraph.nodes.find((node) => node.id === "py:module:src.game")!;
    const extraModule = { ...sourceModule, id: "py:module:src.main.extra", qualifiedName: "src.main.extra" };
    const firstLocation = { path: "src/main.py", start: { line: 1, column: 1 }, end: { line: 1, column: 8 } };
    const secondLocation = { path: "src/main.py", start: { line: 2, column: 1 }, end: { line: 2, column: 8 } };
    const first = { ...imported, id: "projected-import-a", source: sourceModule.id, target: targetModule.id, count: 2, diff: "added" as const, resolution: "resolved" as const, confidence: "exact" as const, locations: [firstLocation] };
    const second = { ...imported, id: "projected-import-b", source: sourceModule.id, target: targetModule.id, count: 3, diff: "removed" as const, resolution: "external" as const, confidence: "heuristic" as const, locations: [secondLocation] };
    const base = { ...mockGraph, entryKey: sourceModule.id, nodes: [sourceFile, sourceModule, targetFile, targetModule, extraModule] };
    const single = buildVisibleGraph({ ...base, edges: [first] }, defaultFilters(), "entry", new Set());
    const id = "imports|file:src/main.py|file:src/game.py";
    expect(single.edges[0]).toMatchObject({ id, source: sourceFile.id, target: targetFile.id, count: 2, resolution: "resolved", diff: "added" });

    const document = { ...base, edges: [first, second, { ...imported, id: "projected-self", source: sourceModule.id, target: extraModule.id }] };
    const visible = buildVisibleGraph(document, defaultFilters(), "entry", new Set());
    const projected = visible.edges.find((edge) => edge.id === id);
    expect(projected).toMatchObject({ kind: "imports", source: sourceFile.id, target: targetFile.id, count: 5, resolution: "external", confidence: "heuristic", diff: "modified" });
    expect(projected?.locations).toEqual([firstLocation, secondLocation]);
    expect(selectedEdge(document, visible.edges, single.edges[0].id)).toEqual(projected);
    expect(visible.edges.some((edge) => edge.source === sourceFile.id && edge.target === sourceFile.id)).toBe(false);
  });

  it("does not project disabled leaf kinds into file facts", () => {
    const file = mockGraph.nodes.find((node) => node.kind === "file")!;
    const call = mockGraph.edges.find((edge) => edge.kind === "calls")!;
    for (const kind of ["function", "method", "class", "variable", "unresolved"] as const) {
      const source = { ...file, id: `${kind}:hidden`, kind, parent: file.id };
      const target = { ...file, id: "file:target" };
      const filters = defaultFilters();
      filters.nodes[kind] = false;
      const document = { ...mockGraph, nodes: [file, source, target], edges: [{ ...call, source: source.id, target: target.id }] };
      expect(buildVisibleGraph(document, filters, "repository", new Set()).edges).toHaveLength(0);
      expect(buildVisibleGraph({ ...document, edges: [{ ...call, source: target.id, target: source.id }] }, filters, "repository", new Set()).edges).toHaveLength(0);
    }
  });

  it("does not resurrect a collapsed module after hiding modules", () => {
    const file = mockGraph.nodes.find((node) => node.kind === "file")!;
    const module = { ...mockGraph.nodes.find((node) => node.kind === "module")!, parent: file.id };
    const filters = defaultFilters();
    filters.nodes.module = true;
    const document = { ...mockGraph, nodes: [file, module], edges: [] };
    const collapsed = new Set([module.id]);
    expect(buildVisibleGraph(document, filters, "repository", collapsed).nodes.map((node) => node.id)).toContain(module.id);
    filters.nodes.module = false;
    expect(buildVisibleGraph(document, filters, "repository", collapsed).nodes.map((node) => node.id)).not.toContain(module.id);
  });

  it("keeps canonical edge selection through aggregation cardinality changes", () => {
    const file = mockGraph.nodes.find((node) => node.kind === "file")!;
    const target = { ...file, id: "file:target" };
    const call = { ...mockGraph.edges.find((edge) => edge.kind === "calls")!, id: "raw-first", source: file.id, target: target.id };
    const one = { ...mockGraph, nodes: [file, target], edges: [call] };
    const two = { ...one, edges: [call, { ...call, id: "raw-second" }] };
    const id = `calls|${file.id}|${target.id}`;
    const first = buildVisibleGraph(one, defaultFilters(), "repository", new Set());
    const aggregate = buildVisibleGraph(two, defaultFilters(), "repository", new Set());
    expect(first.edges[0]).toMatchObject({ id, count: 1 });
    expect(aggregate.edges[0]).toMatchObject({ id, count: 2 });
    expect(selectedEdge(two, aggregate.edges, id)).toEqual(aggregate.edges[0]);
    expect(selectedEdge(one, first.edges, id)).toEqual(first.edges[0]);
  });

  it("shows modified projected links in changed-only mode but excludes unchanged links", () => {
    const file = mockGraph.nodes.find((node) => node.kind === "file")!;
    const module = mockGraph.nodes.find((node) => node.kind === "module")!;
    const imported = mockGraph.edges.find((edge) => edge.kind === "imports")!;
    const nodes: GraphNode[] = [
      { ...file, id: "file:a", diff: "modified" },
      { ...module, id: "module:a", parent: "file:a", diff: "unchanged" },
      { ...file, id: "file:b", diff: "modified" },
      { ...module, id: "module:b", parent: "file:b", diff: "unchanged" },
    ];
    const filters = defaultFilters();
    filters.changedOnly = true;
    const changed: GraphEdge = { ...imported, source: "module:a", target: "module:b", diff: "modified" };
    const document = { ...mockGraph, entryKey: "module:a", nodes, edges: [changed] };
    expect(buildVisibleGraph(document, filters, "entry", new Set()).edges[0]).toMatchObject({ source: "file:a", target: "file:b", diff: "modified" });
    expect(buildVisibleGraph({ ...document, edges: [{ ...changed, diff: "unchanged" }] }, filters, "entry", new Set()).edges).toHaveLength(0);
    const modifiedModules = nodes.map((node) => node.kind === "module" ? { ...node, diff: "modified" as const } : node);
    const unchanged = { ...changed, id: "unchanged-import", diff: "unchanged" as const };
    expect(buildVisibleGraph({ ...document, nodes: modifiedModules, edges: [unchanged] }, filters, "entry", new Set()).edges).toHaveLength(0);
    expect(buildVisibleGraph({ ...document, nodes: modifiedModules, edges: [unchanged, changed] }, filters, "entry", new Set()).edges)
      .toMatchObject([{ source: "file:a", target: "file:b", diff: "modified", count: changed.count }]);
  });

  it("keeps mixed boundary reasons consistent with resolution in either input order", () => {
    const file = mockGraph.nodes.find((node) => node.kind === "file")!;
    const module = mockGraph.nodes.find((node) => node.kind === "module")!;
    const imported = mockGraph.edges.find((edge) => edge.kind === "imports")!;
    const nodes = [{ ...file, id: "file:a" }, { ...module, id: "module:a", parent: "file:a" }, { ...file, id: "file:b" }, { ...module, id: "module:b", parent: "file:b" }];
    const external: GraphEdge = { ...imported, id: "external", source: "module:a", target: "module:b", resolution: "external", confidence: "heuristic", boundaryReason: "Target is outside the repository." };
    const unresolved: GraphEdge = { ...external, id: "unresolved", resolution: "unresolved", confidence: "unresolved", boundaryReason: "Dynamic target could not be resolved." };
    const document = { ...mockGraph, nodes, edges: [external, unresolved] };
    const forward = buildVisibleGraph(document, defaultFilters(), "repository", new Set()).edges[0];
    const reverse = buildVisibleGraph({ ...document, edges: [unresolved, external] }, defaultFilters(), "repository", new Set()).edges[0];
    expect(forward).toMatchObject({ resolution: "unresolved", confidence: "unresolved", boundaryReason: "Mixed boundary evidence (unresolved)." });
    expect(reverse.boundaryReason).toBe(forward.boundaryReason);
  });

  it("does not project modules excluded by test, diff, or entry scope", () => {
    const imported = mockGraph.edges.find((edge) => edge.kind === "imports")!;
    const sourceFile = mockGraph.nodes.find((node) => node.id === "file:src/main.py")!;
    const sourceModule = mockGraph.nodes.find((node) => node.id === "py:module:src.main")!;
    const dependency = mockGraph.nodes.find((node) => node.id === "py:function:src.rules.winner")!;
    const testModule = { ...sourceModule, id: "test-module", test: true };
    const testVisible = buildVisibleGraph({
      ...mockGraph,
      nodes: [sourceFile, testModule, dependency],
      edges: [{ ...imported, id: "test-import", source: testModule.id, target: dependency.id }],
    }, defaultFilters(), "repository", new Set());
    expect(testVisible.edges).toHaveLength(0);

    const changedFile = { ...sourceFile, diff: "modified" as const };
    const unchangedModule = { ...sourceModule, id: "unchanged-module", qualifiedName: "src.main.unchanged" };
    const changedDependency = { ...dependency, id: "changed-dependency", diff: "modified" as const };
    const changedFilters = defaultFilters();
    changedFilters.changedOnly = true;
    const diffVisible = buildVisibleGraph({
      ...mockGraph,
      nodes: [changedFile, unchangedModule, changedDependency],
      edges: [{ ...imported, id: "unchanged-import", source: unchangedModule.id, target: changedDependency.id }],
    }, changedFilters, "repository", new Set());
    expect(diffVisible.edges).toHaveLength(0);

    const entryFunction = { ...mockGraph.nodes.find((node) => node.id === "py:function:src.main.main")!, parent: sourceFile.id };
    const outOfScopeModule = { ...sourceModule, id: "out-of-scope-module", qualifiedName: "src.main.out_of_scope" };
    const entryCall = mockGraph.edges.find((edge) => edge.kind === "calls" && edge.source === entryFunction.id)!;
    const entryVisible = buildVisibleGraph({
      ...mockGraph,
      entryKey: entryFunction.id,
      nodes: [sourceFile, entryFunction, outOfScopeModule, dependency],
      edges: [
        { ...entryCall, id: "entry-call", target: dependency.id },
        { ...imported, id: "out-of-scope-import", source: outOfScopeModule.id, target: dependency.id },
      ],
    }, defaultFilters(), "entry", new Set());
    expect(entryVisible.edges.some((edge) => edge.kind === "imports")).toBe(false);
  });

  it("keeps aggregate edge counts and locations inspectable", () => {
    const original = mockGraph.edges.find((edge) => edge.kind === "constructs")!;
    const document = { ...mockGraph, edges: [...mockGraph.edges, { ...original, id: "second-construct", target: "py:method:src.game.Game.__init__", count: 2 }] };
    const visible = buildVisibleGraph(document, defaultFilters(), "repository", new Set(["file:src/game.py"]));
    const aggregate = visible.edges.find((edge) => edge.kind === "constructs" && edge.target === "file:src/game.py")!;
    expect(aggregate).toMatchObject({ id: `constructs|${original.source}|file:src/game.py`, count: 3 });
    expect(aggregate.locations).toHaveLength(2);
    expect(selectedEdge(document, visible.edges, aggregate.id)).toEqual(aggregate);
    const flow = neighborhood({ ...document, edges: visible.edges }, "file:src/game.py");
    expect(flow.inbound.length + flow.outbound.length).toBeGreaterThan(0);
    expect(inspectionNeighborhood(document, visible, "file:src/game.py", defaultFilters().relations)).toEqual(flow);
    expect(inspectionNeighborhood(document, visible, "py:method:src.game.Game.__init__", defaultFilters().relations)).toEqual(
      neighborhood(document, "py:method:src.game.Game.__init__", defaultFilters().relations),
    );
  });

  it("keeps entry mode directed from the configured entry", () => {
    const visible = buildVisibleGraph(mockGraph, defaultFilters(), "entry", new Set());
    expect(visible.nodes.some((node) => node.id === mockGraph.entryKey)).toBe(true);
    expect(visible.nodes.some((node) => node.id === "py:class:src.renderer.Renderer")).toBe(false);
  });

  it("keeps entry reachability independent from relationship display filters", () => {
    const filters = defaultFilters();
    const full = buildVisibleGraph(mockGraph, filters, "entry", new Set());
    filters.relations.calls = false;
    const callsHidden = buildVisibleGraph(mockGraph, filters, "entry", new Set());
    expect(callsHidden.nodes.map((node) => node.id)).toEqual(full.nodes.map((node) => node.id));
    expect(callsHidden.edges.some((edge) => edge.kind === "calls")).toBe(false);
  });

  it("falls back to the repository when no entry was detected", () => {
    const document = { ...mockGraph, entryKey: "" };
    const visible = buildVisibleGraph(document, defaultFilters(), "entry", new Set());
    expect(visible.nodes).toEqual(buildVisibleGraph(document, defaultFilters(), "repository", new Set()).nodes);
  });

  it("collapses file boundaries for a large repository overview", () => {
    const document = { ...mockGraph, nodes: [...mockGraph.nodes, ...Array.from({ length: 300 }, (_, index) => ({
      ...mockGraph.nodes.find((node) => node.id === "py:method:src.game.Game.play")!, id: `large:${index}`, parent: "file:src/game.py",
    }))] };
    const collapsed = largeRepositoryOverview(document);
    expect(collapsed).toContain("file:src/game.py");
    expect(buildVisibleGraph(document, defaultFilters(), "repository", collapsed).nodes.some((node) => node.id.startsWith("large:"))).toBe(false);
    expect(selectionFocusTarget(document, "large:1", collapsed)).toBe("file:src/game.py");
    expect(selectionFocusTarget(mockGraph, "py:method:src.game.Game.play", new Set(["file:src/game.py"]))).toBe("py:method:src.game.Game.play");
  });

  it("collapses known external symbols at their package boundary", () => {
    const external = mockGraph.nodes.find((node) => node.id === "external:rich")!;
    const apiCall = mockGraph.edges.find((edge) => edge.kind === "api_calls" && edge.target === external.id)!;
    const document = {
      ...mockGraph,
      nodes: [...mockGraph.nodes, { ...external, id: "external:rich.console", qualifiedName: "rich.console.Console" }],
      edges: [...mockGraph.edges, { ...apiCall, id: "api-rich-console", target: "external:rich.console" }],
    };
    const visible = buildVisibleGraph(document, defaultFilters(), "repository", new Set());
    expect(visible.nodes.filter((node) => node.kind === "external_package")).toEqual([
      expect.objectContaining({ id: "group:external:rich", label: "rich", summary: "2 external symbols" }),
    ]);
    expect(visible.edges.some((edge) => edge.target === "group:external:rich" && edge.count === 2)).toBe(true);
  });

  it("supports fuzzy search and exact inbound/outbound inspection", () => {
    expect(searchNodes(mockGraph.nodes, "gmpl")[0]?.id).toBe("py:method:src.game.Game.play");
    const links = neighborhood(mockGraph, "py:function:src.rules.winner");
    expect(links.inbound.map((edge) => edge.source)).toContain("py:method:src.game.Game.play");
    expect(links.outbound).toHaveLength(1);
  });
});
