# Code View web canvas

A local-only React/TypeScript canvas for understanding a repository as files, symbols, and directional relationships. The browser demo uses a deterministic Python tic-tac-toe graph; live mode consumes the local Code View host.

## Run it

```bash
cd services/web-canvas
npm install
npm run dev
```

Open `http://127.0.0.1:4173`. The demo makes no external network requests. A tokenized host URL automatically enables live mode. The client immediately removes the capability from the visible URL, retains it in tab-scoped `sessionStorage` so a clean-URL reload works, and sends `Authorization: Bearer <token>` on every `/api/v1/*` request. Closing the tab clears the retained capability.

## What is implemented

- Entire-repository and full statically reachable Entry Focus modes.
- Readable file/class boundaries, compact symbol rows, and orthogonal connectors. Select a symbol for a three-column flow: callers, selection, dependencies. File headers sit above symbols; function locals are flat, owner-qualified rows. All connections restores the filtered overview. Every original relationship stays available through Trace, including relationships outside the selected flow.
- Double-click or Enter to collapse a group. Repositories above 300 symbols start at collapsed file boundaries; unresolved targets collapse with their source file and known external symbols collapse by top-level package. Viewport and manual positions survive selection changes within All connections; filters and new generations recompute deterministic rows without graph animation. Initial zoom keeps labels readable; use pan/zoom for large views, or explicitly Fit for an overview.
- Files, classes, functions, methods, variables, external packages, and unresolved nodes visible by default. Derived directory, package, and module groups are off by default but independently selectable. Tests are excluded by default.
- All canonical relationships are always listed: `contains`, `imports`, `calls`, `inherits`, `constructs`, `reads`, `writes`, `decorates`, `type_uses`, `api_calls`, and `test_covers`. Only `test_covers` starts off.
- Blue inbound and gold outbound graph emphasis, explicit arrowheads, uncapped neighbor lists, edge occurrence locations, source evidence, boundary reasons, and Git diff evidence. Counts and JSON export reflect the current presentation scope. Ownership headers may repeat across flow columns but exported IDs and source facts remain canonical. Late occurrence responses cannot overwrite a newer selection, occurrence, or generation.
- HEAD vs WORKTREE comparison with branch/recent-commit base selection, non-color `+`, `~`, `−`, `·` markers, and changed-only scope.
- Search/command palette with bounded center recovery, symbol tabs, bookmarks, local saved views, fit/reset, JSON/PNG export, responsive side panels, and keyboard navigation. Search opens small groups; groups above 150 descendants stay collapsed while the Inspector uses the exact symbol's canonical relationships. If no entry is detected, Repository opens and Entry Focus explains why it is disabled.
- Native light/dark mode follows the macOS preference on first use and persists a manual selection locally.
- Generation and status polling through `/api/v1/status`, manual demo refresh, external/unresolved states, and stale/error messaging. Failed reindexing remains visible in both themes even when generation is unchanged; the last loaded graph remains usable.
- Read-only source integration. Range-less file boundaries and long symbols load a bounded 400-line source window. `Open in Editor` POSTs a graph-provided relative path and position to the host. `Copy location` remains available when no adapter is configured.
- Start commands are documentation by default. Manual launch is visible only when both host guards are present and uses a focus-contained per-start confirmation freezing the exact host-provided `argv`, `cwd`, and `generation`. Approval and start are separate POSTs; the browser never constructs a shell command. A stale dialog is rejected and requires a fresh review. Failed indexing blocks both old and fresh approvals until repaired. Stop remains available for a running process even when launch permission is revoked. The dialog warns that approved processes are not sandboxed.

Keyboard: `⌘/Ctrl K` opens search, `1` selects Repository, `2` selects Entry Focus, `F` fits the graph, arrow keys move between graph nodes, `E` selects a scoped relationship (then cycles all filtered relationships), Enter collapses a selected group, and Escape clears overlays/selection and restores All connections. Filters open from the toolbar on desktop and mobile. All controls have visible focus styles and semantic labels.

## API boundary

`src/apiContract.ts` is the only analyzer-to-UI adapter. It accepts the host response used by `HttpHost::wrapGraph`:

```json
{
  "generation": 42,
  "comparison": null,
  "projectName": "tic-tac-toe",
  "config": {},
  "graph": {
    "schemaVersion": "code-view.graph/v1",
    "project": {"root":".","languages":["python"],"entryNodeId":null,"entryReachableNodeIds":[]},
    "nodes": [],
    "edges": [],
    "diagnostics": []
  }
}
```

The canonical graph remains unrenamed. The adapter derives UI labels, compound parents from `contains`, resolution state, modifiers, source ranges, entry key, and host comparison state. Any unsupported schema version is rejected.

Live evidence calls are relative and authenticated:

- `GET /api/v1/graph?view=repository`
- `GET /api/v1/status`
- `GET /api/v1/git/refs`
- `GET /api/v1/source?path=...&startLine=...&endLine=...`
- `GET /api/v1/git/diff?node=...&base=...`
- `POST /api/v1/editor/open`
- `GET /api/v1/launch`
- `POST /api/v1/launch/approve`, followed by `POST /api/v1/launch/start`
- `POST /api/v1/launch/stop`

Launch responses include `generation`. Approval sends exactly
`{"schemaVersion":"code-view.launch-approval/v2","generation":42}`, using the
generation frozen when the dialog opened. This coordinated v2 request contract
is defined in `contracts/code-view-launch-approval-v2.schema.json`; the graph
contract and `/api/v1` transport routes are unchanged.

When the selected Git base resolves, the host attaches node and edge comparison
metadata and exposes a bounded unified source diff for changed symbols. On a
non-Git repository, comparison remains `null` and the UI leaves diff markers
unchanged rather than inventing evidence.

## Quality gates

```bash
npx playwright install chromium
npm test
npm run eval
npm run build
```

The periodic eval starts a private loopback Vite instance and runs a real
Chromium workflow at 390 px with reduced motion. It rejects console errors,
off-screen core controls, and outbound requests. Desktop live-API regressions
verify same-generation error visibility and recovery in both themes, frozen
command approval, conflict feedback, successful re-approval after review, and
Stop after launch is disabled. Real analyzer output for tic-tac-toe and a generated
ten-file repository tests exact-edge tracing and scoped export; screenshots
capture settled light/dark styles. A delayed-response browser regression checks
that old relationship evidence cannot replace the current source panel.

Gate tests cover graph filtering/search/collapse, no-entry fallback, large-repository boundaries, external grouping, full entry reachability, bounded source adaptation, the real analyzer response adapter, modal focus containment, one-shot launch approval sequencing, token bootstrap, full launch responses, and aggregate edge inspection. Periodic evals freeze product defaults, capability handling, collapsed evidence, 390px control reachability, accessible fit/reset controls, reduced-motion navigation, and a 5,000-node filtering budget.

Performance budgets for the initial local target: deterministic gate suite under 2 seconds, no graph layout animation, 120/220/320ms control motion only, and no runtime outbound requests. The current production bundle is about 722 kB minified / 228 kB gzip, dominated by Cytoscape and React. Vite reports its 500 kB chunk advisory; no additional layout or motion dependency was added.

## Motion and component attribution

Motion uses one corporate/premium identity: `120ms`, `220ms`, `320ms`, `cubic-bezier(.2,0,0,1)`, transform/opacity transitions only, no looping motion, no overshoot, and a reduced-motion path that removes spatial movement.

The semantic Tabs and Command Palette behaviors were adapted from the MIT-licensed [beUI Tabs](https://beui.dev/components/motion/tabs) and [beUI Command Palette](https://beui.dev/components/blocks/command-palette) source by [Saurabh Chauhan / starc007](https://github.com/starc007/ui-components). Only the relevant focus, keyboard, grouping, and mounted-panel patterns were retained; Tailwind and motion dependencies were not copied.
