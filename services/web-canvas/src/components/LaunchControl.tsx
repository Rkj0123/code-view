import { Play, Square, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { approveAndStartLaunch, setLaunchRunning } from "../api";
import type { LaunchConfiguration } from "../types";

interface LaunchControlProps {
  value: LaunchConfiguration | null | undefined;
  onChange: (value: LaunchConfiguration) => void;
}

// Native confirmation dialog pattern with explicit risk context, a focused
// approval action, and escape-to-cancel.
export function LaunchControl({ value, onChange }: LaunchControlProps) {
  const [approval, setApproval] = useState<LaunchConfiguration | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const approveRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const canLaunch = value?.mode === "manual" && value.allowLaunch;

  useEffect(() => {
    if (!approval) return;
    const frame = requestAnimationFrame(() => approveRef.current?.focus());
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setApproval(null); return; }
      if (event.key !== "Tab") return;
      const controls = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? [])];
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", keydown);
    return () => { cancelAnimationFrame(frame); window.removeEventListener("keydown", keydown); triggerRef.current?.focus(); };
  }, [approval]);

  if (!value || (!value.display && !value.running && !approval)) return null;

  const perform = async (action: "start" | "stop") => {
    if (action === "start" && !approval) return;
    setBusy(true);
    setError(undefined);
    try {
      onChange(action === "start" ? await approveAndStartLaunch(approval!.generation) : await setLaunchRunning("stop"));
      setApproval(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Could not ${action} the command.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="launch-control">
      {value.display && <div className="launch-command" title={value.display}><span>Start</span><code>{value.display}</code></div>}
      {canLaunch && !value.running && <button ref={triggerRef} className="launch-button" disabled={busy} onClick={() => { setError(undefined); setApproval({ ...value, argv: [...value.argv] }); }}><Play size={12} />Run</button>}
      {value.running && <button className="launch-button launch-button--stop" disabled={busy} onClick={() => void perform("stop")}><Square size={11} />Stop</button>}
      {!canLaunch && value.mode === "manual" && <span className="launch-disabled" title="Both manual mode and allowLaunch are required">launch disabled</span>}
      {error && !approval && <span className="launch-error" role="alert">{error}</span>}
      {approval && <div className="approval-layer" role="presentation">
        <div className="palette-backdrop" aria-hidden="true" onMouseDown={() => setApproval(null)} />
        <div ref={dialogRef} className="approval-dialog motion-panel" role="dialog" aria-modal="true" aria-labelledby="launch-approval-title">
          <div className="approval-title"><div><span>Local process approval</span><h2 id="launch-approval-title">Run configured start command?</h2></div><button className="icon-button" onClick={() => setApproval(null)} aria-label="Cancel"><X size={16} /></button></div>
          <div className="approval-risk"><TriangleAlert size={15} /><p>This process is not sandboxed. It can read, change, or delete accessible files and use the network. Approval applies to this start only.</p></div>
          <dl className="approval-details"><div><dt>argv</dt><dd><code>{JSON.stringify(approval.argv)}</code></dd></div><div><dt>cwd</dt><dd><code>{approval.cwd}</code></dd></div></dl>
          <p className="approval-note">No shell is used. Approval is bound to displayed generation {approval.generation}; a newer generation requires a fresh review.</p>
          {error && <p className="launch-error" role="alert">{error}</p>}
          <div className="approval-actions"><button className="button button--secondary" onClick={() => setApproval(null)}>Cancel</button><button ref={approveRef} className="button button--approve" disabled={busy} onClick={() => void perform("start")}>{busy ? "Starting…" : "Approve and run"}</button></div>
        </div>
      </div>}
    </div>
  );
}
