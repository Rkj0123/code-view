# Code View graph contract v1

`code-view.graph/v1` is language-neutral. Producers emit one JSON envelope matching
`code-view-graph-v1.schema.json`, or deterministic NDJSON in this order:

1. `{"record":"meta","schemaVersion":...,"project":...}`
2. one `{"record":"node","node":...}` per sorted node
3. one `{"record":"edge","edge":...}` per sorted edge
4. one `{"record":"diagnostic","diagnostic":...}` per sorted diagnostic
5. `{"record":"summary","nodeCount":...,"edgeCount":...,"diagnosticCount":...}`

Node IDs identify `(language, kind, qualifiedName, repository-relative path)`;
repeated definitions add a deterministic source-order occurrence suffix.
Edge IDs additionally include relationship kind, endpoints, and occurrence range.
Ranges use one-based UTF-8 line and column positions; the end is exclusive.

`external` means the target is known but outside the repository. `unresolved` means
static analysis cannot name a safe target. Both carry a non-null `reason` stating
why traversal stopped. Consumers must never display an unresolved edge as an
internal call.

Relationship direction is consumer to dependency, except `contains`, which is
parent to child. `decorates` is decorated symbol to decorator.

Repository containment is `repository/package -> file -> module -> symbols`.
Package and module nodes remain available even when a UI hides them by default.

JSON Schema validates shape. Producers also run the deterministic semantic
validator in `services/python-analyzer/analyzer.py`, which rejects duplicate IDs,
dangling endpoints, unknown entry IDs, reversed ranges, and malformed boundary
nodes before a graph crosses the service boundary.
