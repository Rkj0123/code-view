import { Bookmark, ChevronRight, Eye, Save, Trash2 } from "lucide-react";
import { nodeKinds, relationKinds, type GraphFilters, type GraphNode, type SavedView, type ViewMode } from "../types";
import { relationLabel } from "../graphModel";

const nodeLabel = (kind: string) => kind.replaceAll("_", " ");

interface FilterSidebarProps {
  filters: GraphFilters;
  onChange: (filters: GraphFilters) => void;
  bookmarks: GraphNode[];
  onOpenNode: (id: string) => void;
  onRemoveBookmark: (id: string) => void;
  savedViews: SavedView[];
  onSaveView: () => void;
  onApplyView: (view: SavedView) => void;
  onDeleteView: (id: string) => void;
  mode: ViewMode;
}

export function FilterSidebar({ filters, onChange, bookmarks, onOpenNode, onRemoveBookmark, savedViews, onSaveView, onApplyView, onDeleteView, mode }: FilterSidebarProps) {
  const setNode = (kind: (typeof nodeKinds)[number], checked: boolean) => onChange({ ...filters, nodes: { ...filters.nodes, [kind]: checked } });
  const setRelation = (kind: (typeof relationKinds)[number], checked: boolean) => onChange({ ...filters, relations: { ...filters.relations, [kind]: checked } });

  return (
    <aside className="sidebar sidebar--filters" aria-label="Graph filters">
      <div className="sidebar__scroll">
        <section className="filter-section">
          <div className="section-heading"><span>Nodes</span><Eye size={14} /></div>
          <div className="filter-list">
            {nodeKinds.map((kind) => (
              <label className="filter-row" key={kind}>
                <span className={`kind-dot kind-dot--${kind}`} aria-hidden="true" />
                <span>{nodeLabel(kind)}</span>
                <input type="checkbox" checked={filters.nodes[kind]} onChange={(event) => setNode(kind, event.target.checked)} />
              </label>
            ))}
          </div>
        </section>

        <section className="filter-section">
          <div className="section-heading"><span>Relationships</span><span className="section-count">{relationKinds.filter((kind) => filters.relations[kind]).length}/{relationKinds.length}</span></div>
          <div className="filter-list">
            {relationKinds.map((kind) => (
              <label className="filter-row" key={kind}>
                <span className={`edge-swatch edge-swatch--${kind}`} aria-hidden="true" />
                <span>{relationLabel(kind)}</span>
                <input type="checkbox" checked={filters.relations[kind]} onChange={(event) => setRelation(kind, event.target.checked)} />
              </label>
            ))}
          </div>
        </section>

        <section className="filter-section">
          <div className="section-heading"><span>Scope</span></div>
          <label className="switch-row">
            <span><strong>Include tests</strong><small>Test files and symbols</small></span>
            <input role="switch" type="checkbox" checked={filters.includeTests} onChange={(event) => onChange({ ...filters, includeTests: event.target.checked })} />
          </label>
          <label className="switch-row">
            <span><strong>Changed only</strong><small>Added, modified, or removed</small></span>
            <input role="switch" type="checkbox" checked={filters.changedOnly} onChange={(event) => onChange({ ...filters, changedOnly: event.target.checked })} />
          </label>
          <p className="section-note">{mode === "entry" ? "Showing the full statically reachable graph." : "Showing the entire repository graph."}</p>
        </section>

        <section className="filter-section">
          <div className="section-heading"><span>Saved views</span><button className="icon-button icon-button--small" onClick={onSaveView} aria-label="Save current view"><Save size={14} /></button></div>
          {savedViews.length === 0 ? <p className="empty-note">No saved views yet.</p> : (
            <div className="item-list">
              {savedViews.map((view) => (
                <div className="item-row" key={view.id}>
                  <button onClick={() => onApplyView(view)}><ChevronRight size={13} /><span>{view.name}</span></button>
                  <button className="icon-button icon-button--small" onClick={() => onDeleteView(view.id)} aria-label={`Delete ${view.name}`}><Trash2 size={13} /></button>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="filter-section">
          <div className="section-heading"><span>Bookmarks</span><Bookmark size={14} /></div>
          {bookmarks.length === 0 ? <p className="empty-note">Bookmark a symbol from its details.</p> : (
            <div className="item-list">
              {bookmarks.map((node) => (
                <div className="item-row" key={node.id}>
                  <button onClick={() => onOpenNode(node.id)}><span className={`kind-dot kind-dot--${node.kind}`} /><span>{node.label}</span></button>
                  <button className="icon-button icon-button--small" onClick={() => onRemoveBookmark(node.id)} aria-label={`Remove ${node.label} bookmark`}><Trash2 size={13} /></button>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </aside>
  );
}
