# Code View local server

This service is the loopback-only boundary between the static Python analyzer and
the browser canvas. It keeps the graph contract independent from the UI and
contains the compatibility mapping in `LegacySemantics.cpp`.

## Build and run

Requirements: CMake 3.21+, Ninja, Qt 6.8+ with Core, Network, and HttpServer,
Python 3, and Node.js/npm for the canvas. Run these commands from the repository
root, or use `./script/code-view.sh /absolute/path/to/python-repo` to build both
services and start them together.

```sh
npm ci --prefix services/web-canvas
npm run build --prefix services/web-canvas
cmake -S services/local-server -B services/local-server/build -G Ninja \
  -DCMAKE_PREFIX_PATH=/opt/homebrew/opt/qt -DBUILD_TESTING=ON
cmake --build services/local-server/build -j4
./services/local-server/build/code-view-local-server --repo /absolute/path/to/python-repo \
  --web-root services/web-canvas/dist
```

Without built canvas assets, a native-only host shows a readiness page instead
of the dependency canvas.

The process prints one JSON startup record. Open its `url`, including the
session capability in `?token=...`. The UI copies that token into
`Authorization: Bearer <token>`, removes it from the visible URL with
`history.replaceState`, retains it in tab-scoped session storage for reloads,
and uses the bearer on every `/api/*` request.

Useful options:

- `--port 0` selects an ephemeral port and is the default.
- `--web-root /absolute/path/to/services/web-canvas/dist` selects built assets.
- Repeated `--analyzer-arg VALUE` supplies the analyzer argv in order. No shell
  parses it.
- `--editor none|code|cursor|zed|sublime` fixes the editor adapter for the
  entire server session. The default is `none`.
- `--allow-command` enables the second of three manual-launch guards.
- `--no-watch` disables the one-second manifest poll and filesystem hints.

The default analyzer and web root are searched only beside the trusted host
executable. `script/code-view.sh` passes the built web root explicitly. An explicit
`--web-root` must canonicalize to an existing directory.
The default Python executable is selected from the fixed Homebrew, `/usr/local`,
and system locations, not the repository's `PATH`; explicit analyzer argv remains
available for a chosen virtual environment.

## Security boundary

- TCP binds `127.0.0.1` only. A bind error reports whether TCP listen or Qt HTTP
  attachment failed.
- Requests must arrive from a loopback peer and carry a matching `Host` header.
- A present `Origin` must exactly match `http://127.0.0.1:<port>` or
  `http://localhost:<port>`.
- The tokenized `GET /` bootstraps the browser; a clean `GET /` can reload the
  already-downloaded UI. Every `/api/*` route still requires the bearer retained
  by that browser session. A supplied invalid launch token is rejected. A missing
  Origin is accepted for a same-machine client only after bearer validation.
- Responses set a same-origin CSP, `nosniff`, `no-referrer`, `DENY` framing,
  same-origin resource policy, and `Cache-Control: no-store`.
- Static routes read only under a directory descriptor pinned at startup. SPA fallback exists
  only for `/`, `/repository`, `/entry`, `/compare`, `/settings`, and
  `/node/<id>`. Repository source is available only through the source API.
- Repository paths must be relative, normalized, and contained by the repository.
  Config, source, and static reads use descriptor-relative opens with no symlink
  following at any component. This deliberately rejects contained symlinks too.
  Only regular files are read; a substituted FIFO cannot block the host. Source
  and editor targets must already exist.
- Analyzer, Git, tar, launch, and editor processes use program plus argv via
  `QProcess`; no request or config is evaluated by a shell. Read-only Git
  processes disable repository fsmonitor, hooks, external diff, optional locks,
  global/system config, prompts, and inherited Git path overrides. Every Git
  invocation uses `/usr/bin/git`, independent of repository-controlled `PATH`.

## HTTP contract v1

All errors are JSON: `{"error":"message"}`. Unless noted, successful responses
are HTTP 200 JSON. `generation` increases only after a complete graph passes the
v1 importer.

### Project and indexing

`GET /api/v1/project`

```json
{"generation":1,"projectName":"demo","config":{},"project":{},"nodeCount":62,"edgeCount":142}
```

`GET /api/v1/status` is the polling endpoint:

```json
{"state":"ready","generation":1,"lastError":null,"nodeCount":62,"edgeCount":142,"diagnosticCount":0,"tracePath":"/tmp/.../trace.jsonl"}
```

`POST /api/v1/reindex` returns `{"accepted":true}`. Concurrent requests coalesce
into one latest follow-up generation.

### Graph

`GET /api/v1/graph?view=repository|entry` returns the complete selected graph:

```json
{
  "generation": 1,
  "comparison": null,
  "projectName": "demo",
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

`repository` contains every indexed node and edge. `entry` contains exactly
`project.entryReachableNodeIds`, their containment ancestors, and edges whose
two endpoints are selected. Neither view is capped.

The same route supports bounded expansion with
`root=<node-id>&depth=0..8&direction=in|out|both&limit=1..1000&kinds=calls,imports`.
Its graph adds `truncated` and `frontierNodeIds`; this focus query is not the
complete entry view.

When `base=<ref>` resolves, `comparison` carries the same node/edge overlay as
the compare endpoint. If the project has no usable Git HEAD or the subtree is
not present in that commit, graph data still returns and `comparison` is null;
the trace records why comparison was unavailable.

`GET /api/v1/node?id=<id>&incomingCursor=0&outgoingCursor=0&limit=100` returns the
canonical node fields plus `incomingCount`, `outgoingCount`, `consumers`,
`dependencies`, `incomingEdges`, `outgoingEdges`, and nullable next cursors.
`showAll=true` returns all remaining relationships instead of a page.

`GET /api/v1/search?q=<text>&limit=50` returns
`{"generation":1,"results":[<canonical-node>]}`.

`GET /api/v1/source?path=main.py&startLine=1&endLine=200` returns at most 400
lines and 5 MB:

```json
{"generation":1,"path":"main.py","startLine":1,"endLine":20,"text":"..."}
```

The path must already occur in the active graph.

### Git comparison

`GET /api/v1/git/refs` returns local branches, the 20 most recent commits, and
`hasHead`. `GET /api/v1/git/revision?ref=HEAD` returns commit, author, date,
subject, current branch, and worktree dirty state.

`GET /api/v1/compare?base=<local-branch-or-commit>&target=WORKTREE` returns:

```json
{"generation":1,"files":{"base":"HEAD","baseCommit":"...","target":"WORKTREE","files":[],"fileCount":0},"graph":{"base":"HEAD","baseCommit":"...","target":"WORKTREE","nodes":{},"edges":{}}}
```

`nodes` and `edges` each contain `added`, `removed`, `modified`, `unchanged`, and
exact counts. Entries retain the canonical current and/or base documents. Base
refs are verified as commits with `git rev-parse --verify --end-of-options`.
The base tree is exported with `git archive` into a `QTemporaryDir`, extracted
with `/usr/bin/tar`, indexed by the same analyzer, and cached by immutable commit
OID. The service never checks out, stages, commits, or creates a worktree.

`GET /api/v1/git/diff?node=<node-id>&base=HEAD` returns the selected changed
node's graph overlay, `state`, and a bounded Git `unified` file diff. Unchanged
or unknown nodes return 404.

### Optional project launch

`GET /api/v1/launch` returns:

```json
{"generation":1,"display":"python3 main.py","argv":["python3","main.py"],"cwd":"/repo","mode":"manual","configured":true,"allowedByCli":true,"approved":false,"allowLaunch":true,"running":false,"pid":null,"lastExitCode":null,"lastExitNormal":null}
```

Executable launch requires all three guards: an object command with
`"mode":"manual"`, server option `--allow-command`, and a fresh
`POST /api/v1/launch/approve` with the generation of the command displayed in
the confirmation dialog:

```json
{"schemaVersion":"code-view.launch-approval/v2","generation":1}
```

The v2 request contract is `contracts/code-view-launch-approval-v2.schema.json`.
Bodyless, wrong-typed, and stale-generation approvals return HTTP 409. The dialog
freezes its displayed argv, cwd, and generation until closed. A reindex requires
a fresh review, even if the dialog is still open. The HTTP route remains under
`/api/v1`; the graph schema is unchanged.
Then `POST /api/v1/launch/start` consumes the approval and checks the generation
again. Every later start requires another approval. A string `startCommand`
or string `entry.command` is document-only and is never executable.

Indexing in progress or a failed reindex blocks both approval and start, even
when the last successful graph and its generation remain readable. Repair must
produce a successful new generation and a fresh user review. Stop remains
available for an already-running command regardless of launch eligibility.

Approved commands start in an owned Unix session with stdout and stderr sent to
the null device. `POST /api/v1/launch/stop` terminates the owned process group and
its current descendants. On macOS, native process enumeration also finds children
that created a separate session; birth identities are checked before signalling
them. The analyzer and Git snapshot processes use the same cleanup path.
SIGINT and SIGTERM request orderly shutdown, including while initial or snapshot
analysis is waiting. Controller destruction also cleans owned workers.
Approved programs can read, change, or delete accessible files and use the network.
This is lifecycle cleanup, not an execution sandbox: a program that deliberately
double-forks and reparents before discovery must be managed by its own supervisor.

### Open in editor

`POST /api/v1/editor/open` accepts exactly:

```json
{"path":"pkg/service.py","line":12,"column":5}
```

Line and column are positive integers. The fixed adapter emits only its known
argv (`--goto ABSOLUTE:LINE:COLUMN` for Code/Cursor; the location argument for
Zed/Sublime). Adapter `none` returns HTTP 409 with an actionable launch option.
The request cannot select a program or pass arguments.

## Configuration and live updates

`code-view.json` is optional. Concise entry and document forms are accepted:

```json
{"entry":"main.py"}
```

```json
{"startCommand":"python -m pkg.main"}
```

The full strict shape is `contracts/code-view-config-v1.schema.json`. Unknown
top-level and nested keys fail explicitly. Languages must be exactly
`["python"]`. `entry` wins for analysis entry selection. Only object commands
with `argv` and manual mode can execute. `index.exclude` patterns are applied to
watch scans in addition to fixed VCS, cache, virtualenv, and dependency ignores.

The watcher combines `QFileSystemWatcher` hints, a deterministic one-second
manifest scan, 300 ms debounce, latest-wins reindexing, and the same 120-second
deadline used by startup indexing. A failed or timed-out process or
invalid graph retains the last good generation. A successful graph containing
`PY_SYNTAX_ERROR` keeps that file's prior nodes and incident edges, adds the
`stale` modifier, retains the new diagnostic, and removes the empty/unresolved
replacement facts. Fixing the file replaces stale facts on the next generation.
The browser polls both generation and status: a failed reindex displays `lastError`
even when the retained graph's generation is unchanged, and keeps that graph usable.

Trace events are JSONL under `/tmp/code-view-local-server/<repo-hash>/trace.jsonl`
and include generation durations, counts, watcher detection, HTTP status, Git
snapshot/compare timing, launch approval/lifecycle, and failures.

## Verification

```sh
ctest --test-dir services/local-server/build --output-on-failure
./services/local-server/build/code-view-local-server-tests --gate
./services/local-server/build/code-view-local-server-eval
python3 services/local-server/evals/reliability.py
```

The fast gate covers strict graph/config import, reversed ranges, boundary reasons,
compatibility conversions, complete views, stale snapshot merging, and contained regular-file reads.
The full local suite also covers live edit/error/fix behavior, start approval/stop/shutdown, editor argv plus source
checksum, branch/commit/invalid/no-HEAD Git cases, graph snapshot comparison,
and live loopback Host/Origin/bearer/CSP/static isolation. The local HTTP gate
must be allowed to bind one ephemeral `127.0.0.1` port.

The periodic eval indexes the frozen Python fixture, imports the exact NDJSON,
checks complete repository and entry views, diagnostics, and compatibility conversion,
and requires a score of 5/5. The 10k-SLOC performance eval runs five fresh
analyzer/import processes and measures the actual C++ `GraphSnapshot::node`
adjacency path.
The reliability eval requires every check to pass: concurrent source/static
replacement confinement, nonblocking FIFO rejection, SIGINT/SIGTERM cleanup,
one-use command approval, trusted Git lookup, and unchanged source/index bytes.

## Failure boundaries

- The importer caps one record at 1 MB, analyzer output at 100 MB, nodes at
  500,000, and edges at 2,000,000.
- Source reads cap at 5 MB and 400 lines; request JSON caps at 4 KB; static
  assets cap at 10 MB; Git command output caps at 2 MB; base archives cap at
  512 MB.
- Startup, manual, watched, and Git snapshot indexing time out after 120 seconds. Git/tar setup
  commands have bounded waits.
- Static analysis can be incomplete around dynamic Python behavior. Boundary
  nodes retain analyzer reasons and are never promoted to internal calls.
- Python is the v1 producer. Java and Go should add producers for the same
  `code-view.graph/v1` contract; the host and canvas do not need a renamed graph
  model.
