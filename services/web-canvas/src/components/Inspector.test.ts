import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { loadSourceRange } from "../api";
import { mockGraph } from "../mockData";
import { defaultFilters } from "../graphModel";
import type { SourceEvidence, SourceRange } from "../types";
import { Inspector } from "./Inspector";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
vi.mock("../api", () => ({ loadSource: vi.fn(async () => null), loadDiff: vi.fn(async () => null), loadSourceRange: vi.fn(), openInEditor: vi.fn() }));

for (const invalidate of ["selection", "occurrence", "generation", "unmount"] as const) {
  for (const outcome of ["resolve", "reject"] as const) {
    it(`ignores an old occurrence ${outcome} after ${invalidate} changes`, async () => {
      const requests: Array<{ resolve: (value: SourceEvidence) => void; reject: (error: Error) => void }> = [];
      vi.mocked(loadSourceRange).mockImplementation(() => new Promise((resolve, reject) => requests.push({ resolve, reject })));
      const host = document.createElement("div");
      document.body.append(host);
      const root = createRoot(host);
      const ranges: SourceRange[] = ["old.py", "new.py"].map((path) => ({ path, start: { line: 1, column: 1 }, end: { line: 1, column: 8 } }));
      const edges = ranges.map((range, index) => ({ ...mockGraph.edges[0], id: `edge:${index}`, locations: ranges }));
      const graph = { ...mockGraph, edges };
      const props = { document: graph, selectedId: edges[0].id, bookmarked: false, gitBase: "HEAD", relations: defaultFilters().relations, onToggleBookmark() {}, onOpenNode() {} };
      const click = (path: string) => [...host.querySelectorAll("button")].find((button) => button.textContent === `${path}:1:1`)!.click();
      try {
        await act(async () => root.render(createElement(Inspector, props)));
        await act(async () => click("old.py"));
        if (invalidate === "selection") await act(async () => root.render(createElement(Inspector, { ...props, selectedId: edges[1].id })));
        if (invalidate === "generation") await act(async () => root.render(createElement(Inspector, { ...props, document: { ...graph, revision: graph.revision + 1 } })));
        if (invalidate === "unmount") await act(async () => root.unmount());
        else {
          await act(async () => click("new.py"));
          await act(async () => requests[1].resolve({ range: ranges[1], text: "CURRENT_SOURCE" }));
          expect(host.querySelector(".source-view")?.textContent).toContain("CURRENT_SOURCE");
        }
        await act(async () => {
          if (outcome === "resolve") requests[0].resolve({ range: ranges[0], text: "STALE_SOURCE" });
          else requests[0].reject(new Error("STALE_ERROR"));
        });
        expect(host.textContent).not.toMatch(/STALE_SOURCE|STALE_ERROR/);
        if (invalidate !== "unmount") expect(host.querySelector(".source-view")?.textContent).toContain("CURRENT_SOURCE");
      } finally {
        if (invalidate !== "unmount") await act(async () => root.unmount());
        host.remove();
        vi.resetAllMocks();
      }
    });
  }
}
