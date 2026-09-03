import { Bookmark, BookmarkCheck, CircleArrowDown, CircleArrowUp, Copy, ExternalLink, FileCode2, GitCompareArrows, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { loadDiff, loadSource, loadSourceRange, openInEditor } from "../api";
import { inspectionNeighborhood, relationLabel, selectedEdge } from "../graphModel";
import type { DiffEvidence, GraphDocument, GraphEdge, GraphFilters, GraphNode, SourceEvidence, SourceRange } from "../types";
import { Tabs } from "./Tabs";

interface InspectorProps {
  document: GraphDocument;
  selectedId?: string;
  bookmarked: boolean;
  onToggleBookmark: (id: string) => void;
  onOpenNode: (id: string) => void;
  gitBase: string;
  displayEdges?: GraphEdge[];
  displayNodes?: GraphNode[];
  relations: GraphFilters["relations"];
}

function FlowList({ edges, direction, nodes, onOpenNode }: { edges: GraphEdge[]; direction: "inbound" | "outbound"; nodes: GraphNode[]; onOpenNode: (id: string) => void }) {
  return (
    <div className={`flow-group flow-group--${direction}`}>
      <div className="flow-heading">
        {direction === "inbound" ? <CircleArrowDown size={14} /> : <CircleArrowUp size={14} />}
        <span>{direction}</span><b>{edges.length}</b>
      </div>
      {edges.length === 0 ? <small>None</small> : edges.map((edge) => {
        const otherId = direction === "inbound" ? edge.source : edge.target;
        const other = nodes.find((node) => node.id === otherId);
        return <button key={edge.id} onClick={() => onOpenNode(edge.id)} title={`Inspect ${relationLabel(edge.kind)} relationship with ${other?.qualifiedName ?? otherId}`}><span>{other?.label ?? otherId}</span><em>{relationLabel(edge.kind)}</em></button>;
      })}
    </div>
  );
}

const locationText = (range: SourceRange) => `${range.path}:${range.start.line}:${range.start.column}`;

export function Inspector({ document, selectedId, bookmarked, onToggleBookmark, onOpenNode, gitBase, relations, displayEdges = [], displayNodes = [] }: InspectorProps) {
  const [tab, setTab] = useState("source");
  const [sourceEvidence, setSourceEvidence] = useState<SourceEvidence | null>();
  const [diffEvidence, setDiffEvidence] = useState<DiffEvidence | null>();
  const [evidenceError, setEvidenceError] = useState<string>();
  const [actionMessage, setActionMessage] = useState<string>();
  const evidenceRequest = useRef(0);
  const allNodes = [...document.nodes, ...displayNodes.filter((item) => !document.nodes.some((node) => node.id === item.id))];
  const node = allNodes.find((item) => item.id === selectedId);
  const edge = selectedEdge(document, displayEdges, selectedId);
  useEffect(() => {
    const request = ++evidenceRequest.current;
    setTab("source");
    setSourceEvidence(undefined);
    setDiffEvidence(undefined);
    setEvidenceError(undefined);
    setActionMessage(undefined);
    if (node) Promise.all([loadSource(node), loadDiff(node.id, gitBase)]).then(([source, diff]) => {
      if (request === evidenceRequest.current) { setSourceEvidence(source); setDiffEvidence(diff); }
    }).catch((cause) => { if (request === evidenceRequest.current) setEvidenceError(cause instanceof Error ? cause.message : "Evidence could not be loaded."); });
    return () => { ++evidenceRequest.current; };
  }, [document, gitBase, node?.id, selectedId]);

  const inspectOccurrence = async (range: SourceRange) => {
    const request = ++evidenceRequest.current;
    setTab("source");
    setSourceEvidence(undefined);
    setEvidenceError(undefined);
    try {
      const source = await loadSourceRange(range);
      if (request === evidenceRequest.current) setSourceEvidence(source);
    } catch (cause) {
      if (request === evidenceRequest.current) setEvidenceError(cause instanceof Error ? cause.message : "Source could not be loaded.");
    }
  };

  const copyLocation = async (range: SourceRange) => {
    try {
      await navigator.clipboard.writeText(locationText(range));
      setActionMessage("Location copied.");
    } catch {
      setActionMessage(`Copy failed. Location: ${locationText(range)}`);
    }
  };

  if (!node && !edge) {
    return (
      <aside className="sidebar inspector" aria-label="Selection details">
        <div className="inspector-empty motion-in">
          <div className="inspector-empty__mark"><FileCode2 size={22} /></div>
          <h2>Understand the flow</h2>
          <p>Select a symbol to see its source and every relationship entering or leaving it.</p>
          <dl className="repo-facts">
            <div><dt>Symbols</dt><dd>{document.nodes.length}</dd></div>
            <div><dt>Relations</dt><dd>{document.edges.length}</dd></div>
            <div><dt>Warnings</dt><dd>{document.diagnostics.length}</dd></div>
          </dl>
        </div>
      </aside>
    );
  }

  if (edge) {
    const source = allNodes.find((item) => item.id === edge.source);
    const target = allNodes.find((item) => item.id === edge.target);
    const occurrences = edge.locations ?? [];
    const occurrenceSource = sourceEvidence ? <div className="source-view"><div className="source-view__path"><FileCode2 size={13} /><span>{locationText(sourceEvidence.range)}</span></div><pre><code>{sourceEvidence.text}</code></pre></div> : <div className="panel-empty">Select an occurrence to load its exact source range.</div>;
    return (
      <aside className="sidebar inspector" aria-label="Relationship details">
        <div className="inspector__scroll motion-in">
          <div className="eyebrow">{relationLabel(edge.kind)} relationship</div>
          <h2>{source?.label ?? edge.source} <span className="edge-arrow">→</span> {target?.label ?? edge.target}</h2>
          <div className="status-line"><span data-resolution={edge.resolution}>{edge.resolution}</span><span>{edge.confidence}</span><span>{edge.count} occurrence{edge.count === 1 ? "" : "s"}</span></div>
          {edge.boundaryReason && <div className="boundary-reason"><strong>Boundary reason</strong><span>{edge.boundaryReason}</span></div>}
          <div className="edge-route">
            <button onClick={() => onOpenNode(edge.source)}>{source?.qualifiedName ?? edge.source}</button>
            <span>flows to</span>
            <button onClick={() => onOpenNode(edge.target)}>{target?.qualifiedName ?? edge.target}</button>
          </div>
          <section className="meta-grid">
            <div><span>Git state</span><strong data-diff={edge.diff}>{edge.diff}</strong></div>
            <div><span>Locations</span><strong>{edge.locations?.length ?? 0}</strong></div>
          </section>
          <section className="occurrences">
            <div className="section-heading"><span>Source occurrences</span><span className="section-count">{occurrences.length}</span></div>
            {occurrences.length === 0 ? <p className="empty-note">No exact source locations were emitted.</p> : occurrences.map((range, index) => (
              <div className="occurrence-row" key={`${locationText(range)}-${index}`}>
                <button onClick={() => void inspectOccurrence(range)}><FileCode2 size={13} /><span>{locationText(range)}</span></button>
                <button className="icon-button icon-button--small" onClick={() => void copyLocation(range)} aria-label={`Copy ${locationText(range)}`}><Copy size={13} /></button>
              </div>
            ))}
          </section>
          <Tabs label="Relationship evidence" value={tab} onChange={setTab} items={[{ id: "source", label: "Source", content: occurrenceSource }]} />
          {actionMessage && <p className="action-message" role="status">{actionMessage}</p>}
          {evidenceError && <div className="warning-callout"><TriangleAlert size={14} /><span>{evidenceError}</span></div>}
        </div>
      </aside>
    );
  }

  if (!node) return null;

  const flow = inspectionNeighborhood(document, { nodes: displayNodes, edges: displayEdges }, node.id, relations);
  const sourceContent = sourceEvidence === undefined ? <div className="panel-empty">Loading exact source…</div> : sourceEvidence ? (
    <div className="source-view"><div className="source-view__path"><FileCode2 size={13} /><span>{locationText(sourceEvidence.range)}</span></div><pre><code>{sourceEvidence.text}</code></pre></div>
  ) : <div className="panel-empty">Source preview is unavailable for this symbol.</div>;
  const diffContent = diffEvidence === undefined ? <div className="panel-empty">Loading Git diff…</div> : !diffEvidence ? <div className="panel-empty"><GitCompareArrows size={18} />No changes against {gitBase}.</div> : (
    <div className="diff-view"><div className="diff-summary"><GitCompareArrows size={15} /><span><b data-diff={diffEvidence.state}>{diffEvidence.state}</b> against {diffEvidence.base}</span></div><pre><code>{diffEvidence.unified}</code></pre></div>
  );

  return (
    <aside className="sidebar inspector" aria-label="Symbol details">
      <div className="inspector__scroll motion-in">
        <div className="inspector-title-row">
          <div><div className="eyebrow">{node.kind}</div><h2>{node.label}</h2></div>
          <button className="icon-button" onClick={() => onToggleBookmark(node.id)} aria-label={bookmarked ? `Remove ${node.label} bookmark` : `Bookmark ${node.label}`}>{bookmarked ? <BookmarkCheck size={17} /> : <Bookmark size={17} />}</button>
        </div>
        <p className="qualified-name">{node.qualifiedName}</p>
        <div className="status-line"><span data-resolution={node.resolution}>{node.resolution}</span><span data-diff={node.diff}>{node.diff}</span>{node.entry && <span className="entry-chip">entry</span>}</div>
        {node.signature && <code className="signature">{node.signature}</code>}
        {node.summary && <p className="symbol-summary">{node.summary}</p>}
        {node.boundaryReason && <div className="boundary-reason"><strong>Boundary reason</strong><span>{node.boundaryReason}</span></div>}
        {(node.resolution === "unresolved" || node.stale) && <div className="warning-callout"><TriangleAlert size={14} /><span>{node.resolution === "unresolved" ? "Best-effort analysis could not resolve this symbol." : "This result may be stale while indexing."}</span></div>}

        <div className="flow-grid" aria-label="Inbound and outbound relationships">
          <FlowList edges={flow.inbound} direction="inbound" nodes={allNodes} onOpenNode={onOpenNode} />
          <FlowList edges={flow.outbound} direction="outbound" nodes={allNodes} onOpenNode={onOpenNode} />
        </div>

        {node.resolution === "resolved" && sourceEvidence && <div className="source-actions">
          <button className="button button--primary" onClick={() => void openInEditor(sourceEvidence.range).then((result) => setActionMessage(result.message))}><ExternalLink size={13} />Open in Editor</button>
          <button className="button button--secondary" onClick={() => void copyLocation(sourceEvidence.range)}><Copy size={13} />Copy location</button>
        </div>}
        {actionMessage && <p className="action-message" role="status">{actionMessage}</p>}
        {evidenceError && <div className="warning-callout"><TriangleAlert size={14} /><span>{evidenceError}</span></div>}

        <Tabs label="Symbol evidence" value={tab} onChange={setTab} items={[
          { id: "source", label: "Source", content: sourceContent },
          { id: "diff", label: "Diff", content: diffContent },
        ]} />
      </div>
    </aside>
  );
}
