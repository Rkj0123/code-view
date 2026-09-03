import { nodeKinds, relationKinds, type CanvasPreferences, type GraphDocument, type GraphEdge, type GraphFilters, type GraphNode, type RelationKind, type ViewMode } from "./types";

export const defaultFilters = (): GraphFilters => ({
  nodes: Object.fromEntries(nodeKinds.map((kind) => [kind, kind !== "directory" && kind !== "module" && kind !== "package"])) as GraphFilters["nodes"],
  relations: Object.fromEntries(relationKinds.map((kind) => [kind, kind !== "test_covers"])) as GraphFilters["relations"],
  includeTests: false,
  changedOnly: false,
});

export const filtersFromPreferences = (preferences: CanvasPreferences): GraphFilters => {
  const filters = defaultFilters();
  filters.nodes.module = preferences.showModules;
  filters.nodes.package = preferences.showModules;
  filters.includeTests = preferences.includeTests;
  filters.relations = Object.fromEntries(relationKinds.map((kind) => [kind, preferences.relationships.includes(kind)])) as GraphFilters["relations"];
  return filters;
};

const subsequenceScore = (query: string, value: string) => {
  const normalized = value.toLocaleLowerCase();
  if (normalized === query) return 0;
  if (normalized.startsWith(query)) return 1;
  let queryIndex = 0;
  let start = -1;
  let end = -1;
  for (let index = 0; index < normalized.length && queryIndex < query.length; index += 1) {
    if (normalized[index] === query[queryIndex]) {
      if (start < 0) start = index;
      end = index;
      queryIndex += 1;
    }
  }
  return queryIndex === query.length ? 2 + (end - start - query.length) + start / 100 : Number.POSITIVE_INFINITY;
};

export const searchNodes = (nodes: GraphNode[], query: string) => {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return nodes;
  return nodes
    .map((node) => ({ node, score: Math.min(subsequenceScore(normalized, node.label), 100 + subsequenceScore(normalized, node.qualifiedName), 200 + subsequenceScore(normalized, node.kind)) }))
    .filter(({ score }) => Number.isFinite(score))
    .sort((left, right) => left.score - right.score || left.node.qualifiedName.localeCompare(right.node.qualifiedName) || left.node.id.localeCompare(right.node.id))
    .map(({ node }) => node);
};

export const neighborhood = (document: GraphDocument, nodeId: string, relations?: GraphFilters["relations"]) => ({
  inbound: document.edges.filter((edge) => edge.target === nodeId && (!relations || relations[edge.kind])),
  outbound: document.edges.filter((edge) => edge.source === nodeId && (!relations || relations[edge.kind])),
});

export const inspectionNeighborhood = (document: GraphDocument, display: VisibleGraph, nodeId: string, relations: GraphFilters["relations"]) =>
  neighborhood(
    { ...document, edges: display.nodes.some((node) => node.id === nodeId) ? display.edges : document.edges },
    nodeId,
    relations,
  );

const entryReachable = (document: GraphDocument, allowedEdges: GraphEdge[]) => {
  const ids = new Set([document.entryKey]);
  let frontier = [document.entryKey];
  while (frontier.length) {
    const next = new Set(allowedEdges.filter((edge) => frontier.includes(edge.source)).map((edge) => edge.target));
    frontier = [...next].filter((id) => !ids.has(id));
    frontier.forEach((id) => ids.add(id));
  }
  return ids;
};

const ancestorsOf = (nodeId: string, byId: Map<string, GraphNode>) => {
  const ancestors: string[] = [];
  let parent = byId.get(nodeId)?.parent;
  while (parent) {
    ancestors.push(parent);
    parent = byId.get(parent)?.parent;
  }
  return ancestors;
};

const collapsedRepresentative = (node: GraphNode, collapsed: Set<string>, byId: Map<string, GraphNode>) => {
  let parent = node.parent;
  while (parent) {
    if (collapsed.has(parent)) return parent;
    parent = byId.get(parent)?.parent;
  }
  return node.id;
};

export interface VisibleGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export const largeRepositoryOverview = (document: GraphDocument, threshold = 300) =>
  document.nodes.length > threshold
    ? new Set(document.nodes.filter((node) => node.kind === "file").map((node) => node.id))
    : new Set<string>();

export const selectionFocusTarget = (document: GraphDocument, nodeId: string, collapsed: Set<string>, expansionLimit = 150) => {
  const byId = new Map(document.nodes.map((node) => [node.id, node]));
  const boundary = ancestorsOf(nodeId, byId).find((id) => collapsed.has(id));
  if (!boundary) return nodeId;
  let descendants = 0;
  for (const node of document.nodes) {
    if (ancestorsOf(node.id, byId).includes(boundary) && ++descendants > expansionLimit) return boundary;
  }
  return nodeId;
};

export const selectedEdge = (document: GraphDocument, displayEdges: GraphEdge[], id?: string) =>
  displayEdges.find((edge) => edge.id === id) ?? document.edges.find((edge) => edge.id === id);

export function buildVisibleGraph(document: GraphDocument, filters: GraphFilters, mode: ViewMode, collapsed: Set<string>): VisibleGraph {
  const byId = new Map(document.nodes.map((node) => [node.id, node]));
  const allowedEdges = document.edges.filter((edge) => filters.relations[edge.kind]);
  const entryIds = mode === "entry" && document.entryKey ? entryReachable(document, document.edges) : null;
  const candidateIds = new Set<string>();

  document.nodes.forEach((node) => {
    if ((!node.test || filters.includeTests) && filters.nodes[node.kind] && (!filters.changedOnly || node.diff !== "unchanged") && (!entryIds || entryIds.has(node.id))) {
      candidateIds.add(node.id);
      ancestorsOf(node.id, byId).forEach((id) => {
        const ancestor = byId.get(id);
        if (ancestor && filters.nodes[ancestor.kind] && (!ancestor.test || filters.includeTests)) candidateIds.add(id);
      });
    }
  });

  const externalGroups = new Map<string, GraphNode>();
  const externalRepresentative = new Map<string, string>();
  candidateIds.forEach((id) => {
    const node = byId.get(id);
    if (node?.kind !== "external_package") return;
    const packageName = node.qualifiedName.split(".", 1)[0] || node.label;
    const groupId = `group:external:${packageName}`;
    externalRepresentative.set(id, groupId);
    const current = externalGroups.get(groupId);
    externalGroups.set(groupId, current ? {
      ...current,
      summary: `${Number.parseInt(current.summary ?? "1", 10) + 1}`,
      diff: current.diff === node.diff ? current.diff : "modified",
    } : {
      ...node,
      id: groupId,
      label: packageName,
      qualifiedName: packageName,
      parent: undefined,
      summary: "1",
      boundaryReason: "Known symbols are collapsed at their external package boundary.",
    });
  });
  externalGroups.forEach((node, id) => {
    const count = Number.parseInt(node.summary ?? "1", 10);
    externalGroups.set(id, { ...node, summary: `${count} external symbol${count === 1 ? "" : "s"}` });
  });

  const representative = new Map<string, string>();
  candidateIds.forEach((id) => {
    const node = byId.get(id);
    if (node) representative.set(id, externalRepresentative.get(id) ?? collapsedRepresentative(node, collapsed, byId));
  });

  const visibleIds = new Set(representative.values());
  const nodes = document.nodes
    .filter((node) => visibleIds.has(node.id) && !externalRepresentative.has(node.id))
    .map((node) => {
      const parent = ancestorsOf(node.id, byId).find((id) => visibleIds.has(id));
      return parent === node.parent ? node : { ...node, parent };
    });
  externalGroups.forEach((node) => { if (visibleIds.has(node.id)) nodes.push(node); });

  const aggregated = new Map<string, GraphEdge>();
  allowedEdges.forEach((edge) => {
    const source = representative.get(edge.source);
    const target = representative.get(edge.target);
    if (!source || !target || source === target) return;
    const id = `${edge.kind}|${source}|${target}`;
    const current = aggregated.get(id);
    aggregated.set(id, current ? {
      ...current,
      id,
      count: current.count + edge.count,
      locations: [...(current.locations ?? []), ...(edge.locations ?? [])],
    } : { ...edge, source, target });
  });

  return { nodes, edges: [...aggregated.values()] };
}

export const relationLabel = (kind: RelationKind) => kind.replaceAll("_", " ");

export const exportDocument = (document: GraphDocument, visible: VisibleGraph) => JSON.stringify({
  schemaVersion: document.schemaVersion,
  project: document.project,
  snapshot: document.snapshot,
  revision: document.revision,
  nodes: visible.nodes,
  edges: visible.edges,
}, null, 2);
