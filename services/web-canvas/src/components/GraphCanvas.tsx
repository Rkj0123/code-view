import cytoscape, { type Core, type ElementDefinition, type StylesheetStyle } from "cytoscape";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { VisibleGraph } from "../graphModel";
import type { Theme, ViewMode } from "../types";
import { buildFlowScene, groupKinds, type FlowScope } from "../flowLayout";
import "./flowCanvas.css";

export interface GraphCanvasHandle {
  fit: () => void;
  reset: () => void;
  focus: (id: string) => void;
  exportPng: () => string | undefined;
  getVisibleGraph: () => VisibleGraph;
}
export interface GraphCanvasProps {
  graph: VisibleGraph;
  mode: ViewMode;
  theme: Theme;
  selectedId?: string;
  onSelect: (id?: string) => void;
  onToggleCollapse: (id: string) => void;
  /** Use these scoped facts for the outer status, Inspector, and JSON export. */
  onScopeChange?: (graph: VisibleGraph, scope: FlowScope) => void;
}

export function replaceCanvasElements(cy: Core, elements: ElementDefinition[]) {
  // Removing a compound also removes its children. Replace the collection atomically.
  cy.batch(() => { cy.elements().remove(); cy.add(elements); });
}

export const graphStyles = (theme: Theme): StylesheetStyle[] => {
  const light = theme === "light";
  return [
    { selector: "node", style: { label: "data(displayLabel)", shape: "round-rectangle", width: 164, height: 28, "font-size": 13, "font-family": "ui-monospace, SFMono-Regular, Menlo, monospace", "text-valign": "center", "text-halign": "center", "text-wrap": "ellipsis", "text-max-width": 156, color: light ? "#30342e" : "#e9ece5", "background-color": light ? "#ffffff" : "#20251f", "border-width": 0, "overlay-opacity": 0 } },
    { selector: "node[width]", style: { width: "data(width)", "text-max-width": "data(labelWidth)" } },
    { selector: "node.group-boundary", style: { label: "", "background-color": light ? "#e7e8e4" : "#252c28", "border-width": 1, "border-color": light ? "#d5d9ce" : "#424b3f", padding: 12, "compound-sizing-wrt-labels": "exclude" } },
    { selector: "node.group-title", style: { "background-opacity": 0, "font-weight": 600 } },
    { selector: "node.compact-title", style: { height: 20 } },
    { selector: "node.group-boundary[kind = 'class']", style: { "background-color": light ? "#ffffff" : "#161d17", "border-width": 0 } },
    { selector: "node.group-boundary.is-compact", style: { padding: 2 } },
    { selector: "node[kind = 'function'], node[kind = 'method']", style: { "background-color": light ? "#fae5a2" : "#554721", color: light ? "#453717" : "#fff0bd" } },
    { selector: "node[kind = 'variable']", style: { "background-color": light ? "#d5e8ed" : "#213d46", color: light ? "#254d59" : "#c5e5ee" } },
    { selector: "node[resolution = 'external'], node[resolution = 'unresolved']", style: { "border-width": 1, "border-style": "dashed", "border-color": light ? "#a4765a" : "#af8568", "background-color": light ? "#f7ece4" : "#342920" } },
    { selector: "node[diff = 'added']", style: { "border-width": 2, "border-color": "#498263" } },
    { selector: "node[diff = 'modified']", style: { "border-width": 2, "border-color": "#b88931" } },
    { selector: "node[diff = 'removed']", style: { "border-width": 2, "border-color": "#b36468", opacity: 0.55 } },
    { selector: "node.is-selected", style: { "border-width": 2.5, "border-color": light ? "#3c4836" : "#e2eacc" } },
    { selector: "node.lane-label", style: { width: 210, height: 12, "background-opacity": 0, "font-size": 10, "font-family": "ui-sans-serif, system-ui", color: light ? "#74796e" : "#abb5a3", "text-max-width": 210 } },
    { selector: "edge", style: { "curve-style": "taxi", "taxi-direction": "data(direction)", "taxi-turn": "50%", "taxi-turn-min-distance": 18, width: 1.5, "line-color": light ? "#aaa991" : "#727765", "target-arrow-color": light ? "#888771" : "#939981", "target-arrow-shape": "triangle", "arrow-scale": 0.75, opacity: 0.68, "overlay-opacity": 0 } },
    { selector: "edge[kind = 'contains']", style: { "line-style": "dotted", width: 1, opacity: 0.24 } },
    { selector: "edge[kind = 'imports']", style: { "line-style": "dashed" } },
    { selector: "edge[kind = 'calls'], edge[kind = 'constructs'], edge[kind = 'api_calls']", style: { "line-color": light ? "#c19d36" : "#b69950", "target-arrow-color": light ? "#c19d36" : "#b69950" } },
    { selector: "edge[kind = 'reads'], edge[kind = 'writes']", style: { "line-color": light ? "#73a4b5" : "#739eab", "target-arrow-color": light ? "#73a4b5" : "#739eab" } },
    { selector: "edge[resolution = 'unresolved']", style: { "line-style": "dashed", "line-color": "#b87968", "target-arrow-color": "#b87968" } },
    { selector: "edge[diff = 'added']", style: { "line-color": "#498263", "target-arrow-color": "#498263" } },
    { selector: "edge[diff = 'removed']", style: { "line-color": "#b36468", "target-arrow-color": "#b36468", opacity: 0.55 } },
    { selector: "edge.is-inbound", style: { width: 2, opacity: 1, "line-color": light ? "#58879b" : "#7bafc1", "target-arrow-color": light ? "#58879b" : "#7bafc1" } },
    { selector: "edge.is-outbound", style: { width: 2, opacity: 1, "line-color": light ? "#be963b" : "#c2a154", "target-arrow-color": light ? "#be963b" : "#c2a154" } },
    { selector: "edge.is-selected, edge:selected", style: { width: 3, opacity: 1, label: "data(relation)", "font-size": 11, color: light ? "#30342e" : "#e9ece5", "text-background-color": light ? "#fffef9" : "#131812", "text-background-opacity": 1, "text-background-padding": 4 } },
  ] as unknown as StylesheetStyle[];
};

export const GraphCanvas = forwardRef<GraphCanvasHandle, GraphCanvasProps>(function GraphCanvas({ graph, mode, theme, selectedId, onSelect, onToggleCollapse, onScopeChange }, ref) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | undefined>(undefined);
  const callbacks = useRef({ onSelect, onToggleCollapse });
  callbacks.current = { onSelect, onToggleCollapse };
  const [requestedScope, setRequestedScope] = useState<FlowScope>("selection");
  const scene = useMemo(() => buildFlowScene(graph, selectedId, requestedScope), [graph, selectedId, requestedScope]);
  const sceneRef = useRef(scene);
  sceneRef.current = scene;
  const previous = useRef<{ graph: VisibleGraph; scope: FlowScope; mode: ViewMode; selectedId?: string } | undefined>(undefined);
  const byId = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph]);
  const selectedNode = graph.nodes.find((node) => node.id === selectedId);
  const selectedEdge = graph.edges.find((edge) => edge.id === selectedId);
  const focus = (id: string) => {
    const cy = cyRef.current;
    const nodes = cy?.nodes().filter((node) => node.data("originalId") === id);
    if (!cy || !nodes?.length) return;
    // The selected-flow layout already places the selection with its callers and uses.
    if (sceneRef.current.scope === "selection") return;
    cy.center(nodes.first());
    if (cy.zoom() < 0.9) cy.zoom({ level: 0.9, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  };
  useImperativeHandle(ref, () => ({
    fit: () => cyRef.current?.fit(undefined, 44), reset: () => cyRef.current?.reset(), focus,
    exportPng: () => cyRef.current?.png({ full: true, scale: 2, bg: theme === "light" ? "#ffffff" : "#0c1014" }),
    getVisibleGraph: () => sceneRef.current.graph,
  }), [theme]);
  useEffect(() => {
    if (!container.current) return;
    const cy = cytoscape({ container: container.current, elements: [], style: graphStyles(theme), minZoom: 0.12, maxZoom: 3.2, boxSelectionEnabled: true });
    cyRef.current = cy;
    let lastTap = { id: "", at: 0 };
    cy.on("tap", "node", (event) => {
      if (event.target.data("ui")) return;
      const id = event.target.data("originalId") as string;
      const at = Date.now();
      if (lastTap.id === id && at - lastTap.at < 320 && groupKinds.has(event.target.data("kind"))) callbacks.current.onToggleCollapse(id);
      else callbacks.current.onSelect(id);
      lastTap = { id, at };
    });
    cy.on("tap", "edge", (event) => callbacks.current.onSelect(event.target.data("originalId")));
    cy.on("tap", (event) => { if (event.target === cy) callbacks.current.onSelect(undefined); });
    const resize = new ResizeObserver(() => cy.resize());
    resize.observe(container.current);
    return () => { resize.disconnect(); cy.destroy(); cyRef.current = undefined; };
  }, []);
  useEffect(() => { cyRef.current?.style(graphStyles(theme)); }, [theme]);
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    // No layout animation: exact, stable rows and reduced-motion behavior match.
    const preserveViewport = previous.current?.scope === scene.scope && previous.current.mode === mode && (scene.scope === "all" || previous.current.selectedId === selectedId);
    const keepPositions = preserveViewport && previous.current?.graph === graph;
    const positions = new Map(cy.nodes().map((node) => [node.id(), { ...node.position() }]));
    const viewport = { zoom: cy.zoom(), pan: cy.pan() };
    replaceCanvasElements(cy, scene.elements.map((element) => keepPositions && positions.has(element.data.id!) ? { ...element, position: positions.get(element.data.id!) } : element));
    cy.layout({ name: "preset", fit: !preserveViewport, padding: 44, animate: false }).run();
    if (preserveViewport) cy.viewport(viewport);
    else if (cy.zoom() < 0.85) {
      // Initial labels stay readable; Fit explicitly requests the full overview.
      cy.zoom(0.85);
      const bounds = cy.elements().boundingBox();
      cy.pan({ x: 36 - bounds.x1 * cy.zoom(), y: 36 - bounds.y1 * cy.zoom() });
    }
    previous.current = { graph, scope: scene.scope, mode, selectedId };
    onScopeChange?.(scene.graph, scene.scope);
  }, [scene, graph, mode, selectedId, onScopeChange]);
  const cycleNode = (step: number) => {
    const ids = scene.navigation;
    if (!ids.length) return;
    const index = ids.indexOf(selectedId ?? "");
    const next = ids[(index + step + ids.length) % ids.length];
    onSelect(next); focus(next);
  };
  const cycleEdge = () => {
    // Every relationship is reachable by keyboard even while the lens is narrow.
    const edges = selectedEdge ? graph.edges : scene.graph.edges;
    if (edges.length) onSelect(edges[(edges.findIndex((edge) => edge.id === selectedId) + 1) % edges.length].id);
  };
  return <div className="flow-view" data-theme={theme}>
    <div className="flow-view__scope" role="group" aria-label="Canvas presentation scope">
      <button aria-pressed={scene.scope === "all"} onClick={() => setRequestedScope("all")}>All connections</button>
      <button aria-pressed={scene.scope === "selection"} disabled={!selectedNode && !selectedEdge} onClick={() => setRequestedScope("selection")}>Selected flow</button>
    </div>
    <div ref={container} className="graph-canvas flow-view__canvas" tabIndex={0} role="application" aria-label="Interactive repository dependency graph. Arrow keys select symbols and groups. E selects a relationship. Enter collapses a group. Escape returns to all connections." onKeyDown={(event) => {
      if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) { event.preventDefault(); cycleNode(event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1); }
      if (event.key.toLowerCase() === "e") { event.preventDefault(); cycleEdge(); }
      if (event.key === "Escape") { event.preventDefault(); setRequestedScope("all"); }
      if (event.key === "Enter" && selectedNode && groupKinds.has(selectedNode.kind)) { event.preventDefault(); onToggleCollapse(selectedNode.id); }
    }} />
    <label className="flow-view__relationships">Trace<select aria-label="Select any relationship in the filtered graph" value={selectedEdge?.id ?? ""} onChange={(event) => { if (event.target.value) { onSelect(event.target.value); setRequestedScope("selection"); } }}><option value="">{graph.edges.length} relationships</option>{graph.edges.map((edge) => <option key={edge.id} value={edge.id}>{byId.get(edge.source)?.qualifiedName ?? edge.source} → {byId.get(edge.target)?.qualifiedName ?? edge.target} · {edge.kind.replaceAll("_", " ")} ×{edge.count}</option>)}</select></label>
    <p className="flow-view__hint">{scene.scope === "all" ? "Select a symbol to trace its connections. Pan or zoom to explore." : "Direct connections and owned symbols. Repeated headers show file ownership."}</p>
  </div>;
});
