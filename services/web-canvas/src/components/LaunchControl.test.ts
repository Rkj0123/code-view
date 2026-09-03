import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LaunchControl } from "./LaunchControl";
import { approveAndStartLaunch, setLaunchRunning } from "../api";
import type { LaunchConfiguration } from "../types";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

vi.mock("../api", () => ({
  approveAndStartLaunch: vi.fn(),
  setLaunchRunning: vi.fn(),
}));

describe("LaunchControl", () => {
  afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

  it("traps modal focus and restores it to Run", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(createElement(LaunchControl, {
      value: { generation: 1, display: "python main.py", argv: ["python", "main.py"], cwd: "/repo", mode: "manual", allowLaunch: true, running: false },
      onChange: vi.fn(),
    })));
    const run = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("Run"))!;
    await act(async () => { run.click(); await new Promise((resolve) => requestAnimationFrame(resolve)); });
    const dialog = host.querySelector<HTMLElement>('[role="dialog"]')!;
    const controls = [...dialog.querySelectorAll<HTMLButtonElement>("button")];
    expect(document.activeElement?.textContent).toContain("Approve and run");
    await act(async () => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab" })); });
    expect(document.activeElement).toBe(controls[0]);
    await act(async () => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", cancelable: true })); });
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(run);
    await act(async () => root.unmount());
  });

  it("freezes the displayed command and surfaces stale approval inside the dialog", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const value: LaunchConfiguration = { generation: 7, display: "python a.py", argv: ["python", "a.py"], cwd: "/repo", mode: "manual", allowLaunch: true, running: false };
    const onChange = vi.fn();
    vi.mocked(approveAndStartLaunch).mockRejectedValue(new Error("Command changed; review it again."));
    await act(async () => root.render(createElement(LaunchControl, { value, onChange })));
    await act(async () => host.querySelector<HTMLButtonElement>(".launch-button")!.click());
    await act(async () => root.render(createElement(LaunchControl, {
      value: { ...value, generation: 8, display: "python b.py", argv: ["python", "b.py"] }, onChange,
    })));
    const dialog = host.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(dialog.textContent).toContain('["python","a.py"]');
    expect(dialog.textContent).not.toContain("b.py");
    await act(async () => dialog.querySelector<HTMLButtonElement>(".button--approve")!.click());
    expect(approveAndStartLaunch).toHaveBeenCalledWith(7);
    expect(onChange).not.toHaveBeenCalled();
    expect(dialog.querySelector('[role="alert"]')?.textContent).toContain("Command changed");
    await act(async () => root.unmount());
  });

  it("keeps Stop available after indexing revokes launch permission or removes the command", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const value: LaunchConfiguration = { generation: 7, display: "", argv: [], cwd: "/repo", mode: "document", allowLaunch: false, running: true };
    const stopped = { ...value, running: false };
    const onChange = vi.fn();
    vi.mocked(setLaunchRunning).mockResolvedValue(stopped);
    await act(async () => root.render(createElement(LaunchControl, { value, onChange })));
    const stop = host.querySelector<HTMLButtonElement>(".launch-button--stop")!;
    expect(stop.textContent).toBe("Stop");
    await act(async () => stop.click());
    expect(setLaunchRunning).toHaveBeenCalledWith("stop");
    expect(onChange).toHaveBeenCalledWith(stopped);
    await act(async () => root.render(createElement(LaunchControl, { value: stopped, onChange })));
    expect(host.textContent).toBe("");
    await act(async () => root.unmount());
  });
});
