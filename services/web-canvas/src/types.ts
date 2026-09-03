export const nodeKinds = [
  "directory",
  "file",
  "module",
  "package",
  "class",
  "function",
  "method",
  "variable",
  "external_package",
  "unresolved",
] as const;

export const relationKinds = [
  "contains",
  "imports",
  "calls",
  "inherits",
  "constructs",
  "reads",
  "writes",
  "decorates",
  "type_uses",
  "api_calls",
  "test_covers",
] as const;

export type NodeKind = (typeof nodeKinds)[number];
export type RelationKind = (typeof relationKinds)[number];
export type DiffState = "added" | "modified" | "removed" | "unchanged";
export type Resolution = "resolved" | "unresolved" | "external";
export type ViewMode = "repository" | "entry";
export type Theme = "light" | "dark";

export interface CanvasPreferences {
  initialView: ViewMode;
  showModules: boolean;
  includeTests: boolean;
  relationships: RelationKind[];
  gitBase: string;
}

export interface SourceRange {
  path: string;
  start: { line: number; column: number };
  end: { line: number; column: number };
}

export interface GraphNode {
  id: string;
  kind: NodeKind;
  label: string;
  qualifiedName: string;
  parent?: string;
  resolution: Resolution;
  test: boolean;
  stale?: boolean;
  entry?: boolean;
  diff: DiffState;
  source?: SourceRange;
  sourceText?: string;
  signature?: string;
  summary?: string;
  boundaryReason?: string;
}

export interface GraphEdge {
  id: string;
  kind: RelationKind;
  source: string;
  target: string;
  resolution: Resolution;
  confidence: "exact" | "heuristic" | "unresolved";
  count: number;
  diff: DiffState;
  locations?: SourceRange[];
  boundaryReason?: string;
}

export interface GraphDocument {
  schemaVersion: 1;
  project: string;
  snapshot: string;
  revision: number;
  generatedAt: string;
  entryKey: string;
  preferences: CanvasPreferences;
  nodes: GraphNode[];
  edges: GraphEdge[];
  diagnostics: { severity: "info" | "warning" | "error"; message: string; path?: string }[];
}

export interface GraphFilters {
  nodes: Record<NodeKind, boolean>;
  relations: Record<RelationKind, boolean>;
  includeTests: boolean;
  changedOnly: boolean;
}

export interface GitRef {
  id: string;
  label: string;
  kind: "branch" | "commit";
}

export interface SourceEvidence {
  range: SourceRange;
  text: string;
}

export interface DiffEvidence {
  base: string;
  state: DiffState;
  unified: string;
}

export interface LaunchConfiguration {
  generation: number;
  display: string;
  argv: string[];
  cwd: string;
  mode: "document" | "manual";
  allowLaunch: boolean;
  running: boolean;
}

export interface IndexStatus {
  generation: number;
  state: "empty" | "indexing" | "ready" | "error";
  lastError: string | null;
}

export interface SavedView {
  id: string;
  name: string;
  mode: ViewMode;
  filters: GraphFilters;
  collapsed: string[];
}
