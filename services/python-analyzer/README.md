# Python analyzer

Static, local-only Python indexing for the Code View graph contract. It uses only
the Python standard library and never imports or executes repository code.

## Run

From the repository root:

```sh
python3 services/python-analyzer/analyzer.py tests/fixtures/tic-tac-toe --trace
```

Use `--format ndjson` for streaming records. `--trace` writes one timing record to
stderr; graph JSON remains deterministic. Tests are excluded by default. Pass
`--include-tests` or set `"includeTests": true` in `code-view.json` to include them.

Supported configuration:

```json
{"schemaVersion": 1, "entry": "main.py", "includeTests": false}
```

Start-command shorthand is separate from an explicit entry:

```json
{"schemaVersion": 1, "startCommand": "python3 main.py"}
```

The full strict v1 shape is in
`contracts/code-view-config-v1.schema.json`, with a runnable example in
`contracts/code-view-config-v1.example.json`. `entry` may select a `file` and
optional `symbol`, plus document-only command metadata in string or `{argv,
mode}` form; it takes precedence over `startCommand`. `index.tests` takes
precedence over the `includeTests` shorthand. `canvas.relationships` accepts an
enabled-name array or a relationship-to-boolean object. Repository-relative
`index.exclude` globs take precedence over test inclusion.

Alternatively, `startCommand` accepts `python FILE`, `python3 FILE`, `python -m
MODULE`, or `python3 -m MODULE`. It is parsed only to find an entry and is never
executed. With no configuration, entry discovery checks root `main.py`, a unique
`__main__.py`, then a unique top-level `__name__ == "__main__"` guard.

## Relationships

All public relationships point from consumer to dependency, except containment.
The supported names are `contains`, `imports`, `calls`, `inherits`, `constructs`,
`reads`, `writes`, `api_calls`, `test_covers`, `decorates`, and `type_uses`.

`constructs` targets the class. Known `__new__`, `__init__`, inherited members,
descriptors, and callable instances follow Python's method-resolution order.
Known lazy values apply effects only when a visible consumer advances them.
External and unresolved nodes include a reason why analysis stopped. Dynamic
dispatch is never guessed.

## Verify

```sh
python3 -m unittest discover -s services/python-analyzer -p 'test_*.py'
python3 evals/python-analyzer/evaluate.py
python3 services/python-analyzer/analyzer.py tests/fixtures/tic-tac-toe --format ndjson --trace
```

The fixture gate is budgeted below two seconds. The eval requires 100% precision
and recall for its frozen graph plus adversarial control flow, lazy activation,
decorators, late binding, dynamic namespaces, call arguments, inheritance, import
collisions, annotations, integrity, strict configuration, package entry, and
40-run concurrency checks. The periodic performance lane covers both standard
and import-heavy exact 10,000-SLOC shapes.

## Failure boundaries

- Invalid configuration, unsafe paths, and shell syntax fail before analysis.
- Syntax errors produce diagnostics and an empty module body.
- Symlinks, virtual environments, VCS metadata, caches, and tests are not followed
  by default.
- Reflection, runtime imports, monkey patching, and unknown receiver types end at
  explicit unresolved nodes.
