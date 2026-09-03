import { X } from "lucide-react";
import type { GraphNode } from "../types";

interface WorkspaceTabsProps {
  nodes: GraphNode[];
  selectedId?: string;
  onSelect: (id?: string) => void;
  onClose: (id: string) => void;
}

export function WorkspaceTabs({ nodes, selectedId, onSelect, onClose }: WorkspaceTabsProps) {
  return (
    <nav className="workspace-tabs" aria-label="Open symbols">
      <button className="workspace-tab" data-active={!selectedId} onClick={() => onSelect(undefined)}>Overview</button>
      {nodes.map((node) => (
        <div className="workspace-tab" data-active={selectedId === node.id} key={node.id}>
          <button title={node.qualifiedName} onClick={() => onSelect(node.id)}><span className={`kind-dot kind-dot--${node.kind}`} />{node.label}</button>
          <button className="workspace-tab__close" aria-label={`Close ${node.label}`} onClick={() => onClose(node.id)}><X size={12} /></button>
        </div>
      ))}
    </nav>
  );
}
