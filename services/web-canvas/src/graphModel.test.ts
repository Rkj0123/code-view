import { describe, expect, it } from "vitest";
import { mockGraph } from "./mockData";
import { buildVisibleGraph, defaultFilters, filtersFromPreferences, inspectionNeighborhood, largeRepositoryOverview, neighborhood, searchNodes, selectedEdge, selectionFocusTarget } from "./graphModel";

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
