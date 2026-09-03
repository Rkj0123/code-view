import type { ElementDefinition, Position } from "cytoscape";
import type { VisibleGraph } from "./graphModel";
import type { GraphNode } from "./types";

export type FlowScope = "all" | "selection";
export interface FlowScene {
  scope: FlowScope;
  graph: VisibleGraph;
  elements: ElementDefinition[];
  positions: Map<string, Position>;
  navigation: string[];
  cards: { id: string; x: number; y: number; width: number; height: number }[];
}

export const groupKinds = new Set(["directory", "file", "module", "package", "class"]);
type DisplayNode = GraphNode & { originalId: string; lane: number; owner?: string };
const rowHeight = 28;
const rowGap = 8;
const padding = 12;
const rowWidth = 164;
const compare = (a: GraphNode, b: GraphNode) =>
  (a.source?.start.line ?? 0) - (b.source?.start.line ?? 0) || a.qualifiedName.localeCompare(b.qualifiedName) || a.id.localeCompare(b.id);

/** Only presentation changes: exact IDs, relationship evidence and graph facts survive. */
export function buildFlowScene(graph: VisibleGraph, selectedId?: string, requestedScope: FlowScope = "all"): FlowScene {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const selectedEdge = graph.edges.find((edge) => edge.id === selectedId);
  const selectedNode = byId.get(selectedId ?? "");
  const scope = requestedScope === "selection" && (selectedNode || selectedEdge) ? "selection" : "all";
  const domain = new Set<string>(selectedNode ? [selectedNode.id] : selectedEdge ? [selectedEdge.source] : []);
  const children = new Map<string, string[]>();
  graph.nodes.forEach((node) => {
    if (node.parent) {
      const siblings = children.get(node.parent) ?? [];
      siblings.push(node.id);
      children.set(node.parent, siblings);
    }
  });
  if (scope === "selection" && selectedNode) {
    const queue = [...domain];
    for (let index = 0; index < queue.length; index += 1) {
      for (const id of children.get(queue[index]) ?? []) if (!domain.has(id)) { domain.add(id); queue.push(id); }
    }
  }
  const edges = scope === "all" ? graph.edges : selectedEdge ? [selectedEdge] : graph.edges.filter((edge) => domain.has(edge.source) || domain.has(edge.target));
  const display = new Map<string, DisplayNode>();
  const originalIds = new Set<string>();
  const visualId = (id: string, lane: number) => `flow:${lane}:${id}`;
  const wrapperId = (id: string) => `group:${id}`;
  const include = (id: string, lane: number, visiting = new Set<string>()): string | undefined => {
    const node = byId.get(id);
    if (!node || visiting.has(id)) return undefined;
    const key = visualId(id, lane);
    if (display.has(key)) return key;
    visiting.add(id);
    let parent = node.parent;
    const seen = new Set<string>();
    while (parent && !seen.has(parent) && !groupKinds.has(byId.get(parent)?.kind ?? "")) {
      seen.add(parent);
      parent = byId.get(parent)?.parent;
    }
    const owner = parent ? include(parent, lane, visiting) : undefined;
    // Local variables are rows, not nested boxes. Their labels retain the owner.
    const immediate = node.parent ? byId.get(node.parent) : undefined;
    if (immediate && !groupKinds.has(immediate.kind)) include(immediate.id, lane, visiting);
    const label = immediate && !groupKinds.has(immediate.kind) ? `${immediate.label}.${node.label}` : node.label;
    display.set(key, { ...node, id: key, label, owner, originalId: id, lane });
    originalIds.add(id);
    return key;
  };
  if (scope === "all") graph.nodes.forEach((node) => include(node.id, 0));
  else domain.forEach((id) => include(id, 1));
  const edgeElements: ElementDefinition[] = edges.map((edge) => {
    const sourceLane = scope === "all" || !domain.has(edge.source) ? 0 : 1;
    const targetLane = scope === "all" ? 0 : domain.has(edge.target) ? 1 : 2;
    return {
      data: { ...edge, source: include(edge.source, sourceLane), target: include(edge.target, targetLane), originalId: edge.id, originalSource: edge.source, originalTarget: edge.target, relation: edge.kind.replaceAll("_", " "), direction: sourceLane < targetLane ? "rightward" : "horizontal" },
      classes: selectedId === edge.id ? "is-selected" : scope === "selection" && !domain.has(edge.source) ? "is-inbound" : scope === "selection" && !domain.has(edge.target) ? "is-outbound" : "",
    };
  }).filter((edge) => edge.data.source && edge.data.target);
  const rows = new Map<string, DisplayNode[]>();
  display.forEach((node) => {
    if (node.owner) {
      const items = rows.get(node.owner) ?? [];
      items.push(node);
      rows.set(node.owner, items);
    }
  });
  rows.forEach((items) => items.sort(compare));
  const compact = new Set([...rows].filter(([id, items]) => display.get(id)?.kind === "file" && items.length === 1 && !rows.has(items[0].id)).map(([id]) => id));
  const roots = [...display.values()].filter((node) => !node.owner).sort((a, b) => a.lane - b.lane || a.qualifiedName.localeCompare(b.qualifiedName) || a.id.localeCompare(b.id));
  const dimensions = new Map<string, { width: number; height: number }>();
  const measure = (id: string): { width: number; height: number } => {
    const cached = dimensions.get(id);
    if (cached) return cached;
    const sizes = (rows.get(id) ?? []).map((node) => measure(node.id));
    const size = compact.has(id) ? { width: 176, height: 56 } : sizes.length
      ? { width: Math.max(rowWidth, ...sizes.map((item) => item.width)) + 2 * padding, height: rowHeight + rowGap + sizes.reduce((sum, item) => sum + item.height + rowGap, 0) - rowGap + 2 * padding }
      : { width: rowWidth, height: rowHeight };
    dimensions.set(id, size);
    return size;
  };
  roots.forEach((node) => measure(node.id));
  const positions = new Map<string, Position>();
  const widths = new Map<string, number>();
  const cards: FlowScene["cards"] = [];
  const place = (id: string, x: number, y: number) => {
    const size = measure(id);
    const items = rows.get(id) ?? [];
    if (compact.has(id)) {
      // Keep the file header above the symbol so connectors cannot cross its label.
      positions.set(id, { x: x + 88, y: y + 12 });
      widths.set(id, 164);
      positions.set(items[0].id, { x: x + 88, y: y + 40 });
      widths.set(items[0].id, 164);
      return;
    }
    positions.set(id, { x: x + size.width / 2, y: y + (items.length ? padding : 0) + rowHeight / 2 });
    widths.set(id, size.width - (items.length ? 2 * padding : 0));
    let cursor = y + padding + rowHeight + rowGap;
    for (const child of items) {
      place(child.id, x + (size.width - measure(child.id).width) / 2, cursor);
      cursor += measure(child.id).height + rowGap;
    }
  };
  const cardWidth = Math.max(rowWidth, ...roots.map((node) => measure(node.id).width));
  const columns = scope === "selection" ? 3 : Math.min(4, Math.max(1, Math.ceil(Math.sqrt(roots.length))));
  const bottoms = Array.from({ length: columns }, () => scope === "selection" ? 48 : 0);
  const gap = (id: string) => compact.has(id) ? 8 : 32;
  const heights = [0, 1, 2].map((lane) => roots.filter((node) => node.lane === lane).reduce((sum, node) => sum + measure(node.id).height + gap(node.id), 0));
  if (scope === "selection") bottoms.forEach((_, lane) => { bottoms[lane] += (Math.max(...heights) - heights[lane]) / 2; });
  roots.forEach((node, index) => {
    // ponytail: stable four-column overview; the explicit selection view handles dense wiring.
    const column = scope === "selection" ? node.lane : index % columns;
    const size = measure(node.id);
    const x = column * (cardWidth + 92) + (cardWidth - size.width) / 2;
    const y = bottoms[column];
    place(node.id, x, y);
    cards.push({ id: node.id, x, y, ...size });
    bottoms[column] += size.height + gap(node.id);
  });
  const nodeElements: ElementDefinition[] = [];
  display.forEach((node) => {
    const owner = node.owner ? wrapperId(node.owner) : undefined;
    if (rows.has(node.id)) nodeElements.push({
      data: { id: wrapperId(node.id), parent: owner, ui: true, kind: node.kind },
      classes: `group-boundary${compact.has(node.id) ? " is-compact" : ""}`, selectable: false,
    });
    // Group titles are leaf endpoints too. No edge attaches to an overlapping compound.
    nodeElements.push({
      data: { ...node, parent: rows.has(node.id) ? wrapperId(node.id) : owner, width: widths.get(node.id), labelWidth: widths.get(node.id)! - 8, displayLabel: `${node.diff === "unchanged" ? "" : node.diff === "added" ? "+ " : node.diff === "removed" ? "− " : "~ "}${node.label}` },
      position: positions.get(node.id),
      classes: `${node.originalId === selectedId ? "is-selected " : ""}${rows.has(node.id) ? "group-title" : ""}${compact.has(node.id) ? " compact-title" : ""}`,
    });
  });
  if (scope === "selection") ["INCOMING · USED BY", "SELECTED", "OUTGOING · USES"].forEach((label, lane) => {
    const id = `flow-ui:lane:${lane}`;
    const position = { x: lane * (cardWidth + 92) + cardWidth / 2, y: 14 };
    positions.set(id, position);
    nodeElements.push({ data: { id, displayLabel: label, ui: true }, position, classes: "lane-label", selectable: false, grabbable: false });
  });
  return {
    scope,
    graph: { nodes: graph.nodes.filter((node) => originalIds.has(node.id)), edges },
    elements: [...nodeElements, ...edgeElements],
    positions,
    navigation: [...display.values()].sort((a, b) => (positions.get(a.id)!.x - positions.get(b.id)!.x) || (positions.get(a.id)!.y - positions.get(b.id)!.y) || compare(a, b)).map((node) => node.originalId).filter((id, index, ids) => ids.indexOf(id) === index),
    cards,
  };
}
