import { useId, useRef, type ReactNode } from "react";

export interface TabItem {
  id: string;
  label: string;
  content: ReactNode;
}

interface TabsProps {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  label: string;
}

// Adapted from beUI Tabs: semantic tab roles, mounted inactive panels, and
// direction-key navigation. Motion is implemented with local CSS tokens.
export function Tabs({ items, value, onChange, label }: TabsProps) {
  const uid = useId();
  const refs = useRef(new Map<string, HTMLButtonElement>());

  const move = (current: string, delta: number) => {
    const index = items.findIndex((item) => item.id === current);
    const next = items[(index + delta + items.length) % items.length];
    if (!next) return;
    onChange(next.id);
    refs.current.get(next.id)?.focus();
  };

  return (
    <div className="tabs">
      <div className="tabs__list" role="tablist" aria-label={label}>
        {items.map((item) => {
          const active = item.id === value;
          return (
            <button
              key={item.id}
              ref={(node) => {
                if (node) refs.current.set(item.id, node);
                else refs.current.delete(item.id);
              }}
              id={`${uid}-${item.id}-tab`}
              className="tabs__trigger"
              data-active={active}
              role="tab"
              aria-selected={active}
              aria-controls={`${uid}-${item.id}-panel`}
              tabIndex={active ? 0 : -1}
              onClick={() => onChange(item.id)}
              onKeyDown={(event) => {
                if (event.key === "ArrowRight") { event.preventDefault(); move(item.id, 1); }
                if (event.key === "ArrowLeft") { event.preventDefault(); move(item.id, -1); }
                if (event.key === "Home") { event.preventDefault(); onChange(items[0]?.id ?? value); refs.current.get(items[0]?.id ?? "")?.focus(); }
                if (event.key === "End") { event.preventDefault(); onChange(items.at(-1)?.id ?? value); refs.current.get(items.at(-1)?.id ?? "")?.focus(); }
              }}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      {items.map((item) => (
        <div
          key={item.id}
          id={`${uid}-${item.id}-panel`}
          className="tabs__panel motion-in"
          role="tabpanel"
          aria-labelledby={`${uid}-${item.id}-tab`}
          hidden={item.id !== value}
        >
          {item.content}
        </div>
      ))}
    </div>
  );
}
