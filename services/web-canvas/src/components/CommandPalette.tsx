import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface CommandItem {
  id: string;
  label: string;
  group: string;
  hint?: string;
  keywords?: string[];
  onSelect: () => void;
}

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: CommandItem[];
}

const fuzzyMatch = (needle: string, haystack: string) => {
  if (!needle) return true;
  let index = 0;
  for (const character of haystack.toLocaleLowerCase()) {
    if (character === needle[index]) index += 1;
    if (index === needle.length) return true;
  }
  return false;
};

// Adapted from beUI Command Palette: grouped fuzzy filtering, active-row
// keyboard cursor, portalled dialog, focus management, and scroll-into-view.
export function CommandPalette({ open, onOpenChange, items }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return items.filter((item) => [item.label, item.group, ...(item.keywords ?? [])].some((value) => fuzzyMatch(needle, value)));
  }, [items, query]);

  const grouped = useMemo(() => {
    const groups = new Map<string, CommandItem[]>();
    filtered.forEach((item) => groups.set(item.group, [...(groups.get(item.group) ?? []), item]));
    return [...groups.entries()];
  }, [filtered]);

  const close = () => onOpenChange(false);

  useEffect(() => {
    if (!open) return;
    returnFocus.current = document.activeElement as HTMLElement;
    setQuery("");
    setActive(0);
    const frame = requestAnimationFrame(() => inputRef.current?.focus());
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      cancelAnimationFrame(frame);
      document.body.style.overflow = previous;
      returnFocus.current?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    dialogRef.current?.querySelector<HTMLElement>(`[data-command-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  if (!open) return null;

  return createPortal(
    <div className="palette-layer" role="presentation">
      <button className="palette-backdrop" aria-label="Close command palette" onClick={close} />
      <div
        ref={dialogRef}
        className="palette motion-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Search code and run commands"
        onKeyDown={(event) => {
          if (event.key === "Escape") { event.preventDefault(); close(); }
          if (event.key === "ArrowDown") { event.preventDefault(); setActive((index) => Math.min(index + 1, filtered.length - 1)); }
          if (event.key === "ArrowUp") { event.preventDefault(); setActive((index) => Math.max(index - 1, 0)); }
          if (event.key === "Enter") {
            event.preventDefault();
            filtered[active]?.onSelect();
            close();
          }
          if (event.key === "Tab") {
            const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>("input,button:not([disabled])") ?? [])];
            const first = focusable[0];
            const last = focusable.at(-1);
            if (!first || !last) return;
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
          }
        }}
      >
        <div className="palette__search">
          <Search size={17} aria-hidden="true" />
          <input ref={inputRef} value={query} onChange={(event) => { setQuery(event.target.value); setActive(0); }} placeholder="Search symbols or run a command…" aria-label="Search symbols or commands" aria-controls="command-results" />
          <kbd>esc</kbd>
        </div>
        <div id="command-results" className="palette__results" role="listbox" aria-label="Results">
          {filtered.length === 0 ? <p className="empty-message">No symbols or commands found.</p> : grouped.map(([group, entries]) => (
            <section key={group} className="palette__group" aria-label={group}>
              <p>{group}</p>
              {entries.map((item) => {
                const index = filtered.indexOf(item);
                return (
                  <button key={item.id} type="button" role="option" aria-selected={index === active} data-command-index={index} onPointerMove={() => setActive(index)} onClick={() => { item.onSelect(); close(); }}>
                    <span>{item.label}</span>{item.hint && <kbd>{item.hint}</kbd>}
                  </button>
                );
              })}
            </section>
          ))}
        </div>
        <footer className="palette__footer"><span>↑↓ navigate</span><span>↵ open</span><span>⌘K toggle</span></footer>
      </div>
    </div>,
    document.body,
  );
}
