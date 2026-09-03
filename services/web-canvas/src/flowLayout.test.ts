import cytoscape from "cytoscape";
import { describe, expect, it } from "vitest";
import { analyzerFixture } from "../evals/fixtures";
import { normalizeGraphPayload } from "./apiContract";
import { buildFlowScene } from "./flowLayout";
import { buildVisibleGraph, defaultFilters } from "./graphModel";

const fixture = (tenFiles = false) => {
  const document = normalizeGraphPayload({ generation: 1, graph: analyzerFixture(tenFiles).graph });
  return buildVisibleGraph(document, defaultFilters(), "repository", new Set());
};

describe("readable file rows and selected flow", () => {
  const graph = fixture(true);
  it("keeps all ten files and exact relationships in a deterministic, non-overlapping overview", () => {
    const scene = buildFlowScene(graph);
    expect(scene.graph.nodes.filter((node) => node.kind === "file")).toHaveLength(10);
    expect(scene.graph.edges).toBe(graph.edges);
    expect(scene.cards.every((card, index) => scene.cards.every((other, otherIndex) => index === otherIndex || card.x + card.width <= other.x || other.x + other.width <= card.x || card.y + card.height <= other.y || other.y + other.height <= card.y))).toBe(true);
    expect(buildFlowScene(graph).positions).toEqual(scene.positions);
    const cy = cytoscape({ headless: true, elements: scene.elements });
    expect(cy.edges().map((edge) => edge.id()).sort()).toEqual(graph.edges.map((edge) => edge.id).sort());
    expect(cy.nodes(":parent").connectedEdges()).toHaveLength(0);
    cy.destroy();
  });
  it("traces all nine outgoing files and callers left-to-right without changing evidence", () => {
    const selected = graph.nodes.find((node) => node.qualifiedName === "main.main")!;
    const scene = buildFlowScene(graph, selected.id, "selection");
    const expected = graph.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id);
    expect(scene.graph.edges).toEqual(expected);
    expect(expected.filter((edge) => edge.source === selected.id && edge.kind === "calls")).toHaveLength(9);
    expect(Math.max(...scene.cards.map((card) => card.y + card.height)) - Math.min(...scene.cards.map((card) => card.y))).toBeLessThanOrEqual(600);
    for (const edge of expected) {
      const rendered = scene.elements.find((element) => element.data.id === edge.id)!;
      expect(rendered.data.originalSource).toBe(edge.source);
      expect(rendered.data.originalTarget).toBe(edge.target);
      expect(rendered.data.locations).toBe(edge.locations);
      expect(scene.positions.get(String(rendered.data.source))!.x).toBeLessThan(scene.positions.get(String(rendered.data.target))!.x);
      for (const endpoint of [rendered.data.source, rendered.data.target]) {
        const symbol = scene.elements.find((element) => element.data.id === endpoint)!;
        const header = scene.elements.find((element) => element.classes?.includes("compact-title") && element.data.parent === symbol.data.parent);
        if (header) expect(header.position!.y + 10).toBeLessThan(symbol.position!.y - 14);
      }
    }
    expect(buildFlowScene(graph, selected.id, "all").graph.edges).toEqual(graph.edges);
  });
  it("flattens function-local rows but keeps every real Python fact and leaf endpoint", () => {
    const real = fixture();
    const scene = buildFlowScene(real);
    const cy = cytoscape({ headless: true, elements: scene.elements });
    expect(scene.graph.nodes).toEqual(real.nodes);
    expect(scene.graph.edges).toEqual(real.edges);
    expect(cy.edges()).toHaveLength(real.edges.length);
    expect(cy.nodes(":parent").connectedEdges()).toHaveLength(0);
    const local = real.nodes.find((node) => node.qualifiedName === "tictactoe.game.Game.play.$parameter.position")!;
    const row = cy.nodes().filter((node) => node.data("originalId") === local.id).first();
    expect(row.data("displayLabel")).toBe("play.position");
    expect(cy.getElementById(row.data("parent")).data("kind")).toBe("class");
    cy.destroy();
    const isolated = buildFlowScene({ ...real, edges: [] }, local.id, "selection");
    const present = new Set(isolated.graph.nodes.map((node) => node.id));
    expect(isolated.graph.nodes.every((node) => !node.parent || present.has(node.parent))).toBe(true);
  });
  it("allows exact inspection of every edge, and gracefully handles empty or stale selections", () => {
    for (const edge of graph.edges) {
      expect(buildFlowScene(graph, edge.id, "selection").graph.edges).toEqual([edge]);
    }
    expect(buildFlowScene(graph, "missing", "selection").scope).toBe("all");
    expect(buildFlowScene({ nodes: [], edges: [] }).elements).toEqual([]);
  });
});
