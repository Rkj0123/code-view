import { nodeKinds, relationKinds, type DiffState, type GraphDocument, type NodeKind, type RelationKind, type Resolution, type SourceRange } from "./types";

interface CanonicalNode {
  id: string;
  kind: "repository" | "package" | "file" | "module" | "class" | "function" | "method" | "variable" | "external" | "unresolved";
  language: string | null;
  name: string;
  qualifiedName: string;
  path: string | null;
  range: Omit<SourceRange, "path"> | null;
  signature: string | null;
  docstring: string | null;
  external: boolean;
  unresolved: boolean;
  reason: string | null;
  modifiers: string[];
}

interface CanonicalEdge {
  id: string;
  kind: RelationKind;
  language: string;
  source: string;
  target: string;
  path: string | null;
  range: Omit<SourceRange, "path"> | null;
  reason: string | null;
}

interface CanonicalGraph {
  schemaVersion: string;
  project: { root: "."; languages: string[]; entryNodeId: string | null; entryReachableNodeIds: string[] };
  nodes: CanonicalNode[];
  edges: CanonicalEdge[];
  diagnostics: Array<{ code: string; severity: "info" | "warning" | "error"; message: string; path: string | null; range: Omit<SourceRange, "path"> | null }>;
}

interface Overlay<T> {
  added?: Array<{ id: string; current: T }>;
  modified?: Array<{ id: string; current: T; base: T }>;
  removed?: Array<{ id: string; base: T }>;
  unchanged?: Array<{ id: string; current: T }>;
}

interface HostGraphResponse {
  generation: number;
  comparison: null | { base?: string; target?: string; nodes?: Overlay<CanonicalNode>; edges?: Overlay<CanonicalEdge>; edgeLocations?: Record<string, SourceRange[]> };
  projectName: string;
  config: {
    includeTests?: boolean;
    modules?: "hide" | "show";
    initialView?: "entry-focus" | "whole-repo";
    relationships?: string[];
    gitBase?: string;
  };
  graph: CanonicalGraph;
  generatedAt?: string;
}

const nodeKind = (value: CanonicalNode["kind"]): NodeKind => {
  const derived = value === "external" ? "external_package" : value === "repository" ? "directory" : value;
  if ((nodeKinds as readonly string[]).includes(derived)) return derived as NodeKind;
  throw new Error(`Unsupported node kind: ${value}`);
};

const relationKind = (value: string): RelationKind => {
  if ((relationKinds as readonly string[]).includes(value)) return value as RelationKind;
  throw new Error(`Unsupported relationship kind: ${value}`);
};

const resolution = (item: { external?: boolean; unresolved?: boolean }, kind?: string): Resolution => item.unresolved || kind === "unresolved" ? "unresolved" : item.external || kind === "external" ? "external" : "resolved";

const overlayStates = <T,>(overlay?: Overlay<T>) => {
  const states = new Map<string, DiffState>();
  (["added", "modified", "removed", "unchanged"] as const).forEach((state) => overlay?.[state]?.forEach((item) => states.set(item.id, state)));
  return states;
};

export function normalizeGraphPayload(value: unknown): GraphDocument {
  const payload = value as HostGraphResponse;
  const graph = payload?.graph;
  if (!graph || graph.schemaVersion !== "code-view.graph/v1") throw new Error(`Unsupported graph schemaVersion: ${graph?.schemaVersion ?? "missing"}`);
  if (!graph.project || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || !Array.isArray(graph.diagnostics) || typeof payload.generation !== "number") throw new Error("Malformed graph response");

  const nodes = [...graph.nodes];
  payload.comparison?.nodes?.removed?.forEach(({ base }) => { if (!nodes.some((node) => node.id === base.id)) nodes.push(base); });
  const edges = [...graph.edges];
  payload.comparison?.edges?.removed?.forEach(({ base }) => { if (!edges.some((edge) => edge.id === base.id)) edges.push(base); });
  const parentByChild = new Map<string, string>();
  edges.forEach((edge) => { if (edge.kind === "contains") parentByChild.set(edge.target, edge.source); });
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const fileByPath = new Map(nodes.filter((node) => node.kind === "file" && node.path).map((node) => [node.path!, node.id]));
  const nodeDiff = overlayStates(payload.comparison?.nodes);
  const edgeDiff = overlayStates(payload.comparison?.edges);
  const configuredRelationships = payload.config?.relationships?.map(relationKind) ?? relationKinds.filter((kind) => kind !== "test_covers");

  return {
    schemaVersion: 1,
    project: payload.projectName,
    snapshot: `${payload.comparison?.target ?? "WORKTREE"} vs ${payload.comparison?.base ?? "HEAD"}`,
    revision: payload.generation,
    generatedAt: payload.generatedAt ?? "",
    entryKey: graph.project.entryNodeId ?? "",
    preferences: {
      initialView: payload.config?.initialView === "whole-repo" ? "repository" : "entry",
      showModules: payload.config?.modules === "show",
      includeTests: payload.config?.includeTests === true,
      relationships: configuredRelationships,
      gitBase: typeof payload.config?.gitBase === "string" && payload.config.gitBase ? payload.config.gitBase : "HEAD",
    },
    nodes: nodes.map((node) => {
      const modifiers = new Set(node.modifiers);
      const sourceStart = node.range?.start ?? { line: 1, column: 1 };
      const rawSourceEnd = node.range?.end ?? { line: 400, column: 1 };
      const sourceEnd = rawSourceEnd.line - sourceStart.line < 400
        ? rawSourceEnd
        : { line: sourceStart.line + 399, column: 1 };
      return {
        id: node.id,
        kind: nodeKind(node.kind),
        label: node.name,
        qualifiedName: node.qualifiedName,
        parent: parentByChild.get(node.id) ?? (node.unresolved && node.path ? fileByPath.get(node.path) : undefined),
        resolution: resolution(node, node.kind),
        test: modifiers.has("test"),
        stale: modifiers.has("stale") || undefined,
        entry: node.id === graph.project.entryNodeId || modifiers.has("entry") || undefined,
        diff: nodeDiff.get(node.id) ?? "unchanged",
        source: node.path ? {
          path: node.path,
          start: sourceStart,
          end: sourceEnd,
        } : undefined,
        signature: node.signature ?? undefined,
        summary: node.docstring ?? undefined,
        boundaryReason: node.reason ?? undefined,
      };
    }),
    edges: edges.map((edge) => {
      const target = nodesById.get(edge.target);
      const occurrence = edge.path && edge.range ? { path: edge.path, ...edge.range } : undefined;
      const locations = payload.comparison?.edgeLocations?.[edge.id] ?? (occurrence ? [occurrence] : []);
      return {
        id: edge.id,
        kind: relationKind(edge.kind),
        source: edge.source,
        target: edge.target,
        resolution: resolution(target ?? {}),
        confidence: target?.unresolved ? "unresolved" : edge.reason ? "heuristic" : "exact",
        count: Math.max(1, locations.length),
        diff: edgeDiff.get(edge.id) ?? "unchanged",
        locations,
        boundaryReason: edge.reason ?? undefined,
      };
    }),
    diagnostics: graph.diagnostics.map((diagnostic) => ({ severity: diagnostic.severity, message: diagnostic.message, path: diagnostic.path ?? undefined })),
  };
}
