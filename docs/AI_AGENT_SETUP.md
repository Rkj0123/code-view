# AI-agent setup prompt

Copy the prompt below into an AI coding agent after checking out Code View. It
is written to set up the existing application, not to ask the agent to invent a
replacement. Replace `<PYTHON_REPOSITORY>` with the absolute path to the Python
repository you want to inspect.

The prompt keeps the normal path local and read-only. It does not enable command
execution, deploy anything, add a backend, or modify the target repository.

## Copy-paste prompt

```text
Set up and run Code View for this Python repository on this macOS machine.

Code View is the checked-out local application. Work from its existing checkout
and do not replace it with a new implementation.

Target repository: <PYTHON_REPOSITORY>

Follow these steps:

1. Confirm that the current directory is the Code View checkout by checking for:
   script/code-view.sh
   services/python-analyzer/analyzer.py
   services/local-server
   services/web-canvas

   If those paths are not present, locate the local Code View checkout. If it
   is not available, stop and tell me that I must provide it. Do not invent a
   replacement implementation.

2. Check that the target repository path is an existing absolute directory.
   Ask me for the path if I did not provide one. Do not silently analyze the
   Code View checkout itself.

3. Read README.md, docs/USAGE.md, and docs/TECHNICAL.md before changing anything.

4. Check these commands and versions:
   brew
   cmake
   ninja
   python3
   node
   npm

   The required environment is macOS, CMake 3.21 or newer, Ninja, Qt 6.8 or
   newer with Core, Network, and HttpServer, Python 3, and Node.js/npm. If a
   prerequisite is missing and package installation is allowed, install it with:
   brew install cmake ninja qthttpserver node python
   If Homebrew itself is missing, tell me to run this official installer and
   wait for me:
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   Otherwise stop with the exact install command and wait for me.

5. Inspect the target repository for an existing code-view.json. Preserve it.
   Do not rewrite or delete it unless I explicitly ask you to change it.

6. If no entry is configured, inspect source files without running them. Code
   View can detect root main.py, one unique __main__.py, or one unique top-level
   if __name__ == "__main__" guard. Tell me which entry will be used.

7. Start the app from the Code View checkout:
   ./script/code-view.sh "<PYTHON_REPOSITORY>"

   Do not add --allow-command. Do not run the target repository. Do not add an
   editor adapter unless I specifically request editor integration.

8. Wait for the host's single JSON startup record. Read its url field and give
   me the complete tokenized URL. Do not truncate the ?token=... query.

9. If you can open a local browser, open that URL. Otherwise tell me to paste it
   into a browser on this Mac. Do not call external services.

10. Confirm that the page reaches a usable Repository or Entry Focus view. Tell
    me the detected/configured entry and whether indexing completed.

11. Keep the host process running until I ask you to stop it. To stop it, tell me
    to press Ctrl-C in the terminal running the host.

12. Finish with a short report containing:
    - target repository path
    - whether an existing code-view.json was used
    - detected or configured entry
    - complete local URL
    - how to stop the host
    - any setup issue that still needs my decision

Safety rules:

- The normal analysis mode is static and read-only. Never import or execute
  target repository code to diagnose setup.
- Do not add a cloud backend, database, account system, telemetry, hosted LLM,
  unrelated dependency, or deployment step.
- Do not change files in the target repository.
- If setup fails, preserve the exact stderr and identify the failing stage:
  dependency install, web build, CMake configure, native build, initial static
  analysis, or browser startup. Fix only that stage and rerun its smallest
  relevant command.
- Optional command execution is a separate feature. It requires an object
  command with mode manual, the --allow-command host flag, and one fresh UI
  approval. Never enable it without my explicit request.

If the requested target path is unavailable, use this smoke-test path only after
telling me:
./tests/fixtures/tic-tac-toe
```

## How to use the prompt safely

- Replace `<PYTHON_REPOSITORY>` with an absolute path before pasting.
- Keep the target's existing `code-view.json`; the agent should inspect it first.
- Treat the printed tokenized URL as a local capability. Do not put it in a
  commit, issue, log file, or public message.
- Leave the host terminal running while the browser is in use.
- If an agent proposes `--allow-command`, stop and ask it to follow the normal
  read-only path unless you intentionally want execution.
- If the agent reports an error, ask it to show the first failing command and
  its exact stderr before approving any change.
