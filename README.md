# Code View

Code View is a local, read-only code-flow canvas for understanding Python
repositories. Point it at a repository and see how files, classes, functions,
variables, imports, calls, inheritance, reads, writes, decorators, type use,
API calls, source locations, and local Git changes connect.

Everything runs on your Mac. Static analysis parses Python source without
importing or executing the repository.

[Quick start](#quick-start) · [Usage guide](docs/USAGE.md) ·
[Technical details](docs/TECHNICAL.md) · [AI-agent setup](docs/AI_AGENT_SETUP.md)

## One-command installation

On a Mac with an internet connection, install Code View with one command:

```sh
curl -fsSL https://raw.githubusercontent.com/Rkj0123/code-view/main/install.sh | bash
```

The installer checks for Homebrew, CMake, Ninja, Qt HttpServer, Node.js, npm,
Python 3.10+, and Git. It installs missing tools and builds the app before reporting
success. It keeps the app under
`~/.local/share/code-view`, and adds the `code-view` command to Homebrew's bin
directory. If that directory is not on `PATH`, the installer prints the one
line to add.

After installation, move into any Python repository and run:

```sh
cd /absolute/path/to/your/python-repository
code-view
```

Code View scans the current directory, starts the local host, and opens the
tokenized localhost URL in your default browser. Press `Ctrl-C` in that same
terminal to stop it. For a reviewable manual install, see
[Install Code View](docs/USAGE.md#2-install-code-view).

![Code View showing callers on the left, a selected Python symbol in the middle, and dependencies on the right](docs/images/01-entry-focus-light.png)

*Selected flow keeps the symbol you are studying in the middle and makes each
connection easy to follow.*

## What Code View does

| Need | Use Code View |
| --- | --- |
| Find who uses a symbol | Select it and inspect inbound consumers. |
| Understand dependencies | Read the outbound column and follow its arrows. |
| See the whole project | Open `Repository`, then choose `All connections`. |
| Inspect exact evidence | Open a relationship or source occurrence in the Inspector. |
| Make a dense graph readable | Collapse boundaries, filter categories, or choose `Selected flow`. |
| Review local changes | Compare `WORKTREE` with a local branch or commit. |
| Run a repository command | Opt in explicitly; execution is disabled by default and requires three guards. |

## Requirements

The current release supports macOS and Python repositories. Install Homebrew,
then install CMake 3.21+, Ninja, Qt 6.8+ with Core/Network/HttpServer, Node.js,
npm, and Python 3.10+:

If `brew --version` fails, install Homebrew with its official installer, follow
the PATH instruction it prints, and open a new Terminal window:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

```sh
brew install cmake ninja qthttpserver node python
```

If a package is already installed, Homebrew reports that it is current.

The screenshots in this repository use Git LFS. Install it if your clone shows
an image pointer instead of an image:

```sh
brew install git-lfs
git lfs install
```

## Quick start

Follow these steps from a terminal. The bundled launcher builds the browser
canvas and local host for you.

### 1. Open the Code View checkout

```sh
cd /absolute/path/to/code-view
```

Use the directory containing `script/code-view.sh`, `services/`, and `tests/`.

### 2. Verify the tools

```sh
cmake --version
ninja --version
node --version
npm --version
python3 --version
```

### 3. Start the bundled example

```sh
./script/code-view.sh "$PWD/tests/fixtures/tic-tac-toe"
```

The first run installs locked web dependencies, builds the canvas, configures
and builds the local host, indexes the fixture, and starts the server. Later
runs reuse build outputs.

### 4. Open the browser

The host prints one compact JSON startup record. Copy the complete `url` value,
including `?token=...`, into a browser on the same Mac:

```text
http://127.0.0.1:53211/?token=YOUR_SESSION_TOKEN
```

The token bootstraps that tab. Code View retains it in tab-scoped storage and
removes it from the visible address bar.

### 5. Explore the example

1. Leave `Repository` selected for the complete indexed graph.
2. Select `Entry Focus` for the statically reachable entry flow.
3. Search for `Game.play`.
4. Choose `Selected flow` to place callers left, the selected symbol in the
   middle, and dependencies right.
5. Select an arrow or use `Trace` to inspect its relationship and source range.

![Repository overview with compact file and class boundaries](docs/images/02-repository-overview-dark.png)

*Repository view keeps the full filtered graph available. Pan and zoom when a
large repository extends beyond the viewport.*

### 6. Analyze your repository

Press `Ctrl-C` in the terminal, then run:

```sh
./script/code-view.sh /absolute/path/to/your/python-repository
```

Copy the new tokenized URL into the browser. Indexing does not run your
application.

### 7. Stop Code View

Return to the host terminal and press `Ctrl-C`. The host shuts down its owned
workers before exiting.

## The 60-second tour

- `Repository` shows every indexed node and relationship in the current filters.
- `Entry Focus` shows the complete statically reachable flow from the detected
  or configured entry.
- `All connections` restores every relationship in the filtered graph.
- `Selected flow` creates a readable caller/selection/dependency view.
- `Trace` reaches every filtered relationship, including those outside the
  focused flow.
- The Inspector shows qualified names, source, occurrences, boundary reasons,
  and Git evidence.
- The filter button controls node kinds, relationship kinds, tests, and changed
  only mode.
- `Fit` shows an overview; `Reset` restores the viewport.
- JSON and PNG export use the current visible scope.
- The sun/moon button switches and remembers light/dark mode locally.

![Inspector showing a selected relationship and its source evidence](docs/images/03-inspector-light.png)

*The Inspector keeps the selected symbol's exact source and relationship evidence beside the canvas.*

![Filter panel showing node and relationship visibility controls](docs/images/04-filters-light.png)

*Filters change what is visible without changing the canonical graph.*

## Default visibility

- tests excluded
- package and module boundaries hidden
- external packages collapsed
- every canonical relationship enabled except `test_covers`
- source viewing enabled
- command execution disabled
- system light/dark preference used on first launch

Change these choices in the filter panel or in `code-view.json`.

## Configure an entry point

Create `code-view.json` in the repository root. The smallest useful file is:

```json
{
  "schemaVersion": 1,
  "entry": "main.py"
}
```

The full strict schema is in
[`contracts/code-view-config-v1.schema.json`](contracts/code-view-config-v1.schema.json).
An explicit `entry` wins over an accepted `startCommand` entry hint and over
automatic detection. `index.tests: "include"` enables test files;
`index.modules: "show"` reveals module/package boundaries. Unknown keys are
rejected.

## Optional command execution

Commands are documentation-only by default. Running one requires:

1. an object command with `"mode": "manual"` in the config;
2. the host started with `--allow-command`; and
3. approval of the exact argv, working directory, and graph generation in the UI.

```sh
./script/code-view.sh /absolute/path/to/repository --allow-command
```

Code View passes the approved argument array directly to a child process and
never invokes a shell. Approval is single-use and is invalidated by reindexing
or a failed index. The process is not sandboxed. See
[Optional command execution](docs/USAGE.md#16-optional-command-execution).

## Troubleshooting

- **`brew: command not found`**: install Homebrew, reopen the terminal, and
  rerun the prerequisite command.
- **CMake cannot find Qt**: confirm `qthttpserver` is installed and follow the
  manual build in the usage guide.
- **Readiness page**: use the URL printed by the host and include its token.
- **Entry Focus disabled**: add an `entry` or use `Repository`.
- **Refresh failed**: fix the reported source/config error and press Refresh;
  the last successful graph remains usable.
- **Missing relationship**: check filters and whether the target is external or
  unresolved.

## Documentation

- [Usage guide](docs/USAGE.md): complete first-run and day-to-day instructions.
- [Technical details](docs/TECHNICAL.md): architecture, contracts, lifecycle,
  security, API routes, limits, and development.
- [AI-agent setup prompt](docs/AI_AGENT_SETUP.md): a copy-paste macOS setup
  prompt.
- [Documentation verification log](docs/verification-log.md): evidence for the
  commands, links, screenshots, and walkthroughs.

## Current scope and limits

Python is the first supported analyzer. The graph contract is language-neutral,
so Java and Go producers can be added later without changing the host or
canvas. Dynamic Python behavior that cannot be named safely remains an explicit
external or unresolved boundary. The initial performance target is 10,000
Python source lines. It is a benchmark target, not a size rejection limit.

## Development checks

```sh
./script/code-view-gate.sh
./script/code-view-eval.sh
```

See the [technical details](docs/TECHNICAL.md#17-build-and-verification) for
individual commands.

## License and community

Code View is free and open source under [GNU GPL version 3](LICENSE.txt).
You may use, study, modify, and redistribute it, including commercially, under
that license. Distributed derivative works must preserve its license obligations.

This project builds on Sourcetrail by Coati Software and its contributors.
Inherited copyright notices and third-party licenses remain in force. The
Sourcetrail trademark is not included in the software license. See
[licensing scope](NOTICE.md) and [contributing](CONTRIBUTING.md).
