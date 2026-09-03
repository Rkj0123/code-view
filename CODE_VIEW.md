# Code View

Code View turns a Python repository into a local, read-only dependency canvas. It shows files, modules, classes, functions, variables, imports, calls, inheritance, construction, reads, writes, decorators, type use, API calls, inbound consumers, outbound dependencies, source, and local Git changes. Analysis is static: repository code is parsed but never imported or executed.

## Run on macOS

Install the local prerequisites:

```sh
brew install cmake ninja qthttpserver node python
```

Start Code View for a repository:

```sh
./script/code-view.sh /absolute/path/to/python-repository
```

The host prints a tokenized `http://127.0.0.1:<port>/` URL. Open that URL in a browser. The server binds only to loopback and rejects invalid Host, Origin, and bearer-token requests.

Try the bundled tic-tac-toe project:

```sh
./script/code-view.sh "$PWD/tests/fixtures/tic-tac-toe"
```

The first run installs locked web dependencies and builds the two local services. Later runs are incremental.

## Configure a repository

Add `code-view.json` at the repository root. An explicit entry wins over automatic detection.

```json
{
  "schemaVersion": 1,
  "entry": {
    "file": "main.py",
    "symbol": "main",
    "command": {"argv": ["python3", "main.py"], "mode": "manual"}
  },
  "languages": ["python"],
  "index": {
    "tests": "exclude",
    "modules": "hide",
    "externalPackages": "collapse",
    "exclude": ["generated/**"]
  },
  "canvas": {
    "initialView": "entry-focus",
    "relationships": {
      "contains": true,
      "imports": true,
      "calls": true,
      "inherits": true,
      "constructs": true,
      "reads": true,
      "writes": true,
      "decorates": true,
      "type_uses": true,
      "api_calls": true,
      "test_covers": false
    }
  },
  "git": {"base": "HEAD"}
}
```

Tests are excluded, package/module nodes are hidden, external dependencies are collapsed, and test coverage is off by default. The canvas controls can change those display choices. `index.tests` must be changed to `include` before test files are analyzed.

## Read the flow

Search for a function, or select one on the canvas. Selected flow places callers
on the left, the selected symbol in the middle, and dependencies on the right.
Compact file/class headers and orthogonal lines follow the original Sourcetrail
presentation. Blue indicates incoming connections; gold indicates outgoing ones.

Use Trace to inspect any relationship in the filtered graph, its resolution,
and exact source occurrences. All connections restores the full filtered graph.
The footer reports the displayed subset; JSON export uses that same subset.
Pan/zoom keeps larger views readable, while Fit deliberately shows the overview.
The toolbar filter button controls visible symbols and relationship types.

## Optional command execution

Commands are documentation by default. Execution requires all three gates:

1. A config command with `"mode": "manual"`.
2. Starting the host with `--allow-command`.
3. Approving that individual start in the UI.

```sh
./script/code-view.sh /absolute/path/to/repository --allow-command
```

Commands are argv arrays passed directly to a child process. Code View never invokes a shell. Approval applies to one start only.
The confirmation freezes the displayed argv, working directory, and index
generation. Changes or indexing failures invalidate approval; repair requires
a fresh review. Stop remains available for a running command. Approved commands
are not sandboxed and can change accessible files or use the network.

## Behavior and limits

- File watching rebuilds an atomic graph generation after edits. Invalid Python preserves that file's last good facts and reports the syntax error. Failed reindexing also shows an error when the generation is unchanged, without discarding the usable graph.
- Git comparison reads local worktree, branch, and commit data without checkout or repository mutation.
- External and dynamic targets stay visible as collapsed `external` or `unresolved` boundaries. Code View does not invent a target when Python cannot resolve one statically.
- Repositories above 300 symbols open at file boundaries, with known externals grouped by package. Search centers the chosen symbol; a file with more than 150 descendants stays collapsed while the Inspector shows the symbol's exact source and canonical relationships. If no entry is detected, Repository opens and Entry Focus stays disabled until one is configured.
- Source viewing is read-only. Editor opening is available only through a fixed local adapter selected at host startup.
- Light and dark modes follow the macOS appearance on first use. The toolbar toggle persists a manual choice in local browser storage.
- Python is the first analyzer. The graph contract is language-neutral so Java and Go analyzers can emit the same schema later.
- The qualified MVP envelope is 10,000 Python source lines. It is a performance target, not a repository-size rejection limit.

## Service boundaries

- `services/python-analyzer`: deterministic Python AST analysis, tests, and golden eval.
- `services/local-server`: Qt loopback host, graph generations, file watching, Git reads, command guard, and security boundary.
- `services/web-canvas`: React/Cytoscape read-only canvas, inspector, search, filters, tabs, saved views, bookmarks, export, and reduced-motion UI.
- `contracts`: versioned config and language-neutral graph schemas.

Run the local gate and periodic evals:

```sh
./script/code-view-gate.sh
./script/code-view-eval.sh
```

The periodic lane includes standard and import-heavy exact 10,000-SLOC Python
repositories. It fails if either cold static analysis shape exceeds five seconds
or adjacency queries exceed 100 ms.

Enable the repository gate once per clone:

```sh
git config core.hooksPath .githooks
```

The full host suite is built with:

```sh
cmake -S services/local-server -B services/local-server/build -G Ninja -DBUILD_TESTING=ON
cmake --build services/local-server/build
ctest --test-dir services/local-server/build --output-on-failure
```
