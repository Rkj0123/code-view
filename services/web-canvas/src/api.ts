import { mockGraph } from "./mockData";
import { normalizeGraphPayload } from "./apiContract";
import { approveThenStart } from "./launchPolicy";
import { bootstrapCapabilityToken } from "./auth";
import type { DiffEvidence, GitRef, GraphDocument, GraphNode, IndexStatus, LaunchConfiguration, SourceEvidence, SourceRange } from "./types";

const browserHref = typeof window === "undefined" ? "" : window.location.href;
const bootToken = browserHref ? new URL(browserHref).searchParams.get("token") : null;
const retainedToken = typeof window === "undefined" ? null : window.sessionStorage.getItem("code-view.capability");
const live = import.meta.env.VITE_API_MODE === "live" || Boolean(bootToken || retainedToken);
let capabilityToken: string | null = null;

if (typeof window !== "undefined") {
  capabilityToken = bootstrapCapabilityToken(live, browserHref, (url) => window.history.replaceState(window.history.state, "", url), retainedToken);
  if (bootToken) window.sessionStorage.setItem("code-view.capability", bootToken);
}

const request = (path: string, init: RequestInit = {}) => {
  const headers = new Headers(init.headers);
  if (capabilityToken) headers.set("Authorization", `Bearer ${capabilityToken}`);
  return fetch(path, { ...init, headers });
};

export const normalizeLaunchConfiguration = (body: unknown): LaunchConfiguration => {
  const value = body as Partial<LaunchConfiguration> | null;
  if (!value || !Number.isSafeInteger(value.generation) || (value.generation ?? -1) < 0 || typeof value.display !== "string" || !Array.isArray(value.argv) || !value.argv.every((item) => typeof item === "string") || typeof value.cwd !== "string" || !["document", "manual"].includes(value.mode ?? "") || typeof value.allowLaunch !== "boolean" || typeof value.running !== "boolean") {
    throw new Error("Invalid launch configuration response.");
  }
  return value as LaunchConfiguration;
};

export const loadGraph = async (base = "HEAD"): Promise<GraphDocument> => {
  if (!live) return { ...structuredClone(mockGraph), snapshot: `WORKTREE vs ${base}` };
  const response = await request(`/api/v1/graph?view=repository&base=${encodeURIComponent(base)}`);
  if (!response.ok) throw new Error(`Graph request failed: ${response.status}`);
  return normalizeGraphPayload(await response.json());
};

export const loadGitRefs = async (): Promise<GitRef[]> => {
  if (!live) return [{ id: "HEAD", label: "HEAD · main", kind: "branch" }, { id: "HEAD~1", label: "HEAD~1 · previous commit", kind: "commit" }];
  const response = await request("/api/v1/git/refs");
  if (!response.ok) throw new Error(`Git refs request failed: ${response.status}`);
  const body = await response.json() as { branches: Array<{ name: string; commit: string }>; recentCommits: Array<{ commit: string; short: string; subject: string }>; hasHead: boolean };
  return [
    ...(body.hasHead ? [{ id: "HEAD", label: "HEAD", kind: "branch" as const }] : []),
    ...body.branches.map((branch) => ({ id: branch.name, label: `${branch.name} · ${branch.commit.slice(0, 7)}`, kind: "branch" as const })),
    ...body.recentCommits.map((commit) => ({ id: commit.commit, label: `${commit.short} · ${commit.subject}`, kind: "commit" as const })),
  ];
};

export const loadSource = async (node: GraphNode): Promise<SourceEvidence | null> => {
  if (!live) {
    const mocked = mockGraph.nodes.find((item) => item.id === node.id);
    return mocked?.source && mocked.sourceText ? { range: mocked.source, text: mocked.sourceText } : null;
  }
  if (!node.source) return null;
  const query = new URLSearchParams({ path: node.source.path, startLine: String(node.source.start.line), endLine: String(node.source.end.line) });
  const response = await request(`/api/v1/source?${query}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Source request failed: ${response.status}`);
  const body = await response.json() as { path: string; startLine: number; endLine: number; text: string };
  return { range: { path: body.path, start: { line: body.startLine, column: node.source.start.column }, end: { line: body.endLine, column: node.source.end.column } }, text: body.text };
};

export const loadSourceRange = async (range: SourceRange): Promise<SourceEvidence | null> => {
  if (!live) {
    const node = mockGraph.nodes.find((item) => item.source?.path === range.path && item.sourceText);
    return node?.sourceText ? { range, text: node.sourceText } : null;
  }
  const query = new URLSearchParams({ path: range.path, startLine: String(range.start.line), endLine: String(range.end.line) });
  const response = await request(`/api/v1/source?${query}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Source range request failed: ${response.status}`);
  const body = await response.json() as { path: string; startLine: number; endLine: number; text: string };
  return { range: { path: body.path, start: { line: body.startLine, column: range.start.column }, end: { line: body.endLine, column: range.end.column } }, text: body.text };
};

export const loadDiff = async (nodeId: string, base: string): Promise<DiffEvidence | null> => {
  if (!live) {
    const node = mockGraph.nodes.find((item) => item.id === nodeId);
    if (!node || node.diff === "unchanged") return null;
    return { base, state: node.diff, unified: `@@ ${node.source?.path ?? node.qualifiedName} @@\n+ ${node.signature ?? node.label}` };
  }
  const response = await request(`/api/v1/git/diff?node=${encodeURIComponent(nodeId)}&base=${encodeURIComponent(base)}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Diff request failed: ${response.status}`);
  return response.json() as Promise<DiffEvidence>;
};

export const loadLaunchConfiguration = async (): Promise<LaunchConfiguration | null> => {
  if (!live) return { generation: mockGraph.revision, display: "python -m src.main", argv: ["python", "-m", "src.main"], cwd: ".", mode: "document", allowLaunch: false, running: false };
  const response = await request("/api/v1/launch");
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Launch configuration request failed: ${response.status}`);
  return normalizeLaunchConfiguration(await response.json());
};

export const setLaunchRunning = async (action: "start" | "stop"): Promise<LaunchConfiguration> => {
  if (!live) throw new Error("Launching is disabled in demo mode.");
  const response = await request(`/api/v1/launch/${action}`, { method: "POST" });
  if (!response.ok) throw new Error(`Launch ${action} failed: ${response.status}`);
  return normalizeLaunchConfiguration(await response.json());
};

const approveLaunch = async (displayedGeneration: number) => {
  if (!live) throw new Error("Launching is disabled in demo mode.");
  const response = await request("/api/v1/launch/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ schemaVersion: "code-view.launch-approval/v2", generation: displayedGeneration }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null) as { error?: string } | null;
    throw new Error(detail?.error ?? `Launch approval failed: ${response.status}`);
  }
};

export const approveAndStartLaunch = (displayedGeneration: number) => approveThenStart(() => approveLaunch(displayedGeneration), () => setLaunchRunning("start"));

export const normalizeIndexStatus = (body: unknown): IndexStatus => {
  const value = body as Partial<IndexStatus> | null;
  if (!value || !Number.isSafeInteger(value.generation) || (value.generation ?? -1) < 0 || !["empty", "indexing", "ready", "error"].includes(value.state ?? "") || !(value.lastError === null || typeof value.lastError === "string")) {
    throw new Error("Invalid index status response.");
  }
  return value as IndexStatus;
};

export const loadStatus = async (): Promise<IndexStatus> => {
  if (!live) return { generation: mockGraph.revision, state: "ready", lastError: null };
  const response = await request("/api/v1/status");
  if (!response.ok) throw new Error(`Status request failed: ${response.status}`);
  return normalizeIndexStatus(await response.json());
};

export const openInEditor = async (source: SourceRange): Promise<{ ok: boolean; message: string }> => {
  if (!live) return { ok: false, message: "Editor integration is disabled in demo mode. Copy the location and open it manually." };
  const segments = source.path.split("/");
  if (!source.path || source.path.startsWith("/") || segments.includes("..") || source.start.line < 1 || source.start.column < 1) {
    return { ok: false, message: "This source location is invalid and was not sent to the host." };
  }
  const response = await request("/api/v1/editor/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: source.path, line: source.start.line, column: source.start.column }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null) as { message?: string } | null;
    return { ok: false, message: detail?.message ?? "Editor integration is unavailable. Configure an editor command on the local host, or copy the location." };
  }
  return { ok: true, message: "Opened in your configured editor." };
};

export const usingMockApi = !live;
