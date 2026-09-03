import { BookOpen, Braces, Check, ChevronsLeftRight, Filter, Focus, GitCompareArrows, Image, Maximize2, Moon, PanelRight, RefreshCw, RotateCcw, Search, Share2, Sparkles, Sun } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { loadGitRefs, loadGraph, loadLaunchConfiguration, loadStatus, usingMockApi } from "./api";
import { buildVisibleGraph, defaultFilters, exportDocument, filtersFromPreferences, largeRepositoryOverview, selectionFocusTarget, type VisibleGraph } from "./graphModel";
import { CommandPalette, type CommandItem } from "./components/CommandPalette";
import { FilterSidebar } from "./components/FilterSidebar";
import { GraphCanvas, type GraphCanvasHandle } from "./components/GraphCanvas";
import { Inspector } from "./components/Inspector";
import { LaunchControl } from "./components/LaunchControl";
import { WorkspaceTabs } from "./components/WorkspaceTabs";
import { loadBookmarks, loadSavedViews, loadTheme, storeBookmarks, storeSavedViews, storeTheme } from "./storage";
import type { GitRef, GraphDocument, GraphFilters, LaunchConfiguration, SavedView, ViewMode } from "./types";

type SyncState = "idle" | "indexing" | "updated";

const download = (name: string, contents: string, type: string) => {
  const anchor = document.createElement("a");
  anchor.href = contents.startsWith("data:") ? contents : URL.createObjectURL(new Blob([contents], { type }));
  anchor.download = name;
  anchor.click();
  if (!contents.startsWith("data:")) URL.revokeObjectURL(anchor.href);
};

export default function App() {
  const [graphDocument, setGraphDocument] = useState<GraphDocument>();
  const [gitRefs, setGitRefs] = useState<GitRef[]>([]);
  const [gitBase, setGitBase] = useState("HEAD");
  const [launch, setLaunch] = useState<LaunchConfiguration | null>();
  const [error, setError] = useState<string>();
  const [indexError, setIndexError] = useState<string>();
  const [serverIndexing, setServerIndexing] = useState(false);
  const [mode, setMode] = useState<ViewMode>("repository");
  const [filters, setFilters] = useState<GraphFilters>(defaultFilters);
  const [collapsed, setCollapsed] = useState(new Set<string>());
  const [selectedId, setSelectedId] = useState<string>();
  const [openTabIds, setOpenTabIds] = useState<string[]>([]);
  const [bookmarks, setBookmarks] = useState<string[]>(loadBookmarks);
  const [savedViews, setSavedViews] = useState<SavedView[]>(loadSavedViews);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [theme, setTheme] = useState(loadTheme);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 820px)").matches);
  const [syncState, setSyncState] = useState<SyncState>("idle");
  const [presentation, setPresentation] = useState<VisibleGraph>();
  const canvasRef = useRef<GraphCanvasHandle>(null);
  const filtersToggleRef = useRef<HTMLButtonElement>(null);
  const inspectorToggleRef = useRef<HTMLButtonElement>(null);
  const appliedPreferences = useRef("");
  const overviewInitialized = useRef(false);
  const pendingFocus = useRef<string | undefined>(undefined);

  const refresh = useCallback(async (manual = false) => {
    setSyncState("indexing");
    try {
      if (usingMockApi && manual) {
        await new Promise((resolve) => window.setTimeout(resolve, 320));
        setGraphDocument((current) => current ? { ...current, revision: current.revision + 1, generatedAt: new Date().toISOString() } : current);
      } else {
        const next = await loadGraph(gitBase);
        const signature = JSON.stringify(next.preferences);
        if (signature !== appliedPreferences.current) {
          appliedPreferences.current = signature;
          const initialMode = next.preferences.initialView === "entry" && !next.entryKey ? "repository" : next.preferences.initialView;
          setMode(initialMode);
          setFilters(filtersFromPreferences(next.preferences));
          setGitBase(next.preferences.gitBase);
          if (initialMode === "repository" && !overviewInitialized.current) {
            const overview = largeRepositoryOverview(next);
            if (overview.size) { setCollapsed(overview); overviewInitialized.current = true; }
          }
        }
        setGraphDocument(next);
      }
      setError(undefined);
      setSyncState("updated");
      window.setTimeout(() => setSyncState("idle"), 1200);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load the graph.");
      setSyncState("idle");
    }
  }, [gitBase]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    void loadGitRefs().then(setGitRefs).catch(() => setGitRefs([{ id: "HEAD", label: "HEAD", kind: "branch" }]));
    void loadLaunchConfiguration().then(setLaunch).catch(() => setLaunch(null));
  }, []);

  useEffect(() => {
    if (usingMockApi || !graphDocument) return;
    let active = true;
    let polling = false;
    const timer = window.setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const status = await loadStatus();
        if (!active) return;
        setIndexError(status.lastError || (status.state === "error" ? "Indexing failed." : undefined));
        setServerIndexing(status.state === "indexing");
        if (status.generation !== graphDocument.revision) await refresh();
        const nextLaunch = await loadLaunchConfiguration();
        if (active) setLaunch(nextLaunch);
      } catch (cause) {
        if (active) setIndexError(cause instanceof Error ? cause.message : "Live status is unavailable.");
      } finally {
        polling = false;
      }
    }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [graphDocument, refresh]);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") { event.preventDefault(); setPaletteOpen((open) => !open); return; }
      if (event.key === "Escape") {
        const returnFocus = event.target instanceof Element && event.target.closest(".responsive-panel--left")
          ? filtersToggleRef.current
          : event.target instanceof Element && event.target.closest(".responsive-panel--right")
            ? inspectorToggleRef.current
            : null;
        setSelectedId(undefined); setFiltersOpen(false); setInspectorOpen(false);
        if (returnFocus) requestAnimationFrame(() => returnFocus.focus());
        return;
      }
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === "1") {
        if (graphDocument && !overviewInitialized.current) {
          const overview = largeRepositoryOverview(graphDocument);
          if (overview.size) { setCollapsed(overview); overviewInitialized.current = true; }
        }
        setMode("repository");
      }
      if (event.key === "2" && graphDocument?.entryKey) setMode("entry");
      if (event.key.toLocaleLowerCase() === "f") canvasRef.current?.fit();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [graphDocument]);

  useEffect(() => { storeBookmarks(bookmarks); }, [bookmarks]);
  useEffect(() => { storeSavedViews(savedViews); }, [savedViews]);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 820px)");
    const update = () => setCompact(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0c1014" : "#f3f6f7");
    storeTheme(theme);
  }, [theme]);

  const visible = useMemo(() => graphDocument ? buildVisibleGraph(graphDocument, filters, mode, collapsed) : { nodes: [], edges: [] }, [collapsed, filters, graphDocument, mode]);
  const displayed = presentation ?? visible;
  const selectedNode = visible.nodes.find((node) => node.id === selectedId) ?? graphDocument?.nodes.find((node) => node.id === selectedId);
  const canvasSelectedId = selectedId && graphDocument ? selectionFocusTarget(graphDocument, selectedId, collapsed) : undefined;
  const openTabs = openTabIds.map((id) => graphDocument?.nodes.find((node) => node.id === id)).filter((node): node is NonNullable<typeof node> => Boolean(node));
  const bookmarkNodes = bookmarks.map((id) => graphDocument?.nodes.find((node) => node.id === id)).filter((node): node is NonNullable<typeof node> => Boolean(node));

  useEffect(() => {
    const id = pendingFocus.current;
    if (!id || !visible.nodes.some((node) => node.id === id)) return;
    const frame = requestAnimationFrame(() => canvasRef.current?.focus(id));
    pendingFocus.current = undefined;
    return () => cancelAnimationFrame(frame);
  }, [visible]);

  const changeMode = (next: ViewMode) => {
    if (!graphDocument) return;
    if (next === "entry" && !graphDocument.entryKey) return;
    if (next === "repository" && !overviewInitialized.current) {
      const overview = largeRepositoryOverview(graphDocument);
      if (overview.size) { setCollapsed(overview); overviewInitialized.current = true; }
    }
    setMode(next);
  };

  const select = (id?: string) => {
    setSelectedId(id);
    const document = graphDocument;
    const selected = id ? document?.nodes.find((node) => node.id === id) : undefined;
    if (selected && document) {
      const focusId = selectionFocusTarget(document, selected.id, collapsed);
      setOpenTabIds((ids) => ids.includes(selected.id) ? ids : [...ids, selected.id].slice(-7));
      setCollapsed((current) => {
        const next = new Set(current);
        const byId = new Map(document.nodes.map((node) => [node.id, node]));
        let parent = selected.parent;
        while (parent) { if (parent !== focusId) next.delete(parent); parent = byId.get(parent)?.parent; }
        return next;
      });
      pendingFocus.current = focusId;
    }
    if (id) { setFiltersOpen(false); setInspectorOpen(true); }
  };

  const toggleCollapse = (id: string) => setCollapsed((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const saveView = () => setSavedViews((views) => [...views, {
    id: `view-${Date.now()}`,
    name: `${mode === "entry" ? "Entry focus" : "Repository"} ${views.length + 1}`,
    mode,
    filters: structuredClone(filters),
    collapsed: [...collapsed],
  }]);

  const applyView = (view: SavedView) => {
    overviewInitialized.current = true;
    setMode(view.mode === "entry" && !graphDocument?.entryKey ? "repository" : view.mode);
    setFilters(structuredClone(view.filters));
    setCollapsed(new Set(view.collapsed));
  };

  const exportJson = () => {
    if (graphDocument) download(`${graphDocument.project}-graph.json`, exportDocument(graphDocument, canvasRef.current?.getVisibleGraph() ?? visible), "application/json");
  };
  const exportPng = () => {
    const image = canvasRef.current?.exportPng();
    if (image && graphDocument) download(`${graphDocument.project}-graph.png`, image, "image/png");
  };

  const commands = useMemo<CommandItem[]>(() => {
    const actions: CommandItem[] = [
      { id: "mode-repo", label: "Show entire repository", group: "Commands", hint: "1", keywords: ["overview", "all"], onSelect: () => changeMode("repository") },
      ...(graphDocument?.entryKey ? [{ id: "mode-entry", label: "Focus full entry flow", group: "Commands", hint: "2", keywords: ["main", "reachable"], onSelect: () => changeMode("entry") }] : []),
      { id: "fit", label: "Fit graph to viewport", group: "Commands", hint: "F", onSelect: () => canvasRef.current?.fit() },
      { id: "reset", label: "Reset graph viewport", group: "Commands", onSelect: () => canvasRef.current?.reset() },
      { id: "tests", label: filters.includeTests ? "Exclude tests" : "Include tests", group: "Commands", onSelect: () => setFilters((current) => ({ ...current, includeTests: !current.includeTests })) },
      { id: "theme", label: theme === "dark" ? "Use light mode" : "Use dark mode", group: "Commands", keywords: ["appearance", "color"], onSelect: () => setTheme((current) => current === "dark" ? "light" : "dark") },
      { id: "export", label: "Export visible graph as JSON", group: "Commands", onSelect: exportJson },
    ];
    const searchableNodes = [
      ...(graphDocument?.nodes.filter((node) => node.kind !== "external_package") ?? []),
      ...visible.nodes.filter((node) => node.kind === "external_package"),
    ];
    const symbols = searchableNodes.map((node) => ({ id: `node-${node.id}`, label: node.label, group: "Symbols", hint: node.kind, keywords: [node.qualifiedName, node.kind], onSelect: () => select(node.id) }));
    return [...actions, ...symbols];
  }, [filters.includeTests, graphDocument, theme, visible]);

  if (!graphDocument && !error) return <main className="boot-screen"><div className="boot-mark"><Share2 size={24} /></div><p>Indexing repository graph…</p></main>;
  if (!graphDocument) return <main className="boot-screen boot-screen--error"><p>{error}</p><button className="button button--primary" onClick={() => void refresh()}>Try again</button></main>;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><div className="brand__mark"><Share2 size={16} /></div><div><strong>Code View</strong><span>{graphDocument.project}</span></div></div>
        <div className="mode-switch" aria-label="Graph scope">
          <button data-active={mode === "repository"} onClick={() => changeMode("repository")}><BookOpen size={14} />Repository<kbd>1</kbd></button>
          <button data-active={mode === "entry"} disabled={!graphDocument.entryKey} title={graphDocument.entryKey ? "Show the complete entry flow" : "No entry was detected; configure one in code-view.json"} onClick={() => changeMode("entry")}><Focus size={14} />Entry Focus<kbd>2</kbd></button>
        </div>
        <button className="search-trigger" onClick={() => setPaletteOpen(true)} aria-label="Search symbols or commands"><Search size={15} /><span>Search symbols or commands</span><kbd>⌘ K</kbd></button>
        <div className="topbar__actions">
          <div className="generation" data-state={error || indexError ? "error" : serverIndexing ? "indexing" : syncState} title={`Revision ${graphDocument.revision}`}><span /><span>{error || indexError ? "Refresh failed" : serverIndexing || syncState === "indexing" ? "Indexing" : syncState === "updated" ? "Updated" : `Gen ${graphDocument.revision}`}</span></div>
          <button className="icon-button" onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>{theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}</button>
          <button className="icon-button" onClick={() => void refresh(true)} aria-label="Refresh generated graph"><RefreshCw size={16} /></button>
          <button ref={filtersToggleRef} className="icon-button" onClick={() => { setInspectorOpen(false); setFiltersOpen((open) => !open); }} aria-label="Toggle graph filters" aria-controls="graph-filters" aria-expanded={filtersOpen}><Filter size={16} /></button>
          <button ref={inspectorToggleRef} className="icon-button mobile-only" onClick={() => { setFiltersOpen(false); setInspectorOpen((open) => !open); }} aria-label="Toggle selection details" aria-controls="selection-details" aria-expanded={inspectorOpen}><PanelRight size={16} /></button>
        </div>
      </header>

      <div className="gitbar">
        <div><GitCompareArrows size={13} /><span>WORKTREE vs</span><select aria-label="Git comparison base" value={gitBase} onChange={(event) => setGitBase(event.target.value)}>{gitRefs.map((ref) => <option key={ref.id} value={ref.id}>{ref.label}</option>)}</select><b>{graphDocument.nodes.filter((node) => node.diff !== "unchanged").length} changed symbols</b></div>
        <LaunchControl value={launch} onChange={setLaunch} />
        <div className="git-legend"><span data-diff="added">+ added</span><span data-diff="modified">~ modified</span><span data-diff="removed">− removed</span><span>· unchanged</span></div>
      </div>
      <WorkspaceTabs nodes={openTabs} selectedId={selectedId} onSelect={select} onClose={(id) => { setOpenTabIds((ids) => ids.filter((item) => item !== id)); if (selectedId === id) setSelectedId(undefined); }} />

      <div className="workspace">
        <div id="graph-filters" className={filtersOpen ? "responsive-panel responsive-panel--left is-open" : "responsive-panel responsive-panel--left"} inert={!filtersOpen} aria-hidden={!filtersOpen}>
          <FilterSidebar filters={filters} onChange={setFilters} bookmarks={bookmarkNodes} onOpenNode={select} onRemoveBookmark={(id) => setBookmarks((items) => items.filter((item) => item !== id))} savedViews={savedViews} onSaveView={saveView} onApplyView={applyView} onDeleteView={(id) => setSavedViews((views) => views.filter((view) => view.id !== id))} mode={mode} />
        </div>
        <main className="canvas-stage">
          <GraphCanvas ref={canvasRef} graph={visible} mode={mode} theme={theme} selectedId={canvasSelectedId} onSelect={select} onToggleCollapse={toggleCollapse} onScopeChange={setPresentation} />
          <div className="canvas-status"><span><b>{displayed.nodes.length}</b> nodes</span><span><b>{displayed.edges.length}</b> relationships{displayed.edges.length !== visible.edges.length && ` of ${visible.edges.length}`}</span><span>{graphDocument.nodes.length} indexed</span>{collapsed.size > 0 && <span title="Double-click a group to expand it"><b>{collapsed.size}</b> collapsed groups</span>}{mode === "entry" && <span className="entry-indicator"><Focus size={12} />entry scope</span>}</div>
          <div className="canvas-tools" aria-label="Canvas tools">
            <button onClick={() => canvasRef.current?.fit()} aria-label="Fit graph"><Maximize2 size={15} /></button>
            <button onClick={() => canvasRef.current?.reset()} aria-label="Reset graph view"><RotateCcw size={15} /></button>
            <button onClick={exportJson} aria-label="Export graph JSON"><Braces size={15} /></button>
            <button onClick={exportPng} aria-label="Export graph image"><Image size={15} /></button>
          </div>
          <div className="direction-legend"><span className="inbound-key"><ChevronsLeftRight size={12} />inbound</span><span className="outbound-key"><ChevronsLeftRight size={12} />outbound</span><span className="unresolved-key">unresolved</span></div>
          {(error || indexError) && <div className="toast toast--error" role="alert">Live refresh failed: {error || indexError} Showing the last loaded graph.</div>}
          {!error && !indexError && !serverIndexing && syncState === "updated" && <div className="toast"><Check size={14} />Graph refreshed from generation {graphDocument.revision}</div>}
        </main>
        <div id="selection-details" className={inspectorOpen ? "responsive-panel responsive-panel--right is-open" : "responsive-panel responsive-panel--right"} inert={compact && !inspectorOpen} aria-hidden={compact && !inspectorOpen}>
          <Inspector document={graphDocument} displayNodes={displayed.nodes} displayEdges={displayed.edges} relations={filters.relations} selectedId={selectedId} bookmarked={Boolean(selectedNode && bookmarks.includes(selectedNode.id))} onToggleBookmark={(id) => setBookmarks((items) => items.includes(id) ? items.filter((item) => item !== id) : [...items, id])} onOpenNode={select} gitBase={gitBase} />
        </div>
      </div>
      <footer className="statusbar"><span><Sparkles size={12} />Static best-effort analysis</span><span>{graphDocument.diagnostics.length} unresolved diagnostic{graphDocument.diagnostics.length === 1 ? "" : "s"}</span><span>Read-only</span></footer>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} items={commands} />
    </div>
  );
}
