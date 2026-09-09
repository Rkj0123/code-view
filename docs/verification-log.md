# Documentation verification log

This log records the checks for the standalone Code View documentation. All
commands were run from the repository root unless a working directory is shown.

## Pass 1: static contract checks

- [x] Confirm every README and guide link points to an existing file.
- [x] Confirm every screenshot path exists and contains a real PNG capture.
- [x] Confirm the public README and guides contain no old product or repository
      references.
- [x] Confirm setup, build, and evaluation commands refer to files present in
      this repository.
- [x] Confirm configuration and launch examples match their versioned schemas.
- [x] Run `python3 -m unittest discover -s tests/docs -p 'test_*.py'` (8 tests, OK).
- [x] Run `python3 -m unittest discover -s tests/installer -p 'test_*.py'` (5 tests, OK).
- [x] Run `sh -n` on the three Code View shell scripts (OK).
- [x] Run `sh -n install.sh script/code-view-command.sh` (OK).

The Homebrew installer URL in the docs was checked against Homebrew's current
official page. It was not executed during verification because Homebrew was
already installed on this Mac; the package command was run and completed.

## Pass 2: clean setup and runtime smoke test

- [x] Build the web canvas and native host from a clean isolated worktree.
- [x] Start the bundled tic-tac-toe fixture with the documented launcher.
- [x] Copy the printed tokenized URL into a local browser.
- [x] Run `code-view` from the fixture directory; it passed the current
      directory through to the host and invoked the browser opener with the
      printed tokenized URL.
- [x] Run the installer smoke path with dependency installation skipped and a
      temporary command directory; the installed command returned its help text.
- [x] Open Repository and Entry Focus; select a symbol and inspect evidence.
- [x] Export JSON and PNG, switch light/dark mode, and stop with `Ctrl-C`.
- [x] Capture the six screenshots in `docs/images/` from the real app.

## Pass 3: naive-user walkthrough

- [x] Follow the README quick start without reading source code.
- [x] Configure an explicit entry in `code-view.json`.
- [x] Include tests and reveal modules using the documented filter/configuration.
- [x] Trigger a refresh after a source edit and read the recovery instructions.
- [x] Read the optional execution warning without enabling execution.
- [x] Recheck screenshot captions, links, and copy-paste commands against the
      current UI.

## Results

Pass 1 completed with the deterministic checks above. Pass 2 completed with the
real launcher, host, browser, fixture, exports, theme switch, and settled image
captures. Pass 3 completed by following the numbered README/usage workflow and
checking the UI states against the captures. Temporary command logs and browser
profiles stay outside the repository; the six PNGs are tracked with Git LFS.

Measured results:

| Check | Result |
| --- | --- |
| Documentation contract | 8 tests passed. |
| Installer contract | 5 tests passed; shell syntax, install command creation, current-directory forwarding, URL opening, safe refusal to overwrite an unrelated command, and host-failure propagation covered. |
| Python fixture | 62 nodes, 142 edges, 0 diagnostics; exact golden graph passed. |
| 10,000-line performance | Standard cold p95 597 ms; import-heavy cold p95 470 ms; adjacency p95 0.003834 ms. |
| Web periodic suite | 15 contract checks and 6 browser workflows passed. |
| Browser exports | Selected-flow JSON contained 12 nodes and 11 edges; PNG export was 96,084 bytes and had a valid PNG header. |
| Native suite | 1 CTest target passed; 148 native checks passed. |
| Reliability suite | 20 checks passed with zero source/static leaks. |

## Open-source installer verification, 2026-09-08

- Pass 1: installer regression tests and documentation link/fragment checks.
- Pass 2: real browser and C++ builds; 41 analyzer tests, 59 browser unit tests,
  69 host gate checks, six browser workflows, and 20 host reliability checks passed.
- Pass 3: installed into a temporary bin directory, ran code-view from a Python
  directory containing a space, verified the browser-open request, HTTP 200,
  correct graph project and symbol, unchanged source, and clean shutdown.
- New Homebrew provisioning and alternate CPU hardware were simulated, not run
  on a clean machine. This release supports macOS/Python only.
- Publication and anonymous URL checks are recorded separately after release.
