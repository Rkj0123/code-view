# Code View usage guide

This guide is for someone using Code View for the first time. It covers
installation, starting a repository, reading the canvas, changing visibility,
inspecting evidence, comparing local changes, and recovering from errors.

## 1. Before you start

Code View currently runs on macOS and analyzes Python repositories. It is a
local application: the host listens on `127.0.0.1`, and the browser connects to
that host with a short-lived capability token.

The normal indexing path is static and read-only. Code View reads Python files,
builds a graph, and never imports or executes the repository being analyzed.

You need:

- a checkout of the Code View repository;
- Homebrew;
- CMake 3.21 or newer;
- Ninja;
- Qt 6.8 or newer with Core, Network, and HttpServer;
- Node.js and npm; and
- Python 3.

## 2. Install Code View

Open Terminal and move to the checkout:

```sh
cd /absolute/path/to/code-view
```

Install the macOS prerequisites:

If `brew --version` fails, run the official Homebrew installer and follow its
printed PATH instructions:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

```sh
brew install cmake ninja qthttpserver node python
```

Check that the commands are available:

```sh
cmake --version
ninja --version
node --version
npm --version
python3 --version
```

The launcher installs the locked web dependencies on its first run. You do not
need to install a Python package for the analyzer.

Documentation screenshots are tracked with Git LFS. If a screenshot file is a
small text pointer, install Git LFS and refresh the files:

```sh
brew install git-lfs
git lfs install
git lfs pull
```

## 3. Start and stop Code View

Start the bundled smoke-test repository:

```sh
./script/code-view.sh "$PWD/tests/fixtures/tic-tac-toe"
```

The launcher performs four jobs in order:

1. runs `npm ci` when the web dependencies are missing;
2. builds the React canvas;
3. configures and builds the local CMake host; and
4. starts the host against the repository path you supplied.

The host prints one JSON line. Copy the complete `url` field into a browser on
the same Mac. Do not remove the `?token=...` portion:

```text
http://127.0.0.1:53211/?token=YOUR_SESSION_TOKEN
```

After the first page load, the browser keeps the token in tab-scoped session
storage and removes it from the visible URL. A clean reload in that tab keeps
working. Closing the tab clears the retained token.

To stop the host, return to the Terminal window running it and press `Ctrl-C`.
The host shuts down its analyzer, watcher, Git, and optional command workers.

## 4. Analyze your first repository

Once the smoke test works, stop it and start your own repository:

```sh
./script/code-view.sh /absolute/path/to/your/python-repository
```

Use an absolute path. Copy the new tokenized URL printed by the host into the
browser. Code View reads the repository; it does not run its application.

### Entry detection order

The analyzer chooses the entry point in this order:

1. `entry` in `code-view.json`;
2. a supported `startCommand` entry hint;
3. a root `main.py`;
4. a unique `__main__.py`; or
5. a unique top-level `if __name__ == "__main__"` guard.

If no entry can be identified, `Repository` still works and `Entry Focus` is
disabled. Add an explicit entry when a project has several possible starts.

## 5. Configure a repository

Create `code-view.json` in the root of the repository being analyzed. Start
with the smallest form:

```json
{
  "schemaVersion": 1,
  "entry": "main.py"
}
```

The supported full shape is documented and validated by
[`contracts/code-view-config-v1.schema.json`](../contracts/code-view-config-v1.schema.json).
Here is a practical example:

```json
{
  "schemaVersion": 1,
  "entry": {
    "file": "main.py",
    "symbol": "main"
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
    "relationships": [
      "contains", "imports", "calls", "inherits", "constructs",
      "reads", "writes", "decorates", "type_uses", "api_calls"
    ]
  },
  "git": {"base": "HEAD"}
}
```

Rules that matter most:

- `entry` may be a file string or an object with `file` and optional `symbol`.
- An explicit `entry` wins over `startCommand` and automatic detection.
- `startCommand` may be a string for documentation and entry discovery, or an
  object with `argv` and `mode`.
- `languages` must currently be exactly `["python"]`.
- `index.tests: "include"` includes test files. Omitted or `"exclude"` keeps
  them out.
- `index.modules: "show"` reveals module and package boundaries. Omitted or
  `"hide"` keeps those derived groups hidden.
- `externalPackages` currently accepts only `"collapse"`.
- `canvas.initialView` is `"entry-focus"` or `"whole-repo"`.
- `canvas.relationships` accepts either a list of names or a boolean object.
- Omit `test_covers` to keep test coverage off. The UI can enable it later.
- `index.exclude` contains repository-relative glob patterns.
- Unknown keys and explicit nulls are rejected. Remove a setting rather than
  writing `null` when you want its default.

## 6. Understand the canvas

### The two top-level views

- `Repository` contains every indexed node and relationship allowed by the
  current filters.
- `Entry Focus` contains the complete statically reachable flow from the entry,
  plus the containment ancestors needed to understand it.

Inside either view, use the presentation switch:

- `All connections` shows the full filtered graph.
- `Selected flow` shows callers on the left, the selected symbol in the middle,
  and dependencies on the right.

Selected flow is the readable path for a busy repository. It does not discard
facts: `Trace` still lists every relationship in the filtered graph, and JSON
export reports the currently displayed scope.

### Canvas terms

- **Node**: a file, class, function, method, variable, external package, or
  unresolved target.
- **Relationship**: a directed `contains`, `imports`, `calls`, `inherits`,
  `constructs`, `reads`, `writes`, `decorates`, `type_uses`, `api_calls`, or
  `test_covers` fact.
- **Boundary**: a collapsed file, class, package, module, external package, or
  unresolved target that preserves context without filling the canvas.
- **Canonical graph**: the analyzer's original IDs, edges, locations, and
  metadata. Presentation rows are derived from it and do not rename its facts.
- **Entry scope**: the indexed entry and everything statically reachable from it.
- **Generation**: the successful graph revision currently shown by the host.

All relationships point from consumer to dependency. `contains` points from an
owner to its child. In Selected flow, blue marks inbound connections to the
selected symbol and gold marks outbound connections. Lane headings and
arrowheads repeat the direction, so color is not the only cue.

![Selected flow showing callers left, the selected symbol in the middle, and dependencies right](images/01-entry-focus-light.png)

## 7. Read a selected flow

1. Search for a symbol or select one from the canvas.
2. Select `Selected flow` if it is not already active.
3. Read the `INCOMING · USED BY` lane for consumers and callers.
4. Read the `SELECTED` lane for the symbol and its owned rows.
5. Read the `OUTGOING · USES` lane for dependencies.
6. Follow an arrow to select its relationship, or choose it from `Trace`.
7. Use the Inspector to inspect the exact source occurrence.

File and class headers may repeat in different lanes. They identify ownership;
the arrow always ends at a leaf symbol row, never at a container label.

## 8. Search and navigate

Open the command palette with `⌘ K` on macOS or `Ctrl K` elsewhere. Search by
label, qualified name, kind, or command. Select a result to open its tab and
center the canvas on it.

The top tabs keep recently opened symbols available. Select a symbol to open
its Inspector and a tab. Use the bookmark button in the Inspector to retain a
symbol in the filter panel.

Keyboard controls:

| Key | Action |
| --- | --- |
| `⌘/Ctrl-K` | Open the command palette |
| `1` | Switch to Repository |
| `2` | Switch to Entry Focus when an entry exists |
| `F` | Fit the visible graph |
| Arrow keys | Move between graph nodes |
| `E` | Select a relationship from the focused flow |
| `Enter` | Collapse or expand a selected group |
| `Escape` | Clear overlays and selection, then restore All connections |

## 9. Control filters and boundaries

Open the filter button in the top bar. Filters are display choices; changing
one does not rewrite the analyzer output.

You can independently control:

- directories, files, modules, packages, classes, functions, methods, variables,
  external packages, and unresolved nodes;
- each canonical relationship kind;
- whether tests are included; and
- whether only changed nodes are shown.

Tests and package/module groups are excluded or hidden by default. External
packages stay collapsed. To reveal them, open the filter panel and enable the
relevant category. To make a huge repository manageable, collapse a boundary
with a double-click or `Enter`; use Search to recover an exact symbol.

![Inspector showing a selected relationship and its source evidence](images/03-inspector-light.png)

![Filter panel showing node and relationship visibility controls](images/04-filters-light.png)

## 10. Inspect source and relationship evidence

Selecting a symbol shows its kind, qualified name, source preview, status, and
inbound/outbound lists. Selecting an edge shows its relationship kind,
resolution, confidence, occurrence count, and boundary reason when present.

For an edge with locations:

1. open the relationship in `Trace` or click its line;
2. choose a source occurrence in the Inspector;
3. read the path, line, column, and bounded source text; and
4. use `Copy location` or `Open in Editor` when available.

Source requests are read-only and limited to 400 lines and 5 MB. `Open in
Editor` uses only the fixed adapter selected when the host started. If no
adapter is configured, copy the location and open it manually.

Source evidence is tied to the selected relationship. If you switch selection
while a request is pending, an older response is ignored.

## 11. Use Git comparison

Git comparison is local and never checks out or modifies a branch.

1. Open the `WORKTREE vs` selector.
2. Choose `HEAD`, a local branch, or one of the recent local commits.
3. Use the `changed only` filter if you want only added, modified, or removed
   nodes.
4. Select a changed node or edge.
5. Read its `added`, `modified`, `removed`, or `unchanged` state in the
   Inspector and open the bounded unified diff.

![Repository view with the local Git comparison control](images/05-git-compare-dark.png)

On a non-Git repository, comparison evidence is unavailable and the graph still
loads. A missing branch, invalid ref, or absent `HEAD` is reported without
inventing a diff.

## 12. Save views and bookmarks

Open the filter panel to use `Save view`. A saved view stores its name, mode,
node/relationship filters, test and changed-only choices, and collapsed groups.
Apply it later from the same panel. Delete a saved view when it is no longer
useful.

Bookmarks store canonical symbol IDs locally in the browser. They do not upload
source or graph data. If a symbol disappears after a reindex, remove its stale
bookmark from the panel.

## 13. Export JSON and PNG

Use the canvas tool buttons:

- the braces button downloads a JSON document;
- the image button downloads a PNG of the current canvas view.

Exports use the current presentation scope. If the footer says `1 relationship
of 92`, the JSON contains that one selected relationship and its endpoint nodes.
Switch to `All connections` before exporting the full filtered graph. Exported
IDs, parent references, source ranges, and edge locations remain canonical.

## 14. Switch light/dark mode

Code View follows the macOS appearance the first time it opens. Use the sun/moon
button to switch manually. The choice is stored in local browser storage for
future visits to that browser profile. It is not sent to a server.

## 15. Watch edits and recover from errors

The default host watches the repository and rebuilds a new graph after edits.
The browser polls `/api/v1/status` and refreshes when a successful generation
arrives.

When a file is temporarily invalid, the last successful graph stays visible and
the error appears in the status area. Fix the source or configuration, press
Refresh, and wait for the next successful generation. If the generation number
does not change because the reindex failed, the error still remains visible.

An indexing failure also disables optional command approval. A running command
can still be stopped. Repairing the source produces a new generation and
requires a fresh approval.

For a one-off static snapshot, pass `--no-watch` to the host command. The
launcher forwards extra arguments:

```sh
./script/code-view.sh /absolute/path/to/repository --no-watch
```

## 16. Optional command execution

Do not enable this for normal browsing. Commands are documentation-only unless
all three gates below are present:

1. `code-view.json` contains an object command with `"mode": "manual"`;
2. the host starts with `--allow-command`; and
3. you approve that exact command in the UI dialog.

Example configuration:

```json
{
  "schemaVersion": 1,
  "entry": {
    "file": "main.py",
    "command": {
      "argv": ["python3", "main.py"],
      "mode": "manual"
    }
  }
}
```

Start with the explicit opt-in:

```sh
./script/code-view.sh /absolute/path/to/repository --allow-command
```

The dialog freezes the displayed argv, working directory, and graph generation.
Approval is single-use. Reindexing, an indexing error, or a generation change
invalidates it. The v2 approval body is defined in
[`contracts/code-view-launch-approval-v2.schema.json`](../contracts/code-view-launch-approval-v2.schema.json).

Arguments are passed directly to a child process. Code View never invokes a
shell. Approved processes are not sandboxed and may read, modify, or delete
accessible files and use the network. Stop is available while the process is
running, even if a later index failure revokes launch eligibility.

![Optional command approval dialog](images/06-command-approval-light.png)

## 17. Narrow-window controls

At a narrow width, the filter panel and Inspector become overlay panels. The
toolbar buttons open them one at a time, and hidden panels are removed from the
keyboard tab order. The canvas remains reachable, and the same Repository,
Entry Focus, Trace, selection, export, and theme controls are available.

## 18. Troubleshooting

### Homebrew or tool errors

Run the version checks in [Install Code View](#2-install-code-view). If a
command is missing, install its package and open a fresh Terminal window.

### Qt is not found

Confirm that `qthttpserver` is installed. For a manual build, set the Homebrew
Qt prefix:

```sh
cmake -S services/local-server -B services/local-server/build -G Ninja \
  -DCMAKE_PREFIX_PATH=/opt/homebrew/opt/qt -DBUILD_TESTING=ON
```

The launcher normally supplies the rest of the build steps.

### The browser shows a readiness page

Use the `url` from the newest host startup JSON and include its complete token.
If the tab was closed, restart the host and use the new URL.

### Entry Focus is disabled

Add `{"schemaVersion": 1, "entry": "main.py"}` to the target repository's
`code-view.json`, or continue in Repository view. An entry file must be inside
the repository and included by the index filters.

### Configuration is rejected

Use the strict schema in `contracts/code-view-config-v1.schema.json`. Remove
unknown keys and explicit null values. Check that `languages` is `["python"]`,
that test/module settings use their documented enum values, and that command
`argv` is a non-empty array of strings.

### Refresh failed or the graph is stale

Read the error shown in the status area, fix the source/configuration, then
press Refresh. The last good graph is intentionally retained. A syntax error
does not replace it with guessed relationships.

### A relationship is missing

Check the relationship filter and whether tests/modules are hidden. If the
target is dynamic, external, or unresolved, inspect its boundary reason. Use
`Trace` to verify whether it is outside the Selected flow but still present in
the filtered canonical graph.

### Git comparison is unavailable

Confirm the target is a Git repository with a readable local ref. Code View does
not fetch remotes and does not create commits or checkouts.

### Editor opening does not work

Start the host with a fixed adapter, for example `--editor code` or
`--editor cursor`. The adapter cannot be selected by a browser request. `Copy
location` works without an adapter.

### Optional approval is stale

Close the dialog and open it again after the graph is ready. A changed command,
generation, or failed reindex always requires a fresh review.

### The repository is large

Large repositories open at collapsed boundaries. Use Search, filters, and
Selected flow first. `Fit` is an overview; pan and zoom are the readable way to
inspect a graph that extends beyond the viewport.

### Port or listen failure

The default port is `0`, which chooses an available loopback port. If you need a
fixed port, pass it to the host:

```sh
./script/code-view.sh /absolute/path/to/repository --port 53211
```

Choose another port if that one is already in use.

## 19. Privacy and local-only behavior

- The host binds to `127.0.0.1` only.
- The tokenized URL bootstraps one local browser session.
- API requests require the retained bearer token.
- Repository source is served only by the local host.
- No cloud backend, account, telemetry, or hosted language model is required.
- Static indexing does not import or execute repository code.
- Optional execution is a separate, explicit capability and is not sandboxed.
- Bookmarks, saved views, and theme choice stay in browser-local storage.

## 20. Current limits

Python is the current analyzer. Java and Go are future producers for the same
language-neutral graph contract. Dynamic imports, reflection, monkey patching,
unknown receiver types, and other runtime-only behavior can end at explicit
external or unresolved boundaries. The 10,000-line target is a measured
performance target, not a hard size limit.
