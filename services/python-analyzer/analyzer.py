#!/usr/bin/env python3
"""Static Python-to-Code-View graph analyzer. Repository code is never executed."""

from __future__ import annotations

import argparse
import ast
import builtins
import fnmatch
import hashlib
import io
import itertools
import json
import operator
import os
from pathlib import Path
import shlex
import sys
import time
import tokenize
from dataclasses import dataclass, field, replace
from typing import Any, Iterable


SCHEMA_VERSION = "code-view.graph/v1"
CONFIG_KEYS = {"schemaVersion", "entry", "startCommand", "languages", "index", "canvas", "git", "includeTests"}
IGNORED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".tox", ".nox", "site-packages"}
SHELL_META = set(";&|><`$\n\r")
BUILTINS = frozenset(dir(builtins))
CANONICAL_EDGE_KINDS = (
    "contains", "imports", "calls", "inherits", "constructs", "reads", "writes",
    "decorates", "type_uses", "api_calls", "test_covers",
)
DEFAULT_RELATIONSHIPS = tuple(kind for kind in CANONICAL_EDGE_KINDS if kind != "test_covers")
LEGACY_EDGE_MAP = {
    "contains": "EDGE_MEMBER",
    "imports": "EDGE_IMPORT",
    "calls": "EDGE_CALL",
    "constructs": "EDGE_CALL",
    "inherits": "EDGE_INHERITANCE",
    "reads": "EDGE_USAGE",
    "writes": "EDGE_USAGE",
    "api_calls": "EDGE_USAGE",
    "decorates": "EDGE_ANNOTATION_USAGE",
    "type_uses": "EDGE_TYPE_USAGE",
    "test_covers": "EDGE_USAGE",
}


class AnalysisError(ValueError):
    pass


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def node_range(node: ast.AST | None) -> dict[str, Any] | None:
    if node is None or not hasattr(node, "lineno"):
        return None
    return {
        "start": {"line": node.lineno, "column": node.col_offset + 1},
        "end": {
            "line": getattr(node, "end_lineno", node.lineno),
            "column": getattr(node, "end_col_offset", node.col_offset) + 1,
        },
    }


def range_key(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    return ":".join(str(value[p][k]) for p in ("start", "end") for k in ("line", "column"))


@dataclass(frozen=True)
class Config:
    entry: str | None = None
    entry_symbol: str | None = None
    entry_command: str | tuple[str, ...] | None = None
    entry_command_mode: str = "document"
    start_command: str | tuple[str, ...] | None = None
    start_mode: str = "document"
    include_tests: bool = False
    exclude: tuple[str, ...] = ()
    modules: str = "hide"
    external_packages: str = "collapse"
    initial_view: str = "entry-focus"
    relationships: tuple[str, ...] = DEFAULT_RELATIONSHIPS
    git_base: str = "HEAD"


@dataclass
class ModuleInfo:
    path: Path
    relative_path: str
    name: str
    source: str
    tree: ast.Module
    node_id: str
    is_package: bool
    symbols: dict[int, str] = field(default_factory=dict)
    ast_nodes: dict[int, ast.AST] = field(default_factory=dict)
    scopes: dict[str, dict[str, str]] = field(default_factory=dict)
    aliases: dict[str, dict[str, "Binding"]] = field(default_factory=dict)
    ambiguous_names: dict[str, set[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Binding:
    node_id: str
    qualified_name: str
    category: str
    value: ast.expr | None = None


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        self.diagnostics: list[dict[str, Any]] = []
        self.entry_node_id: str | None = None

    def add_node(
        self,
        kind: str,
        name: str,
        qualified_name: str,
        path: str | None,
        source_range: dict[str, Any] | None = None,
        signature: str | None = None,
        docstring: str | None = None,
        *,
        language: str | None = "python",
        external: bool = False,
        unresolved: bool = False,
        reason: str | None = None,
        modifiers: Iterable[str] = (),
        identity_suffix: object | None = None,
    ) -> str:
        node_id = stable_id("n", SCHEMA_VERSION, language, kind, qualified_name, path, identity_suffix)
        self.nodes[node_id] = {
            "id": node_id,
            "kind": kind,
            "language": language,
            "name": name,
            "qualifiedName": qualified_name,
            "path": path,
            "range": source_range,
            "signature": signature,
            "docstring": docstring,
            "external": external,
            "unresolved": unresolved,
            "reason": reason,
            "modifiers": sorted(set(modifiers)),
        }
        return node_id

    def add_edge(
        self,
        kind: str,
        source: str,
        target: str,
        path: str | None = None,
        at: ast.AST | None = None,
        reason: str | None = None,
    ) -> str:
        source_range = node_range(at)
        edge_id = stable_id("e", SCHEMA_VERSION, kind, source, target, path, range_key(source_range), reason)
        self.edges[edge_id] = {
            "id": edge_id,
            "kind": kind,
            "language": "python",
            "source": source,
            "target": target,
            "path": path,
            "range": source_range,
            "reason": reason,
        }
        return edge_id

    def add_external(self, qualified_name: str) -> Binding:
        root_name = qualified_name.split(".", 1)[0]
        reason = "outside repository: standard library" if root_name in sys.stdlib_module_names else "outside repository: external module"
        node_id = self.add_node(
            "external", qualified_name.rsplit(".", 1)[-1], qualified_name, None, external=True, reason=reason
        )
        return Binding(node_id, qualified_name, "external")

    def add_unresolved(self, module: ModuleInfo, expression: str, at: ast.AST, reason: str) -> Binding:
        line = getattr(at, "lineno", 1)
        column = getattr(at, "col_offset", 0) + 1
        qualified_name = (
            f"{module.name}::<unresolved>@{line}:{column}:{expression}"
        )
        node_id = self.add_node(
            "unresolved",
            expression,
            qualified_name,
            module.relative_path,
            node_range(at),
            unresolved=True,
            reason=reason,
        )
        return Binding(node_id, qualified_name, "unresolved")

    def to_dict(self) -> dict[str, Any]:
        nodes = sorted(self.nodes.values(), key=lambda n: (n["qualifiedName"], n["kind"], n["id"]))
        edges = sorted(
            self.edges.values(),
            key=lambda e: (e["kind"], e["source"], e["target"], e["path"] or "", range_key(e["range"])),
        )
        diagnostics = sorted(
            self.diagnostics,
            key=lambda d: (d["path"] or "", range_key(d["range"]), d["code"], d["message"]),
        )
        return {
            "schemaVersion": SCHEMA_VERSION,
            "project": {
                "root": ".",
                "languages": ["python"],
                "entryNodeId": self.entry_node_id,
                "entryReachableNodeIds": self._reachable_nodes(),
            },
            "nodes": nodes,
            "edges": edges,
            "diagnostics": diagnostics,
        }

    def _reachable_nodes(self) -> list[str]:
        if not self.entry_node_id:
            return []
        outgoing: dict[str, list[str]] = {}
        parents: dict[str, str] = {}
        for edge in self.edges.values():
            if edge["kind"] == "contains":
                parents[edge["target"]] = edge["source"]
            else:
                outgoing.setdefault(edge["source"], []).append(edge["target"])
        seen = {self.entry_node_id}
        pending = [self.entry_node_id]
        while pending:
            current = pending.pop()
            for target in outgoing.get(current, []):
                if target not in seen:
                    seen.add(target)
                    pending.append(target)
        for current in tuple(seen):
            while current in parents:
                current = parents[current]
                seen.add(current)
        return sorted(seen)


def validate_graph_document(graph: dict[str, Any]) -> None:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_ids = [node.get("id") for node in nodes]
    edge_ids = [edge.get("id") for edge in edges]
    if len(node_ids) != len(set(node_ids)):
        raise AnalysisError("graph contains duplicate node IDs")
    if len(edge_ids) != len(set(edge_ids)):
        raise AnalysisError("graph contains duplicate edge IDs")
    known = set(node_ids)
    project = graph.get("project", {})
    entry = project.get("entryNodeId")
    if entry is not None and entry not in known:
        raise AnalysisError("graph entryNodeId is not a node")
    reachable = project.get("entryReachableNodeIds", [])
    if len(reachable) != len(set(reachable)):
        raise AnalysisError("graph entry reachability contains duplicate IDs")
    if any(node_id not in known for node_id in reachable):
        raise AnalysisError("graph entry reachability contains an unknown node")
    if entry is not None and entry not in reachable:
        raise AnalysisError("graph entryNodeId is missing from entry reachability")
    for edge in edges:
        if edge.get("source") not in known or edge.get("target") not in known:
            raise AnalysisError("graph edge has a dangling endpoint")
    for item in [*nodes, *edges, *graph.get("diagnostics", [])]:
        path = item.get("path")
        if path is not None:
            if (
                not isinstance(path, str)
                or not path
                or "\0" in path
                or "\\" in path
                or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/"))
            ):
                raise AnalysisError("graph path must be normalized and repository-relative")
        source_range = item.get("range")
        if source_range:
            start = source_range["start"]
            end = source_range["end"]
            if (start["line"], start["column"]) > (end["line"], end["column"]):
                raise AnalysisError("graph contains a reversed source range")
    for node in nodes:
        external_kind = node.get("kind") == "external"
        unresolved_kind = node.get("kind") == "unresolved"
        if node.get("external") != external_kind or node.get("unresolved") != unresolved_kind:
            raise AnalysisError("graph boundary flags must match the node kind")
        if external_kind and not node.get("reason"):
            raise AnalysisError("external nodes require the external flag and reason")
        if unresolved_kind and not node.get("reason"):
            raise AnalysisError("unresolved nodes require the unresolved flag and reason")
        if not (external_kind or unresolved_kind) and node.get("reason") is not None:
            raise AnalysisError("internal nodes cannot carry a boundary reason")


def is_test_path(relative: Path) -> bool:
    parts = relative.parts
    return (
        any(part.lower() in {"test", "tests"} for part in parts[:-1])
        or relative.name == "conftest.py"
        or relative.name.startswith("test_")
        or relative.name.endswith("_test.py")
    )


def discover_python_files(root: Path, include_tests: bool, exclude: tuple[str, ...] = ()) -> list[Path]:
    files: list[Path] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in IGNORED_DIRS and not name.startswith(".") and not (current_path / name).is_symlink()
        )
        for name in sorted(names):
            path = current_path / name
            if path.suffix != ".py" or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if any(fnmatch.fnmatchcase(relative.as_posix(), pattern) for pattern in exclude):
                continue
            if include_tests or not is_test_path(relative):
                files.append(path)
    return files


def parse_start_command(command: str) -> str:
    if any(char in SHELL_META for char in command):
        raise AnalysisError("startCommand contains shell metacharacters")
    try:
        parts = shlex.split(command)
    except ValueError as error:
        raise AnalysisError(f"invalid startCommand: {error}") from error
    return parse_start_argv(parts)


def parse_start_argv(parts: list[str] | tuple[str, ...]) -> str:
    if not parts or Path(parts[0]).name not in {"python", "python3"}:
        raise AnalysisError("startCommand must begin with python or python3")
    if len(parts) >= 3 and parts[1] == "-m":
        module = parts[2]
        if not module or any(not piece.isidentifier() for piece in module.split(".")):
            raise AnalysisError("startCommand has an invalid module name")
        return module.replace(".", "/") + ".py"
    if len(parts) >= 2 and parts[1].endswith(".py"):
        return parts[1]
    raise AnalysisError("startCommand must use python FILE or python -m MODULE")


def load_config(root: Path, config_path: Path | None = None) -> Config:
    root = root.resolve(strict=True)
    path = config_path or root / "code-view.json"
    if not path.exists():
        return Config()
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise AnalysisError("configuration must be inside the repository") from error
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AnalysisError(f"invalid code-view.json: {error}") from error
    if not isinstance(data, dict):
        raise AnalysisError("code-view.json must contain a JSON object")
    unknown = sorted(set(data) - CONFIG_KEYS)
    if unknown:
        raise AnalysisError(f"unknown code-view.json keys: {', '.join(unknown)}")
    schema_version = data.get("schemaVersion", 1)
    if type(schema_version) is not int or schema_version != 1:
        raise AnalysisError("unsupported code-view.json schemaVersion")
    entry_value = data.get("entry")
    entry = None
    entry_symbol = None
    entry_command = None
    entry_command_mode = "document"
    if isinstance(entry_value, str):
        if not entry_value:
            raise AnalysisError("entry must be a non-empty string")
        entry = entry_value
    elif isinstance(entry_value, dict):
        _reject_unknown(entry_value, {"file", "symbol", "command"}, "entry")
        entry = _required_string(entry_value, "file", "entry")
        entry_symbol = _optional_string(entry_value, "symbol", "entry")
        if "command" in entry_value:
            entry_command, entry_command_mode = _command_value(entry_value["command"], "entry.command")
    elif "entry" in data:
        raise AnalysisError("entry must be a string or object")
    start_value = data.get("startCommand")
    start_command, start_mode = _command_value(start_value, "startCommand") if "startCommand" in data else (None, "document")
    languages = data.get("languages", ["python"])
    if languages != ["python"]:
        raise AnalysisError("languages must be exactly [\"python\"] in v1")
    index = data.get("index", {})
    if not isinstance(index, dict):
        raise AnalysisError("index must be an object")
    _reject_unknown(index, {"tests", "modules", "externalPackages", "exclude"}, "index")
    tests_mode = index.get("tests")
    if "tests" in index and (not isinstance(tests_mode, str) or tests_mode not in {"exclude", "include"}):
        raise AnalysisError("index.tests must be exclude or include")
    modules = index.get("modules", "hide")
    if not isinstance(modules, str) or modules not in {"hide", "show"}:
        raise AnalysisError("index.modules must be hide or show")
    external_packages = index.get("externalPackages", "collapse")
    if external_packages != "collapse":
        raise AnalysisError("index.externalPackages must be collapse")
    exclude = index.get("exclude", [])
    if not isinstance(exclude, list) or not all(isinstance(pattern, str) and pattern for pattern in exclude):
        raise AnalysisError("index.exclude must be a string array")
    if len(set(exclude)) != len(exclude):
        raise AnalysisError("index.exclude must not contain duplicates")
    for pattern in exclude:
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts or "\x00" in pattern:
            raise AnalysisError("index.exclude patterns must stay inside the repository")
    canvas = data.get("canvas", {})
    if not isinstance(canvas, dict):
        raise AnalysisError("canvas must be an object")
    _reject_unknown(canvas, {"initialView", "relationships"}, "canvas")
    initial_view = canvas.get("initialView", "entry-focus")
    if not isinstance(initial_view, str) or initial_view not in {"entry-focus", "whole-repo"}:
        raise AnalysisError("canvas.initialView must be entry-focus or whole-repo")
    relationship_value = canvas.get("relationships")
    if "relationships" not in canvas:
        relationships = DEFAULT_RELATIONSHIPS
    elif isinstance(relationship_value, list):
        if not all(isinstance(item, str) for item in relationship_value) or len(set(relationship_value)) != len(relationship_value) or any(item not in CANONICAL_EDGE_KINDS for item in relationship_value):
            raise AnalysisError("canvas.relationships contains an unknown or duplicate relationship")
        relationships = tuple(relationship_value)
    elif isinstance(relationship_value, dict):
        _reject_unknown(relationship_value, set(CANONICAL_EDGE_KINDS), "canvas.relationships")
        if not all(isinstance(enabled, bool) for enabled in relationship_value.values()):
            raise AnalysisError("canvas.relationships values must be true or false")
        relationships = tuple(kind for kind in CANONICAL_EDGE_KINDS if relationship_value.get(kind, kind != "test_covers"))
    else:
        raise AnalysisError("canvas.relationships must be an array or object")
    git = data.get("git", {})
    if not isinstance(git, dict):
        raise AnalysisError("git must be an object")
    _reject_unknown(git, {"base"}, "git")
    git_base = git.get("base", "HEAD")
    if not isinstance(git_base, str) or not git_base:
        raise AnalysisError("git.base must be a non-empty string")
    include_tests = data.get("includeTests", False)
    if not isinstance(include_tests, bool):
        raise AnalysisError("includeTests must be true or false")
    if tests_mode is not None:
        include_tests = tests_mode == "include"
    return Config(
        entry=entry,
        entry_symbol=entry_symbol,
        entry_command=entry_command,
        entry_command_mode=entry_command_mode,
        start_command=start_command,
        start_mode=start_mode,
        include_tests=include_tests,
        exclude=tuple(exclude),
        modules=modules,
        external_packages=external_packages,
        initial_view=initial_view,
        relationships=tuple(relationships),
        git_base=git_base,
    )


def _reject_unknown(value: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise AnalysisError(f"unknown {name} keys: {', '.join(unknown)}")


def _required_string(value: dict[str, Any], key: str, name: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise AnalysisError(f"{name}.{key} must be a non-empty string")
    return result


def _optional_string(value: dict[str, Any], key: str, name: str) -> str | None:
    result = value.get(key)
    if result is not None and (not isinstance(result, str) or not result):
        raise AnalysisError(f"{name}.{key} must be a non-empty string")
    return result


def _command_value(value: Any, name: str) -> tuple[str | tuple[str, ...], str]:
    if isinstance(value, str):
        if not value:
            raise AnalysisError(f"{name} must be non-empty")
        return value, "document"
    if not isinstance(value, dict):
        raise AnalysisError(f"{name} must be a string or object")
    _reject_unknown(value, {"argv", "mode"}, name)
    argv = value.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise AnalysisError(f"{name}.argv must be a non-empty string array")
    mode = value.get("mode")
    if not isinstance(mode, str) or mode not in {"document", "manual"}:
        raise AnalysisError(f"{name}.mode must be document or manual")
    return tuple(argv), mode


def contains_main_guard(tree: ast.Module) -> bool:
    for statement in tree.body:
        if not isinstance(statement, ast.If):
            continue
        test = statement.test
        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            continue
        values = [test.left, *test.comparators]
        if any(isinstance(value, ast.Name) and value.id == "__name__" for value in values) and any(
            isinstance(value, ast.Constant) and value.value == "__main__" for value in values
        ):
            return True
    return False


UNKNOWN_VALUE = object()
COMPARISONS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
}


def literal_value(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return UNKNOWN_VALUE


def literal_truth(node: ast.AST) -> bool | None:
    value = literal_value(node)
    if value is not UNKNOWN_VALUE:
        return bool(value)
    if isinstance(node, (ast.Lambda, ast.GeneratorExp)):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return bool(node.elts) if not any(isinstance(item, ast.Starred) for item in node.elts) else None
    if isinstance(node, ast.Dict) and all(key is not None for key in node.keys):
        return bool(node.keys)
    return None


def resolve_entry(root: Path, files: list[Path], config: Config) -> Path | None:
    candidates = {path.relative_to(root).as_posix(): path for path in files}
    module_command = False
    if config.entry:
        requested = config.entry
    elif isinstance(config.start_command, str):
        requested = parse_start_command(config.start_command)
        command_parts = shlex.split(config.start_command)
        module_command = len(command_parts) >= 2 and command_parts[1] == "-m"
    elif config.start_command:
        requested = parse_start_argv(config.start_command)
        module_command = len(config.start_command) >= 2 and config.start_command[1] == "-m"
    else:
        requested = None
    if requested:
        requested_path = Path(requested)
        if requested_path.is_absolute() or ".." in requested_path.parts:
            raise AnalysisError("entry must stay inside the repository")
        if module_command:
            module_parts = requested_path.with_suffix("").parts
            for length in range(1, len(module_parts)):
                prefix = Path(*module_parts[:length])
                if prefix.with_suffix(".py").as_posix() in candidates and (prefix / "__init__.py").as_posix() not in candidates:
                    raise AnalysisError(f"entry module is blocked by regular module: {prefix.as_posix()}.py")
        keys = [requested_path.as_posix()]
        if module_command and requested_path.suffix == ".py":
            package_root = requested_path.with_suffix("")
            package_main = (package_root / "__main__.py").as_posix()
            package_init = (package_root / "__init__.py").as_posix()
            if package_init in candidates:
                keys = [package_main]
            else:
                keys.append(package_main)
        elif not requested_path.suffix:
            keys.append((requested_path / "__main__.py").as_posix())
            keys.append(requested_path.with_suffix(".py").as_posix())
        match = next((candidates[key] for key in keys if key in candidates), None)
        if match is None:
            raise AnalysisError(f"entry does not resolve to one indexed Python file: {requested}")
        return match
    root_main = candidates.get("main.py")
    if root_main:
        return root_main
    package_mains = [path for path in files if path.name == "__main__.py"]
    if len(package_mains) == 1:
        return package_mains[0]
    guarded: list[Path] = []
    for path in files:
        try:
            with tokenize.open(path) as handle:
                if contains_main_guard(ast.parse(handle.read(), filename=str(path))):
                    guarded.append(path)
        except (OSError, UnicodeError, SyntaxError):
            pass
    return guarded[0] if len(guarded) == 1 else None


def module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__root__"


def definition_signature(source_lines: list[str], node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    last_line = max(node.lineno, node.body[0].lineno if node.body else node.lineno)
    source = "".join(source_lines[node.lineno - 1:last_line])
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    start_offset = None
    depth = 0
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    keyword = "class" if isinstance(node, ast.ClassDef) else "def"
    for token in tokens:
        if start_offset is None:
            if token.type == tokenize.NAME and token.string == keyword and token.start[0] == 1:
                start_column = node.col_offset
                start_offset = start_column
            continue
        if token.type == tokenize.OP:
            if token.string in "([{":
                depth += 1
            elif token.string in ")]}" and depth:
                depth -= 1
            elif token.string == ":" and depth == 0:
                end_offset = offsets[token.end[0] - 1] + token.end[1]
                return source[start_offset:end_offset].strip()
    return lines[0].strip()


class DefinitionCollector(ast.NodeVisitor):
    def __init__(self, graph: Graph, module: ModuleInfo) -> None:
        self.graph = graph
        self.module = module
        self.source_lines = module.source.splitlines(keepends=True)
        self.stack: list[tuple[str, str, str]] = [(module.node_id, module.name, "module")]
        self.occurrences: dict[tuple[str, str], int] = {}
        module.scopes[module.node_id] = {}

    def _definition(self, node: ast.AST, name: str, kind: str, modifiers: Iterable[str] = ()) -> None:
        parent_id, parent_name, _ = self.stack[-1]
        qualified_name = f"{parent_name}.{name}"
        occurrence_key = (kind, qualified_name)
        occurrence = self.occurrences.get(occurrence_key, 0) + 1
        self.occurrences[occurrence_key] = occurrence
        node_id = self.graph.add_node(
            kind,
            name,
            qualified_name,
            self.module.relative_path,
            node_range(node),
            definition_signature(self.source_lines, node),
            ast.get_docstring(node, clean=False),
            modifiers=modifiers,
            identity_suffix=occurrence if occurrence > 1 else None,
        )
        self.graph.add_edge("contains", parent_id, node_id, self.module.relative_path, node)
        self.module.symbols[id(node)] = node_id
        self.module.ast_nodes[id(node)] = node
        parent_scope = self.module.scopes.setdefault(parent_id, {})
        if name in parent_scope:
            self.module.ambiguous_names.setdefault(parent_id, set()).add(name)
        parent_scope[name] = node_id
        self.module.scopes[node_id] = {}
        self.stack.append((node_id, qualified_name, kind))
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._definition(node, node.name, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = "method" if self.stack[-1][2] == "class" else "function"
        self._definition(node, node.name, kind)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = "method" if self.stack[-1][2] == "class" else "function"
        self._definition(node, node.name, kind, ("async",))


class ImportCollector(ast.NodeVisitor):
    def __init__(self, module: ModuleInfo) -> None:
        self.module = module
        self.owners = [module.node_id]
        self.specs: list[tuple[str, ast.Import | ast.ImportFrom]] = []

    def collect(self) -> list[tuple[str, ast.Import | ast.ImportFrom]]:
        self.visit(self.module.tree)
        return self.specs

    def visit_Import(self, node: ast.Import) -> None:
        self.specs.append((self.owners[-1], node))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.specs.append((self.owners[-1], node))

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.owners.append(self.module.symbols[id(node)])
        for child in node.body:
            self.visit(child)
        self.owners.pop()

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition


class Analyzer:
    def __init__(self, root: Path, config: Config) -> None:
        self.root = root
        self.config = config
        self.graph = Graph()
        self.modules: dict[str, ModuleInfo] = {}
        self.module_list: list[ModuleInfo] = []
        self.module_collisions: set[str] = set()
        self.blocked_module_names: set[str] = set()
        self.nodes_by_qualified: dict[str, str] = {}
        self.ambiguous_qualified: set[str] = set()
        self.import_specs: dict[str, list[tuple[str, ast.Import | ast.ImportFrom]]] = {}

    def analyze(self, files: list[Path], entry: Path | None) -> dict[str, Any]:
        repository_id = self.graph.add_node("repository", self.root.name, ".", None, language=None)
        self._parse_modules(files, repository_id)
        regular_modules = {module.name for module in self.module_list if not module.is_package}
        self.blocked_module_names = {
            module.name
            for module in self.module_list
            if any(module.name.startswith(name + ".") for name in regular_modules)
        }
        for module in self.module_list:
            DefinitionCollector(self.graph, module).visit(module.tree)
        structural = {"repository", "file", "module", "package"}
        for node_id, node in self.graph.nodes.items():
            if node["external"] or node["unresolved"]:
                continue
            qualified = node["qualifiedName"]
            existing = self.nodes_by_qualified.get(qualified)
            if existing and node["kind"] not in structural and self.graph.nodes[existing]["kind"] not in structural:
                self.ambiguous_qualified.add(qualified)
                self.nodes_by_qualified.pop(qualified, None)
            elif qualified not in self.ambiguous_qualified:
                self.nodes_by_qualified[qualified] = node_id
        self._collect_imports()
        for module in self.module_list:
            RelationshipCollector(self, module).visit(module.tree)
        if entry:
            entry_module = next((module for module in self.module_list if module.path == entry), None)
            if entry_module:
                entry_id = entry_module.node_id
                if self.config.entry_symbol:
                    entry_name = entry_module.name
                    candidates = (
                        self.config.entry_symbol,
                        f"{entry_name}.{self.config.entry_symbol}",
                    )
                    entry_id = entry_module.scopes.get(entry_module.node_id, {}).get(self.config.entry_symbol, "") or next(
                        (self.nodes_by_qualified[name] for name in candidates if name in self.nodes_by_qualified), ""
                    )
                    if not entry_id:
                        raise AnalysisError(f"entry.symbol was not found: {self.config.entry_symbol}")
                self.graph.entry_node_id = entry_id
        document = self.graph.to_dict()
        validate_graph_document(document)
        return document

    def _parse_modules(self, files: list[Path], repository_id: str) -> None:
        package_ids: dict[str, str] = {}
        for path in files:
            relative = path.relative_to(self.root)
            for count in range(1, len(relative.parts)):
                package = ".".join(relative.parts[:count])
                if package not in package_ids:
                    package_path = "/".join(relative.parts[:count])
                    package_id = self.graph.add_node("package", relative.parts[count - 1], package, package_path)
                    parent = package_ids.get(".".join(relative.parts[: count - 1]), repository_id)
                    self.graph.add_edge("contains", parent, package_id)
                    package_ids[package] = package_id
            try:
                with tokenize.open(path) as handle:
                    source = handle.read()
                tree = ast.parse(source, filename=relative.as_posix(), type_comments=True)
            except (OSError, UnicodeError, SyntaxError) as error:
                source = ""
                tree = ast.Module(body=[], type_ignores=[])
                line = getattr(error, "lineno", 1) or 1
                column = getattr(error, "offset", 1) or 1
                self.graph.diagnostics.append(
                    {
                        "code": "PY_SYNTAX_ERROR",
                        "severity": "error",
                        "message": str(error),
                        "path": relative.as_posix(),
                        "range": {
                            "start": {"line": line, "column": column},
                            "end": {"line": line, "column": column + 1},
                        },
                    }
                )
            name = module_name(relative)
            last_line = source.splitlines()[-1] if source.splitlines() else ""
            file_range = {
                "start": {"line": 1, "column": 1},
                "end": {"line": max(1, len(source.splitlines())), "column": len(last_line.encode("utf-8")) + 1},
            }
            file_id = self.graph.add_node(
                "file", relative.name, f"file:{relative.as_posix()}", relative.as_posix(), file_range
            )
            module_id = self.graph.add_node(
                "module",
                relative.name,
                name,
                relative.as_posix(),
                file_range,
                docstring=ast.get_docstring(tree, clean=False),
            )
            parent_name = ".".join(relative.parts[:-1])
            self.graph.add_edge("contains", package_ids.get(parent_name, repository_id), file_id)
            self.graph.add_edge("contains", file_id, module_id)
            module = ModuleInfo(path, relative.as_posix(), name, source, tree, module_id, relative.name == "__init__.py")
            if name in self.modules:
                self.module_collisions.add(name)
            else:
                self.modules[name] = module
            self.module_list.append(module)

    def _collect_imports(self) -> None:
        for module in self.module_list:
            self.import_specs[module.node_id] = ImportCollector(module).collect()
        for module in self.module_list:
            for owner, node in self.import_specs[module.node_id]:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target_name = alias.name
                        import_target = self.resolve_absolute(target_name, module, alias)
                        self.graph.add_edge("imports", owner, import_target.node_id, module.relative_path, alias)
                else:
                    base = self._absolute_from(module, node.module, node.level)
                    for alias in node.names:
                        if alias.name == "*":
                            binding = self.graph.add_unresolved(module, f"{base}.*", alias, "wildcard import")
                        else:
                            binding = self.resolve_absolute(
                                f"{base}.{alias.name}" if base else alias.name,
                                module,
                                alias,
                            )
                            if binding.category == "external" and node.level:
                                binding = self.graph.add_unresolved(module, f"{base}.{alias.name}", alias, "missing relative import")
                        self.graph.add_edge("imports", owner, binding.node_id, module.relative_path, alias)

    def _absolute_from(self, module: ModuleInfo, imported: str | None, level: int) -> str:
        if not level:
            return imported or ""
        package = module.name if module.is_package else module.name.rpartition(".")[0]
        parts = package.split(".") if package else []
        remove = level - 1
        if remove > len(parts):
            return ""
        base = parts[: len(parts) - remove]
        if imported:
            base.extend(imported.split("."))
        return ".".join(base)

    def resolve_absolute(
        self,
        qualified_name: str,
        context_module: ModuleInfo | None = None,
        at: ast.AST | None = None,
    ) -> Binding:
        collision = next((name for name in self.module_collisions if qualified_name == name or qualified_name.startswith(name + ".")), None)
        if collision:
            module = context_module or self.modules[collision]
            return self.graph.add_unresolved(module, qualified_name, at or module.tree, "module and package share the same import name")
        blocked = next((name for name in self.blocked_module_names if qualified_name == name or qualified_name.startswith(name + ".")), None)
        if blocked:
            module = context_module or self.modules[blocked]
            return self.graph.add_unresolved(module, qualified_name, at or module.tree, "regular module blocks same-name namespace package")
        if qualified_name in self.ambiguous_qualified:
            module = context_module or next(iter(self.module_list))
            return self.graph.add_unresolved(module, qualified_name, at or module.tree, "multiple definitions cannot be resolved statically")
        if qualified_name in self.modules:
            return Binding(self.modules[qualified_name].node_id, qualified_name, "module")
        if qualified_name in self.nodes_by_qualified:
            node_id = self.nodes_by_qualified[qualified_name]
            kind = self.graph.nodes[node_id]["kind"]
            category = "class" if kind == "class" else "module" if kind in {"module", "package"} else "symbol"
            return Binding(node_id, qualified_name, category)
        package_id = self.nodes_by_qualified.get(qualified_name)
        if package_id:
            return Binding(package_id, qualified_name, "module")
        root_name = qualified_name.split(".", 1)[0]
        if root_name in self.modules or any(name.startswith(root_name + ".") for name in self.modules):
            module_name_part, _, symbol = qualified_name.rpartition(".")
            if module_name_part in self.modules:
                reexport = self._resolve_reexport(module_name_part, symbol, set())
                if reexport:
                    return reexport
            module = next((item for name, item in self.modules.items() if qualified_name.startswith(name + ".")), None)
            if module:
                return self.graph.add_unresolved(
                    context_module or module,
                    qualified_name,
                    at or module.tree,
                    "internal symbol not found",
                )
        return self.graph.add_external(qualified_name)

    def _resolve_reexport(self, module_name_value: str, symbol: str, seen: set[tuple[str, str]]) -> Binding | None:
        key = (module_name_value, symbol)
        if key in seen:
            return None
        seen.add(key)
        module = self.modules[module_name_value]
        for owner, node in self.import_specs.get(module.node_id, []):
            if owner != module.node_id or not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if (alias.asname or alias.name) != symbol:
                    continue
                base = self._absolute_from(module, node.module, node.level)
                target = f"{base}.{alias.name}" if base else alias.name
                if target in self.nodes_by_qualified:
                    node_id = self.nodes_by_qualified[target]
                    kind = self.graph.nodes[node_id]["kind"]
                    return Binding(node_id, target, "class" if kind == "class" else "symbol")
                parent, _, child = target.rpartition(".")
                return self._resolve_reexport(parent, child, seen) if parent in self.modules else None
        return None


class LocalAssignmentCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: dict[str, ast.AST] = {}
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.setdefault(node.id, node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.setdefault(node.name, node)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.setdefault(alias.asname or alias.name.split(".")[0], alias)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.names.setdefault(alias.asname or alias.name, alias)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.setdefault(node.name, node)
        for child in node.body:
            self.visit(child)

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocals.update(node.names)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.names.setdefault(node.name, node)
        if node.pattern:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.names.setdefault(node.name, node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.names.setdefault(node.rest, node)
        self.generic_visit(node)

    def _child_scope(self, _: ast.AST) -> None:
        return

    visit_Lambda = _child_scope
    visit_ListComp = _child_scope
    visit_SetComp = _child_scope
    visit_DictComp = _child_scope
    visit_GeneratorExp = _child_scope


def local_bindings(statements: Iterable[ast.stmt]) -> tuple[dict[str, ast.AST], set[str], set[str]]:
    collector = LocalAssignmentCollector()
    for statement in statements:
        collector.visit(statement)
    declarations = collector.globals | collector.nonlocals
    return ({name: at for name, at in collector.names.items() if name not in declarations}, collector.globals, collector.nonlocals)


class RelationshipCollector(ast.NodeVisitor):
    def __init__(self, analyzer: Analyzer, module: ModuleInfo) -> None:
        self.analyzer = analyzer
        self.graph = analyzer.graph
        self.module = module
        self.scope_stack = [module.node_id]
        self.class_stack: list[str] = []
        self.variables: dict[str, dict[str, str]] = {module.node_id: {}}
        self.variable_types: dict[str, dict[str, str]] = {module.node_id: {}}
        self.variable_bindings: dict[str, dict[str, Binding]] = {module.node_id: {}}
        self.field_types: dict[str, dict[str, str]] = {}
        self.call_bindings: dict[int, Binding] = {}
        self.call_results: dict[int, Binding] = {}
        self.call_truths: dict[int, bool] = {}
        self.lambda_contexts: dict[int, tuple[tuple[str, ...], tuple[str, ...]]] = {}
        self.global_names: dict[str, set[str]] = {}
        self.nonlocal_names: dict[str, set[str]] = {}
        module_locals, _, _ = local_bindings(module.tree.body)
        self.declared_names: dict[str, set[str]] = {module.node_id: set(module_locals)}
        self.shadowed_names: list[set[str]] = []
        self.function_contexts: dict[str, tuple[ast.FunctionDef | ast.AsyncFunctionDef, tuple[str, ...], tuple[str, ...]]] = {}
        self.function_order: list[str] = []
        self.function_defaults: dict[str, dict[str, Binding]] = {}
        self.function_returns: dict[str, Binding] = {}
        self.function_return_truth: dict[str, bool] = {}
        self.function_raises: dict[str, str | None] = {}
        self.decorated_definitions: set[str] = set()
        self.decorator_results: dict[str, Binding] = {}
        self.static_methods: set[str] = set()
        self.class_methods: set[str] = set()
        self.deferred_values: dict[str, dict[str, ast.expr]] = {module.node_id: {}}
        self.literal_mappings: dict[str, dict[str, dict[str, Binding]]] = {module.node_id: {}}
        self.literal_truths: dict[str, dict[str, bool]] = {module.node_id: {}}
        self.lazy_positions: dict[int, int] = {}
        self.exhausted_lazy_values: set[int] = set()
        self.expression_bindings: list[dict[str, Binding]] = []
        self.executed_functions: set[str] = set()
        self.active_functions: set[str] = set()
        self.isolated_execution_depth = 0
        self.uncertain_execution_depth = 0
        self.eager_lazy_calls: set[int] = set()
        self.suppressed_withs: set[int] = set()
        self.flow_terminated: str | None = None
        self.raised_exception: str | None = None
        self.suspend_at_yield_depth = 0
        self.yields_to_skip = 0
        self.control_depth = 0

    @property
    def scope(self) -> str:
        return self.scope_stack[-1]

    def visit_Module(self, node: ast.Module) -> None:
        for child in node.body:
            self.visit(child)
        index = 0
        while index < len(self.function_order):
            node_id = self.function_order[index]
            index += 1
            if node_id not in self.executed_functions:
                self._execute_function(node_id, isolated=True)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        node_id = self.module.symbols[id(node)]
        for base in node.bases:
            binding = self.resolve_expression(base)
            self.graph.add_edge("inherits", node_id, binding.node_id, self.module.relative_path, base)
        definition_owner = self._binding_scope(node.name)
        if definition_owner != self.scope:
            self._variable(node.name, node, scope=definition_owner)
        self.scope_stack.append(node_id)
        self.class_stack.append(self.graph.nodes[node_id]["qualifiedName"])
        self.variables.setdefault(node_id, {})
        self.variable_types.setdefault(node_id, {})
        class_locals, globals_, nonlocals_ = local_bindings(node.body)
        self.declared_names[node_id] = set(class_locals)
        self.global_names[node_id] = globals_
        self.nonlocal_names[node_id] = nonlocals_
        self.field_types.setdefault(self.class_stack[-1], {})
        for child in node.body:
            self.visit(child)
        self.class_stack.pop()
        self.scope_stack.pop()
        if self.flow_terminated:
            return
        self._decorators(node_id, node.decorator_list)
        if self.flow_terminated:
            return
        decorator_result = self.decorator_results.get(node_id)
        if node.decorator_list and (not decorator_result or decorator_result.node_id != node_id):
            self.decorated_definitions.add(node_id)
            if decorator_result and (decorator_result.node_id or decorator_result.category == "noninstance"):
                self.variable_bindings.setdefault(definition_owner, {})[node.name] = decorator_result
            else:
                self._invalidate_name(node.name, definition_owner)
        else:
            self._bind_definition(node.name, node_id, definition_owner)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        node_id = self.module.symbols[id(node)]
        descriptor_names = {
            decorator.id for decorator in node.decorator_list
            if isinstance(decorator, ast.Name) and decorator.id in {"staticmethod", "classmethod"}
        }
        if "staticmethod" in descriptor_names:
            self.static_methods.add(node_id)
        if "classmethod" in descriptor_names:
            self.class_methods.add(node_id)
        for default in [*node.args.defaults, *(value for value in node.args.kw_defaults if value)]:
            self.visit(default)
        positional = [*node.args.posonlyargs, *node.args.args]
        defaults = {
            argument.arg: binding
            for argument, value in zip(positional[-len(node.args.defaults) :], node.args.defaults)
            if (binding := self._value_binding(value)).node_id or binding.category == "noninstance"
        } if node.args.defaults else {}
        defaults.update({
            argument.arg: binding
            for argument, value in zip(node.args.kwonlyargs, node.args.kw_defaults)
            if value is not None
            and ((binding := self._value_binding(value)).node_id or binding.category == "noninstance")
        })
        self.function_defaults[node_id] = defaults
        self._decorators(node_id, node.decorator_list)
        if self.flow_terminated:
            return
        definition_owner = self._binding_scope(node.name)
        if definition_owner != self.scope:
            self._variable(node.name, node, scope=definition_owner)
        decorator_result = self.decorator_results.get(node_id)
        replaced = node.decorator_list and len(descriptor_names) != len(node.decorator_list) and (
            not decorator_result or decorator_result.node_id != node_id
        )
        if replaced:
            self.decorated_definitions.add(node_id)
            if decorator_result and (decorator_result.node_id or decorator_result.category == "noninstance"):
                self.variable_bindings.setdefault(definition_owner, {})[node.name] = decorator_result
            else:
                self._invalidate_name(node.name, definition_owner)
        else:
            self._bind_definition(node.name, node_id, definition_owner)
        if node_id not in self.function_contexts:
            self.function_order.append(node_id)
        self.function_contexts[node_id] = (node, tuple(self.scope_stack), tuple(self.class_stack))

    def _execute_function(
        self,
        node_id: str,
        arguments: dict[str, Binding] | None = None,
        *,
        isolated: bool = False,
        merge: bool = False,
        suspend_at_yield: bool = False,
        resume_after_yields: int = 0,
    ) -> str | None:
        context = self.function_contexts.get(node_id)
        if not context or node_id in self.active_functions:
            return None
        node, scopes, classes = context
        snapshots = {
            scope: (
                dict(self.variable_bindings.get(scope, {})),
                dict(self.variable_types.get(scope, {})),
                dict(self.module.aliases.get(scope, {})),
                dict(self.deferred_values.get(scope, {})),
                dict(self.literal_mappings.get(scope, {})),
                dict(self.literal_truths.get(scope, {})),
            )
            for scope in scopes
        } if isolated else {}
        saved_scopes, saved_classes = self.scope_stack, self.class_stack
        saved_terminated, saved_exception = self.flow_terminated, self.raised_exception
        saved_yields_to_skip = self.yields_to_skip
        self.scope_stack, self.class_stack = list(scopes), list(classes)
        self.flow_terminated = None
        self.raised_exception = None
        self.variable_bindings[node_id] = {}
        self.variable_types[node_id] = {}
        self.module.aliases[node_id] = {}
        self.deferred_values[node_id] = {}
        self.literal_mappings[node_id] = {}
        self.literal_truths[node_id] = {}
        self.function_returns.pop(node_id, None)
        self.function_return_truth.pop(node_id, None)
        self.function_raises.pop(node_id, None)
        self.active_functions.add(node_id)
        self.isolated_execution_depth += int(isolated)
        self.suspend_at_yield_depth += int(suspend_at_yield)
        if suspend_at_yield:
            self.yields_to_skip = resume_after_yields
        outcome: str | None = None
        try:
            self._function_body(node, arguments or {}, resume_after_yields)
            outcome = self.flow_terminated
            if outcome is None:
                self.function_returns[node_id] = Binding("", "Constant", "noninstance", ast.Constant(None))
                self.function_return_truth[node_id] = False
            if outcome == "raise":
                self.function_raises[node_id] = self.raised_exception
            self.executed_functions.add(node_id)
        finally:
            self.suspend_at_yield_depth -= int(suspend_at_yield)
            self.yields_to_skip = saved_yields_to_skip
            self.isolated_execution_depth -= int(isolated)
            self.active_functions.remove(node_id)
            self.scope_stack, self.class_stack = saved_scopes, saved_classes
            self.flow_terminated = saved_terminated
            self.raised_exception = saved_exception
            for scope, (bindings, types, aliases, deferred, mappings, truths) in snapshots.items():
                changed = {
                    name
                    for before, after in (
                        (bindings, self.variable_bindings.get(scope, {})),
                        (types, self.variable_types.get(scope, {})),
                        (aliases, self.module.aliases.get(scope, {})),
                        (deferred, self.deferred_values.get(scope, {})),
                        (mappings, self.literal_mappings.get(scope, {})),
                        (truths, self.literal_truths.get(scope, {})),
                    )
                    for name in before.keys() | after.keys()
                    if before.get(name) != after.get(name)
                } if merge else ()
                self.variable_bindings[scope] = bindings
                self.variable_types[scope] = types
                self.module.aliases[scope] = aliases
                self.deferred_values[scope] = deferred
                self.literal_mappings[scope] = mappings
                self.literal_truths[scope] = truths
                if merge:
                    for name in changed:
                        self._invalidate_name(name, scope)
        return outcome

    def _function_body(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parameter_bindings: dict[str, Binding],
        resume_after_yields: int = 0,
    ) -> None:
        node_id = self.module.symbols[id(node)]
        self.scope_stack.append(node_id)
        self.variables.setdefault(node_id, {})
        self.variable_types.setdefault(node_id, {})
        self.variable_bindings.setdefault(node_id, {})
        locals_, globals_, nonlocals_ = local_bindings(node.body)
        self.global_names[node_id] = globals_
        self.nonlocal_names[node_id] = nonlocals_
        parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        argument_names = {argument.arg for argument in parameters}
        if node.args.vararg:
            argument_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            argument_names.add(node.args.kwarg.arg)
        self.declared_names[node_id] = set(locals_) | argument_names
        for argument in parameters:
            variable_id = self._variable(argument.arg, argument, "parameter")
            self.graph.add_edge("writes", node_id, variable_id, self.module.relative_path, argument, "parameter binding")
            if argument.arg in parameter_bindings:
                self.variable_bindings[node_id][argument.arg] = parameter_bindings[argument.arg]
            annotation = self.resolve_annotation(argument.annotation)
            if annotation:
                self.variable_types[node_id][argument.arg] = annotation
        if node.returns:
            self.resolve_annotation(node.returns)
        if node.args.vararg:
            self._variable(node.args.vararg.arg, node.args.vararg, "parameter")
        if node.args.kwarg:
            self._variable(node.args.kwarg.arg, node.args.kwarg, "parameter")
        for name, at in locals_.items():
            self._variable(name, at)
        body = self._linear_known_branches(node.body) if self._lazy_function(node) else node.body
        for child in body:
            self.visit(child)
            if self.flow_terminated:
                break
        self.scope_stack.pop()

    @classmethod
    def _linear_known_branches(cls, statements: list[ast.stmt]) -> list[ast.stmt]:
        result: list[ast.stmt] = []
        for statement in statements:
            if isinstance(statement, ast.If) and (truth := literal_truth(statement.test)) is not None:
                result.extend(cls._linear_known_branches(statement.body if truth else statement.orelse))
            else:
                result.append(statement)
        return result

    def _bind_definition(self, name: str, node_id: str, scope: str | None = None) -> None:
        owner = scope or self.scope
        bindings = self.variable_bindings.setdefault(owner, {})
        if self.control_depth:
            self._invalidate_name(name, owner)
            return
        node = self.graph.nodes[node_id]
        bindings[name] = Binding(node_id, node["qualifiedName"], "class" if node["kind"] == "class" else "symbol")

    def _invalidate_name(self, name: str, owner: str | None = None) -> None:
        target = owner or self._binding_scope(name)
        self.variable_bindings.setdefault(target, {}).pop(name, None)
        self.variable_types.setdefault(target, {}).pop(name, None)
        self.module.aliases.setdefault(target, {}).pop(name, None)
        self.deferred_values.setdefault(target, {}).pop(name, None)
        self.literal_mappings.setdefault(target, {}).pop(name, None)
        self.literal_truths.setdefault(target, {}).pop(name, None)

    def _invalidate_scope(self, owner: str) -> None:
        self.variable_bindings.setdefault(owner, {}).clear()
        self.variable_types.setdefault(owner, {}).clear()
        self.module.aliases.setdefault(owner, {}).clear()
        self.deferred_values.setdefault(owner, {}).clear()
        self.literal_mappings.setdefault(owner, {}).clear()
        self.literal_truths.setdefault(owner, {}).clear()

    def _is_shadowed(self, name: str) -> bool:
        return any(name in names for names in reversed(self.shadowed_names))

    def _decorators(self, source: str, decorators: list[ast.expr]) -> None:
        resolved: list[Binding | None] = []
        for decorator in decorators:
            expression = decorator.func if isinstance(decorator, ast.Call) else decorator
            binding = self.resolve_expression(expression)
            self.graph.add_edge("decorates", source, binding.node_id, self.module.relative_path, decorator)
            if isinstance(decorator, ast.Call):
                self.visit(decorator)
                if self.flow_terminated:
                    return
                resolved.append(self._value_binding(decorator))
            else:
                resolved.append(binding)
        result = Binding(
            source,
            self.graph.nodes[source]["qualifiedName"],
            "class" if self.graph.nodes[source]["kind"] == "class" else "symbol",
        )
        for binding in reversed(resolved):
            if binding is None:
                continue
            if binding.category == "class":
                instance = Binding(binding.node_id, binding.qualified_name, "instance")
                for name in ("__new__", "__init__"):
                    method_id = self._class_member(binding, name)
                    context = self.function_contexts.get(method_id or "")
                    if not context:
                        continue
                    parameters = [*context[0].args.posonlyargs, *context[0].args.args]
                    arguments = {parameters[1].arg: result} if len(parameters) > 1 else {}
                    outcome = self._execute_function(method_id, arguments)
                    if outcome == "raise":
                        self.flow_terminated = "raise"
                        self.raised_exception = self.function_raises.get(method_id)
                        return
                    returned = self.function_returns.get(method_id)
                    if name == "__new__" and returned and returned.category != "instance":
                        if returned:
                            instance = returned
                        break
                result = instance
                if result.node_id or result.category == "noninstance":
                    self.decorator_results[source] = result
                continue
            execute_id = binding.node_id
            bound = False
            if binding.category == "instance":
                execute_id = self._class_member(binding, "__call__") or ""
                if execute_id in self.decorated_definitions:
                    execute_id = self.decorator_results.get(execute_id, Binding("", "", "unresolved")).node_id
                bound = True
            context = self.function_contexts.get(execute_id)
            if context and not self.isolated_execution_depth and not self._lazy_function(context[0]):
                parameters = [*context[0].args.posonlyargs, *context[0].args.args]
                index = int(bound)
                if len(parameters) <= index:
                    continue
                outcome = self._execute_function(execute_id, {parameters[index].arg: result})
                if outcome == "raise":
                    self.flow_terminated = "raise"
                    self.raised_exception = self.function_raises.get(execute_id)
                    return
                returned = self.function_returns.get(execute_id)
                if returned and (returned.node_id or returned.category == "noninstance"):
                    result = returned
                    self.decorator_results[source] = result

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.split(".")[0]
            owner = self._binding_scope(bound_name)
            aliases = self.module.aliases.setdefault(owner, {})
            self.deferred_values.setdefault(owner, {}).pop(bound_name, None)
            self.literal_mappings.setdefault(owner, {}).pop(bound_name, None)
            self.literal_truths.setdefault(owner, {}).pop(bound_name, None)
            if self.control_depth:
                aliases.pop(bound_name, None)
                self.variable_bindings.setdefault(owner, {}).pop(bound_name, None)
                continue
            target = self.analyzer.resolve_absolute(alias.name, self.module, alias)
            binding = (
                self.analyzer.resolve_absolute(bound_name, self.module, alias)
                if not alias.asname and "." in alias.name else target
            )
            aliases[bound_name] = binding
            if owner != self.scope and bound_name not in self.variables.get(owner, {}):
                self._variable(bound_name, alias, scope=owner)
            if bound_name in self.variables.get(owner, {}):
                self.variable_bindings.setdefault(owner, {})[bound_name] = binding

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self.analyzer._absolute_from(self.module, node.module, node.level)
        for alias in node.names:
            if alias.name == "*":
                continue
            bound_name = alias.asname or alias.name
            owner = self._binding_scope(bound_name)
            aliases = self.module.aliases.setdefault(owner, {})
            self.deferred_values.setdefault(owner, {}).pop(bound_name, None)
            self.literal_mappings.setdefault(owner, {}).pop(bound_name, None)
            self.literal_truths.setdefault(owner, {}).pop(bound_name, None)
            if self.control_depth:
                aliases.pop(bound_name, None)
                self.variable_bindings.setdefault(owner, {}).pop(bound_name, None)
                continue
            binding = self.analyzer.resolve_absolute(
                f"{base}.{alias.name}" if base else alias.name,
                self.module,
                alias,
            )
            aliases[bound_name] = binding
            if owner != self.scope and bound_name not in self.variables.get(owner, {}):
                self._variable(bound_name, alias, scope=owner)
            if bound_name in self.variables.get(owner, {}):
                self.variable_bindings.setdefault(owner, {})[bound_name] = binding

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        if self.flow_terminated:
            return
        inferred = self.infer_type(node.value) if self.control_depth == 0 else None
        binding = self._value_binding(node.value) if self.control_depth == 0 else None
        mapping = self._known_mapping(node.value) if self.control_depth == 0 else None
        truth = self._expression_truth(node.value) if self.control_depth == 0 else None
        for target in node.targets:
            self._write_target(target, inferred, binding if binding and (binding.node_id or binding.category == "noninstance") else None)
            self._store_deferred(target, node.value)
            self._store_literal_mapping(target, node.value, mapping)
            self._store_literal_truth(target, truth)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            self.visit(node.value)
            if self.flow_terminated:
                return
        inferred = (
            self.resolve_annotation(node.annotation) or (self.infer_type(node.value) if node.value else None)
            if self.control_depth == 0 else None
        )
        binding = self._value_binding(node.value) if node.value and self.control_depth == 0 else None
        mapping = self._known_mapping(node.value) if node.value and self.control_depth == 0 else None
        truth = self._expression_truth(node.value) if node.value and self.control_depth == 0 else None
        self._write_target(node.target, inferred, binding if binding and (binding.node_id or binding.category == "noninstance") else None)
        if node.value:
            self._store_deferred(node.target, node.value)
            self._store_literal_mapping(node.target, node.value, mapping)
            self._store_literal_truth(node.target, truth)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._read_target(node.target)
        self.visit(node.value)
        self._write_target(node.target, None)
        if isinstance(node.target, ast.Subscript) and (mapping := self._known_mapping(node.target.value)) is not None:
            self._replace_mapping(mapping, None)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                self.visit(target)
                continue
            self._write_target(target, None)
            if (mapping := self._known_mapping(target.value)) is None:
                continue
            key = self._literal_value(target.slice)
            if not isinstance(key, str) or self.control_depth:
                self._replace_mapping(mapping, None)
            elif key not in mapping:
                self.flow_terminated = "raise"
                self.raised_exception = "builtins.KeyError"
            else:
                self._replace_mapping(mapping, {name: value for name, value in mapping.items() if name != key})

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.visit(node.value)
        self.visit(node.slice)
        if self.flow_terminated or not isinstance(node.ctx, ast.Load):
            return
        container = node.value
        if isinstance(container, ast.Name):
            container = self.resolve_name(container.id, unresolved=False).value
        index = self._literal_value(node.slice)
        if isinstance(container, (ast.Tuple, ast.List)) and isinstance(index, int) and not any(
            isinstance(item, ast.Starred) for item in container.elts
        ) and not -len(container.elts) <= index < len(container.elts):
            self.flow_terminated = "raise"
            self.raised_exception = "builtins.IndexError"
        elif (mapping := self._known_mapping(node.value)) is not None and isinstance(index, str) and index not in mapping:
            self.flow_terminated = "raise"
            self.raised_exception = "builtins.KeyError"

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._consume_expression(node.iter, exhaust=not self._block_stops_loop(node.body))
        elements = self._known_iterable(node.iter)
        if elements is not None:
            iteration_limit = 1_000 + self.yields_to_skip
            for count, element in enumerate(elements):
                # ponytail: bound eager expansion; suspended generators consume only through their next yield.
                if count >= iteration_limit:
                    self._visit_uncertain(node.body)
                    if self.suspend_at_yield_depth:
                        self.flow_terminated = "yield"
                        return
                    break
                self._bind_iteration_target(node.target, element)
                for child in node.body:
                    self.visit(child)
                    if self.flow_terminated == "break":
                        self.flow_terminated = None
                        return
                    if self.flow_terminated == "continue":
                        self.flow_terminated = None
                        break
                    if self.flow_terminated:
                        return
            for child in node.orelse:
                self.visit(child)
            return
        self.control_depth += 1
        try:
            self._write_target(node.target, None)
        finally:
            self.control_depth -= 1
        self._visit_uncertain(node.body)
        if self.suspend_at_yield_depth and any(
            isinstance(child, (ast.Yield, ast.YieldFrom))
            for statement in node.body for child in ast.walk(statement)
        ):
            self.flow_terminated = "yield"
            return
        self._visit_uncertain(node.orelse)

    visit_AsyncFor = visit_For

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        truth = self._expression_truth(node.test)
        if truth is not None:
            for child in node.body if truth else node.orelse:
                self.visit(child)
                if self.flow_terminated:
                    break
            return
        self._visit_uncertain(node.body)
        self._visit_uncertain(node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        truth = self._expression_truth(node.test)
        if truth is not None:
            if not truth:
                for child in node.orelse:
                    self.visit(child)
                return
            for child in node.body:
                self.visit(child)
                if self.flow_terminated in {"break", "continue"}:
                    self.flow_terminated = None
                    return
                if self.flow_terminated:
                    return
            if not any(isinstance(child, ast.Break) for statement in node.body for child in ast.walk(statement)):
                self.flow_terminated = "loop"
            return
        self._visit_uncertain(node.body)
        self._visit_uncertain(node.orelse)

    def visit_Break(self, _: ast.Break) -> None:
        self.flow_terminated = "break"

    def visit_Continue(self, _: ast.Continue) -> None:
        self.flow_terminated = "continue"

    def visit_Return(self, node: ast.Return) -> None:
        if node.value:
            self.visit(node.value)
            if self.flow_terminated:
                return
            if self.control_depth == 0:
                if (truth := self._expression_truth(node.value)) is not None:
                    self.function_return_truth[self.scope] = truth
                binding = self._value_binding(node.value)
                if binding.node_id or binding.category == "noninstance":
                    self.function_returns[self.scope] = binding
        elif self.control_depth == 0:
            self.function_returns[self.scope] = Binding("", "Constant", "noninstance", ast.Constant(None))
            self.function_return_truth[self.scope] = False
        self.flow_terminated = "return"

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc:
            self.visit(node.exc)
            expression = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            binding = self.resolve_expression(expression, unresolved=False)
            self.raised_exception = binding.qualified_name if binding.node_id else None
        if node.cause:
            self.visit(node.cause)
        self.flow_terminated = "raise"

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        for index, value in enumerate(node.values):
            self.visit(value)
            if self.flow_terminated:
                return
            truth = self._expression_truth(value)
            if truth is not None:
                if (isinstance(node.op, ast.And) and not truth) or (isinstance(node.op, ast.Or) and truth):
                    return
            else:
                self._visit_uncertain(node.values[index + 1 :])
                return

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        truth = self._expression_truth(node.test)
        if truth is not None:
            self.visit(node.body if truth else node.orelse)
        else:
            self._visit_uncertain((node.body, node.orelse))

    def visit_Await(self, node: ast.Await) -> None:
        if isinstance(node.value, ast.Call):
            self.eager_lazy_calls.add(id(node.value))
        try:
            self.visit(node.value)
            if not isinstance(node.value, ast.Call):
                self._consume_expression(node.value)
        finally:
            self.eager_lazy_calls.discard(id(node.value))

    def visit_Try(self, node: ast.Try) -> None:
        for child in node.body:
            self.visit(child)
            if self.flow_terminated:
                break
        nonraising = all(self._statement_proven_nonraising(child) for child in node.body)
        body_terminated = self.flow_terminated
        raised_exception = self.raised_exception
        if body_terminated == "yield":
            return
        self.flow_terminated = None
        self.raised_exception = None
        handled = False
        if body_terminated == "raise" and raised_exception:
            for handler in node.handlers:
                if handler.type:
                    self.visit(handler.type)
                if not self._handler_matches(handler.type, raised_exception):
                    continue
                if handler.name:
                    self._invalidate_name(handler.name)
                for child in handler.body:
                    self.visit(child)
                    if self.flow_terminated:
                        break
                handled = True
                break
        elif body_terminated != "return" and not nonraising:
            for handler in node.handlers:
                if handler.type:
                    self.visit(handler.type)
                if handler.name:
                    self._invalidate_name(handler.name)
                self._visit_uncertain(handler.body)
        if body_terminated is None:
            if nonraising:
                for child in node.orelse:
                    self.visit(child)
            else:
                self._visit_uncertain(node.orelse)
        if self.flow_terminated == "yield":
            return
        for child in node.finalbody:
            self.visit(child)
            if self.flow_terminated:
                break
        final_terminated = self.flow_terminated
        self.flow_terminated = final_terminated or (
            None if body_terminated == "raise" and handled else body_terminated
        )

    visit_TryStar = visit_Try

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        exits: list[Binding] = []
        enter_name, exit_name = (
            ("__aenter__", "__aexit__") if isinstance(node, ast.AsyncWith) else ("__enter__", "__exit__")
        )
        for item in node.items:
            self.visit(item.context_expr)
            binding = self.resolve_expression(item.context_expr, unresolved=False)
            if binding.category == "instance":
                enter_result: Binding | None = None
                if method_id := self._class_member(binding, enter_name):
                    target = self.decorator_results.get(method_id) if method_id in self.decorated_definitions else Binding(
                        method_id, self.graph.nodes[method_id]["qualifiedName"], "symbol"
                    )
                    if target:
                        self.graph.add_edge("calls", self.scope, target.node_id, self.module.relative_path, item.context_expr)
                        outcome = self._execute_function(target.node_id, self.function_defaults.get(target.node_id))
                        enter_result = self.function_returns.get(target.node_id)
                        if outcome == "raise":
                            self.flow_terminated = "raise"
                            self.raised_exception = self.function_raises.get(target.node_id)
                            if self._run_context_exits(exits, item.context_expr):
                                self.suppressed_withs.add(id(node))
                            return
                if method_id := self._class_member(binding, exit_name):
                    target = self.decorator_results.get(method_id) if method_id in self.decorated_definitions else Binding(
                        method_id, self.graph.nodes[method_id]["qualifiedName"], "symbol"
                    )
                    if target:
                        exits.append(target)
            if item.optional_vars:
                self._write_target(item.optional_vars, None, enter_result if binding.category == "instance" else None)
        for child in node.body:
            self.visit(child)
            if self.flow_terminated:
                break
        if self.flow_terminated == "yield":
            return
        if self._run_context_exits(exits, node):
            self.suppressed_withs.add(id(node))

    def _run_context_exits(self, exits: list[Binding], at: ast.AST) -> bool:
        had_exception = self.flow_terminated == "raise"
        for target in reversed(exits):
            self.graph.add_edge("calls", self.scope, target.node_id, self.module.relative_path, at)
            outcome = self._execute_function(target.node_id, self.function_defaults.get(target.node_id))
            if outcome == "raise":
                self.flow_terminated = "raise"
                self.raised_exception = self.function_raises.get(target.node_id)
            elif self.flow_terminated == "raise" and self.function_return_truth.get(target.node_id) is True:
                self.flow_terminated = None
                self.raised_exception = None
        return had_exception and self.flow_terminated != "raise"

    visit_AsyncWith = visit_With

    def _statement_proven_nonraising(self, statement: ast.stmt) -> bool:
        if isinstance(statement, (ast.Pass, ast.Global, ast.Nonlocal)):
            return True
        if isinstance(statement, (ast.With, ast.AsyncWith)) and id(statement) in self.suppressed_withs:
            return True
        if isinstance(statement, ast.If):
            truth = literal_truth(statement.test)
            return truth is not None and all(
                self._statement_proven_nonraising(child)
                for child in (statement.body if truth else statement.orelse)
            )
        if isinstance(statement, ast.Return):
            return statement.value is None or isinstance(statement.value, (ast.Constant, ast.Name))
        if isinstance(statement, ast.Assign):
            return self._expression_proven_nonraising(statement.value)
        if isinstance(statement, ast.AnnAssign):
            return statement.value is None or self._expression_proven_nonraising(statement.value)
        return isinstance(statement, ast.Expr) and self._expression_proven_nonraising(statement.value)

    def _expression_proven_nonraising(self, expression: ast.expr) -> bool:
        if isinstance(expression, (ast.Constant, ast.Name, ast.Lambda)):
            return True
        if not isinstance(expression, ast.Call):
            return False
        binding = self.resolve_expression(expression.func, unresolved=False)
        context = self.function_contexts.get(binding.node_id)
        return bool(context and all(self._statement_proven_nonraising(child) for child in context[0].body))

    def _handler_matches(self, expression: ast.expr | None, raised: str) -> bool:
        if expression is None:
            return True
        if isinstance(expression, ast.Tuple):
            return any(self._handler_matches(item, raised) for item in expression.elts)
        binding = self.resolve_expression(expression, unresolved=False)
        if binding.qualified_name in {raised, "builtins.Exception", "builtins.BaseException"}:
            return True
        if raised.startswith("builtins.") and binding.qualified_name.startswith("builtins."):
            raised_type = getattr(builtins, raised.removeprefix("builtins."), None)
            handler_type = getattr(builtins, binding.qualified_name.removeprefix("builtins."), None)
            return isinstance(raised_type, type) and isinstance(handler_type, type) and issubclass(raised_type, handler_type)
        raised_id = self.analyzer.nodes_by_qualified.get(raised)
        return bool(raised_id and binding.qualified_name in {
            name for _, name in self._class_mro(raised_id, raised)
        })

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        self.control_depth += 1
        self.uncertain_execution_depth += 1
        try:
            for case in node.cases:
                collector = LocalAssignmentCollector()
                collector.visit(case.pattern)
                for name in collector.names:
                    self._invalidate_name(name)
                if case.guard:
                    self.visit(case.guard)
                for child in case.body:
                    self.visit(child)
        finally:
            self.uncertain_execution_depth -= 1
            self.control_depth -= 1

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *(value for value in node.args.kw_defaults if value)]:
            self.visit(default)
        self.lambda_contexts[id(node)] = (tuple(self.scope_stack), tuple(self.class_stack))

    def _visit_comprehension(self, node: ast.ListComp | ast.SetComp | ast.DictComp) -> None:
        self._evaluate_comprehension(node, visit_first=True)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        if node.generators:
            self.visit(node.generators[0].iter)

    def _evaluate_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        *,
        visit_first: bool,
    ) -> None:
        if not node.generators:
            return
        first_iter = node.generators[0].iter
        if visit_first:
            self.visit(first_iter)
        if isinstance(first_iter, (ast.List, ast.Tuple, ast.Set)) and not first_iter.elts:
            return
        names = {
            child.id
            for generator in node.generators
            for child in ast.walk(generator.target)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        }
        owner = self.scope
        saved = (
            dict(self.variables.setdefault(owner, {})),
            dict(self.variable_types.setdefault(owner, {})),
            dict(self.variable_bindings.setdefault(owner, {})),
            dict(self.module.aliases.setdefault(owner, {})),
            dict(self.deferred_values.setdefault(owner, {})),
        )
        self.shadowed_names.append(names)
        try:
            for index, generator in enumerate(node.generators):
                if index:
                    self.visit(generator.iter)
                if isinstance(generator.iter, (ast.List, ast.Tuple, ast.Set)) and not generator.iter.elts:
                    return
                self._write_target(generator.target, None)
                for condition in generator.ifs:
                    self.visit(condition)
                    if literal_truth(condition) is False:
                        return
            if isinstance(node, ast.DictComp):
                self.visit(node.key)
                self.visit(node.value)
            else:
                self.visit(node.elt)
        finally:
            self.shadowed_names.pop()
            current = (
                self.variables[owner],
                self.variable_types[owner],
                self.variable_bindings[owner],
                self.module.aliases[owner],
                self.deferred_values[owner],
            )
            for before, after in zip(saved, current):
                for name in names:
                    if name in before:
                        after[name] = before[name]
                    else:
                        after.pop(name, None)

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension

    def _visit_uncertain(self, children: Iterable[ast.AST]) -> None:
        saved_terminated = self.flow_terminated
        self.flow_terminated = None
        self.control_depth += 1
        self.uncertain_execution_depth += 1
        try:
            for child in children:
                self.visit(child)
                if self.flow_terminated:
                    break
        finally:
            self.uncertain_execution_depth -= 1
            self.control_depth -= 1
            self.flow_terminated = saved_terminated

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            variable_id = self.lookup_variable(node.id)
            if variable_id:
                self.graph.add_edge("reads", self.scope, variable_id, self.module.relative_path, node)
        elif isinstance(node.ctx, ast.Store):
            self._write_target(node, None)
        elif isinstance(node.ctx, ast.Del):
            variable_id = self.lookup_variable(node.id)
            if variable_id:
                self.graph.add_edge("writes", self.scope, variable_id, self.module.relative_path, node, "binding deleted")
            self._invalidate_name(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and node.value.id == "self" and self.class_stack:
            variable_id = self._field(node.attr, node)
            kind = "writes" if isinstance(node.ctx, (ast.Store, ast.Del)) else "reads"
            self.graph.add_edge(kind, self.scope, variable_id, self.module.relative_path, node)
            if isinstance(node.ctx, ast.Del):
                self.field_types.setdefault(self.class_stack[-1], {}).pop(node.attr, None)
        else:
            self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.visit(node.func)
        if self.flow_terminated:
            return
        binding = self.resolve_expression(node.func)
        self.call_bindings[id(node)] = binding
        self.call_results.pop(id(node), None)
        self.call_truths.pop(id(node), None)
        executions: list[tuple[str, bool, bool]] = []
        if binding.category == "class":
            self.graph.add_edge("constructs", self.scope, binding.node_id, self.module.relative_path, node)
            for name in ("__new__", "__init__"):
                if method_id := self._class_member(binding, name):
                    target = self.decorator_results.get(method_id) if method_id in self.decorated_definitions else Binding(
                        method_id,
                        self.graph.nodes[method_id]["qualifiedName"],
                        "symbol",
                    )
                    if target:
                        edge_added = not executions
                        if edge_added:
                            self.graph.add_edge("calls", self.scope, target.node_id, self.module.relative_path, node)
                        executions.append((target.node_id, True, edge_added))
        elif binding.category == "instance":
            if call_id := self._class_member(binding, "__call__"):
                target = self.decorator_results.get(call_id) if call_id in self.decorated_definitions else Binding(
                    call_id,
                    self.graph.nodes[call_id]["qualifiedName"],
                    "symbol",
                )
                if target:
                    self.graph.add_edge("calls", self.scope, target.node_id, self.module.relative_path, node)
                    executions.append((target.node_id, True, True))
                else:
                    unresolved = self.graph.add_unresolved(
                        self.module, ast.unparse(node.func), node.func, "callable result cannot be resolved statically"
                    )
                    self.graph.add_edge("calls", self.scope, unresolved.node_id, self.module.relative_path, node)
        else:
            target = binding if binding.node_id else self.graph.add_unresolved(
                self.module, ast.unparse(node.func), node.func, "call target cannot be resolved statically"
            )
            self.graph.add_edge("calls", self.scope, target.node_id, self.module.relative_path, node)
            executions.append((binding.node_id, False, True))
            if binding.category == "external":
                self.graph.add_edge("api_calls", self.scope, binding.node_id, self.module.relative_path, node)
            elif is_test_path(Path(self.module.relative_path)) and binding.category not in {"unresolved", "external"}:
                self.graph.add_edge("test_covers", self.scope, binding.node_id, self.module.relative_path, node)
        for argument in [*node.args, *node.keywords]:
            if self.flow_terminated:
                return
            self.visit(argument.value if isinstance(argument, ast.keyword) else argument)
        if self.flow_terminated:
            return
        self._mutate_mapping_call(node)
        if self.flow_terminated:
            return
        if binding.qualified_name == "builtins.globals":
            self._invalidate_scope(self.module.node_id)
        elif binding.qualified_name in {"builtins.locals", "builtins.vars", "builtins.exec"}:
            if binding.qualified_name != "builtins.vars" or not node.args and not node.keywords:
                self._invalidate_scope(self.scope)
        if binding.qualified_name == "asyncio.run" and node.args:
            self._consume_expression(node.args[0])
        elif binding.qualified_name in {
            "builtins.all", "builtins.any", "builtins.dict", "builtins.frozenset", "builtins.list",
            "builtins.max", "builtins.min", "builtins.set", "builtins.sorted", "builtins.sum", "builtins.tuple",
        } and node.args:
            self._consume_expression(node.args[0])
        elif binding.qualified_name == "builtins.next" and node.args:
            self._consume_expression(node.args[0], exhaust=False)
        deferred = binding.value if isinstance(binding.value, ast.Lambda) else node.func if isinstance(node.func, ast.Lambda) else (
            self._lookup_deferred(node.func.id) if isinstance(node.func, ast.Name) else None
        )
        if isinstance(deferred, ast.Lambda):
            if not self._call_is_valid(deferred, node, False):
                self.flow_terminated = "raise"
                self.raised_exception = "builtins.TypeError"
                return
            parameter_names = {
                argument.arg
                for argument in [*deferred.args.posonlyargs, *deferred.args.args, *deferred.args.kwonlyargs]
            }
            bindings = self._lambda_arguments(deferred, node)
            saved_scopes, saved_classes = self.scope_stack, self.class_stack
            scopes, classes = self.lambda_contexts.get(id(deferred), (saved_scopes, saved_classes))
            self.scope_stack, self.class_stack = list(scopes), list(classes)
            self.expression_bindings.append(bindings)
            self.shadowed_names.append(parameter_names - bindings.keys())
            try:
                self.visit(deferred.body)
                if not self.flow_terminated:
                    self.call_results[id(node)] = self._value_binding(deferred.body)
                    if (truth := self._expression_truth(deferred.body)) is not None:
                        self.call_truths[id(node)] = truth
            finally:
                self.shadowed_names.pop()
                self.expression_bindings.pop()
                self.scope_stack, self.class_stack = saved_scopes, saved_classes
        for execute_id, bound_call, edge_added in executions:
            context = self.function_contexts.get(execute_id)
            if not context or self.isolated_execution_depth or (
                self._lazy_function(context[0]) and id(node) not in self.eager_lazy_calls
            ):
                if not edge_added:
                    self.graph.add_edge("calls", self.scope, execute_id, self.module.relative_path, node)
                continue
            if isinstance(node.func, ast.Attribute) and binding.category not in {"class", "instance"}:
                receiver = self.resolve_expression(node.func.value, unresolved=False)
                bound_call = receiver.category == "instance" or (
                    isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
                )
                if execute_id in self.static_methods:
                    bound_call = False
                elif execute_id in self.class_methods:
                    bound_call = True
            if not self._call_is_valid(context[0], node, bound_call):
                self.flow_terminated = "raise"
                self.raised_exception = "builtins.TypeError"
                if binding.category == "class":
                    break
                continue
            if not edge_added:
                self.graph.add_edge("calls", self.scope, execute_id, self.module.relative_path, node)
            outcome = self._execute_function(
                execute_id,
                self._call_arguments(context[0], node, bound_call),
                isolated=bool(self.uncertain_execution_depth),
                merge=bool(self.uncertain_execution_depth),
            )
            if outcome == "raise":
                self.flow_terminated = "raise"
                self.raised_exception = self.function_raises.get(execute_id)
            returned = self.function_returns.get(execute_id)
            if binding.category != "class":
                if returned is not None:
                    self.call_results[id(node)] = returned
                if execute_id in self.function_return_truth:
                    self.call_truths[id(node)] = self.function_return_truth[execute_id]
            if binding.category == "class":
                method_name = self.graph.nodes[execute_id]["qualifiedName"].rsplit(".", 1)[-1]
                if method_name == "__new__" and returned is not None:
                    self.call_results[id(node)] = returned
                if outcome == "raise" or returned and (
                    returned.category != "instance"
                    or binding.qualified_name not in {
                        name for _, name in self._class_mro(returned.node_id, returned.qualified_name)
                    }
                ):
                    break

    def _execute_lazy_call(self, call: ast.Call, *, exhaust: bool = True) -> None:
        if id(call) in self.exhausted_lazy_values:
            return
        binding = self.call_bindings.get(id(call)) or self.resolve_expression(call.func, unresolved=False)
        context = self.function_contexts.get(binding.node_id)
        if context and self._lazy_function(context[0]) and not self.isolated_execution_depth:
            bound = False
            if isinstance(call.func, ast.Attribute):
                receiver = self.resolve_expression(call.func.value, unresolved=False)
                bound = receiver.category == "instance" or (
                    isinstance(call.func.value, ast.Name) and call.func.value.id == "self"
                )
                if binding.node_id in self.static_methods:
                    bound = False
                elif binding.node_id in self.class_methods:
                    bound = True
            if not self._call_is_valid(context[0], call, bound):
                return
            position = self.lazy_positions.get(id(call), 0)
            outcome = self._execute_function(
                binding.node_id,
                self._call_arguments(context[0], call, bound),
                isolated=bool(self.uncertain_execution_depth),
                merge=bool(self.uncertain_execution_depth),
                suspend_at_yield=not exhaust,
                resume_after_yields=position,
            )
            if outcome == "yield":
                self.lazy_positions[id(call)] = position + 1
            else:
                self.exhausted_lazy_values.add(id(call))

    def _execute_lazy_binding(self, binding: Binding, *, exhaust: bool = True) -> None:
        context = self.function_contexts.get(binding.node_id)
        if binding.category == "lazy" and context and not self.isolated_execution_depth:
            self._execute_function(
                binding.node_id,
                self.function_defaults.get(binding.node_id),
                isolated=bool(self.uncertain_execution_depth),
                merge=bool(self.uncertain_execution_depth),
                suspend_at_yield=not exhaust,
            )

    def visit_Yield(self, node: ast.Yield) -> None:
        if node.value:
            self.visit(node.value)
        if self.suspend_at_yield_depth:
            if self.yields_to_skip:
                self.yields_to_skip -= 1
            else:
                self.flow_terminated = "yield"

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.visit(node.value)
        self._consume_expression(node.value, exhaust=not self.suspend_at_yield_depth)
        if self.suspend_at_yield_depth:
            if self.yields_to_skip:
                self.yields_to_skip -= 1
            else:
                self.flow_terminated = "yield"

    def _store_deferred(self, target: ast.expr, value: ast.expr) -> None:
        if self.control_depth:
            return
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for child, item in zip(target.elts, value.elts):
                self._store_deferred(child, item)
            return
        if not isinstance(target, ast.Name):
            return
        deferred = isinstance(value, (ast.Lambda, ast.GeneratorExp))
        stored: ast.expr = value
        if isinstance(value, ast.Name) and (original := self._lookup_deferred(value.id)):
            stored = original
            deferred = True
        if isinstance(value, ast.Call):
            binding = self.call_bindings.get(id(value)) or self.resolve_expression(value.func, unresolved=False)
            context = self.function_contexts.get(binding.node_id)
            deferred = bool(context and self._lazy_function(context[0]))
        if deferred:
            self.deferred_values.setdefault(self._binding_scope(target.id), {})[target.id] = stored

    def _lookup_deferred(self, name: str) -> ast.expr | None:
        if self._is_shadowed(name):
            return None
        for scope in self._lexical_scopes():
            if name in self.variables.get(scope, {}):
                return self.deferred_values.get(scope, {}).get(name)
            if name in self.declared_names.get(scope, set()):
                return None
        return None

    def _replace_mapping(self, original: dict[str, Binding], updated: dict[str, Binding] | None) -> None:
        # Copy on write preserves aliases without leaking mutations out of isolated analysis.
        for scope, mappings in self.literal_mappings.items():
            for name, mapping in list(mappings.items()):
                if mapping is original:
                    self.literal_truths.setdefault(scope, {}).pop(name, None)
                    if updated is None:
                        mappings.pop(name)
                    else:
                        mappings[name] = updated

    def _mutate_mapping_call(self, call: ast.Call) -> None:
        if not isinstance(call.func, ast.Attribute) or (mapping := self._known_mapping(call.func.value)) is None:
            return
        name = call.func.attr
        if name not in {"clear", "update", "pop", "setdefault"}:
            return
        if self.control_depth:
            self._replace_mapping(mapping, None)
            return
        updated = dict(mapping)
        returned = Binding("", "Constant", "noninstance", ast.Constant(None))
        if name == "clear":
            if call.args or call.keywords:
                self.flow_terminated, self.raised_exception = "raise", "builtins.TypeError"
                return
            updated.clear()
        elif name == "update":
            if len(call.args) > 1:
                self.flow_terminated, self.raised_exception = "raise", "builtins.TypeError"
                return
            additions = self._known_mapping(call.args[0]) if call.args else {}
            if additions is None:
                self._replace_mapping(mapping, None)
                return
            updated.update(additions)
            for keyword in call.keywords:
                if keyword.arg:
                    updated[keyword.arg] = self._value_binding(keyword.value)
                elif (expanded := self._known_mapping(keyword.value)) is not None:
                    updated.update(expanded)
                else:
                    self._replace_mapping(mapping, None)
                    return
        else:
            if not 1 <= len(call.args) <= 2 or call.keywords:
                self.flow_terminated, self.raised_exception = "raise", "builtins.TypeError"
                return
            key = self._literal_value(call.args[0])
            if not isinstance(key, str):
                self._replace_mapping(mapping, None)
                return
            default = self._value_binding(call.args[1]) if len(call.args) == 2 else returned
            if name == "pop":
                if key not in updated and len(call.args) == 1:
                    self.flow_terminated, self.raised_exception = "raise", "builtins.KeyError"
                    return
                returned = updated.pop(key, default)
            else:
                returned = updated.setdefault(key, default)
        self._replace_mapping(mapping, updated)
        self.call_results[id(call)] = returned

    def _store_literal_mapping(
        self, target: ast.expr, value: ast.expr, mapping: dict[str, Binding] | None,
    ) -> None:
        if isinstance(target, ast.Name) and not self.control_depth and mapping is not None:
            self.literal_mappings.setdefault(self._binding_scope(target.id), {})[target.id] = mapping
        elif isinstance(target, ast.Subscript) and (original := self._known_mapping(target.value)) is not None:
            key = self._literal_value(target.slice)
            updated = None
            if isinstance(key, str) and not self.control_depth:
                updated = {**original, key: self._value_binding(value)}
            self._replace_mapping(original, updated)

    def _known_mapping(self, expression: ast.expr) -> dict[str, Binding] | None:
        if isinstance(expression, ast.Dict):
            result: dict[str, Binding] = {}
            for key, value in zip(expression.keys, expression.values):
                if key is None:
                    expanded = self._known_mapping(value)
                    if expanded is None:
                        return None
                    result.update(expanded)
                elif isinstance(key_value := self._literal_value(key), str):
                    result[key_value] = self._value_binding(value)
                else:
                    return None
            return result
        if isinstance(expression, ast.DictComp):
            result = {}

            def collect(index: int) -> bool:
                generator = expression.generators[index]
                values = self._known_iterable(generator.iter)
                if values is None or generator.is_async:
                    return False
                for count, value in enumerate(values):
                    if count >= 1_000:
                        return False
                    self.expression_bindings.append(self._iteration_bindings(generator.target, value))
                    try:
                        conditions = [self._expression_truth(condition) for condition in generator.ifs]
                        if False in conditions:
                            continue
                        if None in conditions:
                            return False
                        if index + 1 < len(expression.generators):
                            if not collect(index + 1):
                                return False
                        elif isinstance(key := self._literal_value(expression.key), str):
                            result[key] = self._value_binding(expression.value)
                        else:
                            return False
                    finally:
                        self.expression_bindings.pop()
                return True

            return result if collect(0) else None
        if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.BitOr):
            left, right = self._known_mapping(expression.left), self._known_mapping(expression.right)
            return None if left is None or right is None else {**left, **right}
        if isinstance(expression, ast.Call):
            binding = self.call_bindings.get(id(expression)) or self.resolve_expression(expression.func, unresolved=False)
            if binding.qualified_name == "builtins.dict" and len(expression.args) <= 1:
                result = {} if not expression.args else self._known_mapping(expression.args[0])
                if result is None:
                    return None
                result = dict(result)
                for keyword in expression.keywords:
                    if keyword.arg is None:
                        expanded = self._known_mapping(keyword.value)
                        if expanded is None:
                            return None
                        result.update(expanded)
                    else:
                        result[keyword.arg] = self._value_binding(keyword.value)
                return result
        if isinstance(expression, ast.Name) and not self._is_shadowed(expression.id):
            for scope in self._lexical_scopes():
                if expression.id in self.variables.get(scope, {}):
                    return self.literal_mappings.get(scope, {}).get(expression.id)
                if expression.id in self.declared_names.get(scope, set()):
                    return None
        return None

    def _store_literal_truth(self, target: ast.expr, truth: bool | None) -> None:
        if self.control_depth or not isinstance(target, ast.Name):
            return
        if truth is not None:
            self.literal_truths.setdefault(self._binding_scope(target.id), {})[target.id] = truth

    def _literal_value(self, expression: ast.expr) -> Any:
        value = literal_value(expression)
        if value is not UNKNOWN_VALUE:
            return value
        if isinstance(expression, ast.Name):
            binding = next((bindings[expression.id] for bindings in reversed(self.expression_bindings)
                            if expression.id in bindings), None) or self.lookup_variable_binding(expression.id)
            return literal_value(binding.value) if binding and binding.value is not None else UNKNOWN_VALUE
        binding = self._value_binding(expression)
        return literal_value(binding.value) if binding.value is not None else UNKNOWN_VALUE

    def _selected_expression(self, expression: ast.expr) -> ast.expr:
        if isinstance(expression, ast.IfExp) and (truth := self._expression_truth(expression.test)) is not None:
            return expression.body if truth else expression.orelse
        if isinstance(expression, ast.BoolOp):
            for value in expression.values[:-1]:
                truth = self._expression_truth(value)
                if truth is None:
                    return expression
                if truth == isinstance(expression.op, ast.Or):
                    return value
            return expression.values[-1]
        if isinstance(expression, ast.Subscript):
            container = expression.value
            if isinstance(container, ast.Name):
                binding = self.resolve_name(container.id, unresolved=False)
                container = binding.value
            index = self._literal_value(expression.slice)
            if isinstance(container, (ast.Tuple, ast.List)) and isinstance(index, int) and not any(
                isinstance(item, ast.Starred) for item in container.elts
            ):
                try:
                    return container.elts[index]
                except IndexError:
                    pass
        return expression

    def _binding_truth(self, binding: Binding) -> bool | None:
        if binding.value is not None:
            return literal_truth(binding.value)
        if binding.category == "instance":
            hierarchy = self._class_mro(binding.node_id, binding.qualified_name)
            if any(self.graph.nodes[node_id]["external"] or self.graph.nodes[node_id]["unresolved"] for node_id, _ in hierarchy):
                return None
            if not self._class_member(binding, "__bool__") and not self._class_member(binding, "__len__"):
                return True
        if binding.category == "symbol" and binding.node_id in self.function_contexts:
            return True
        return None

    def _expression_truth(self, expression: ast.expr) -> bool | None:
        truth = literal_truth(expression)
        if truth is not None:
            return truth
        if isinstance(expression, ast.Compare):
            left = self._literal_value(expression.left)
            for operation, right_node in zip(expression.ops, expression.comparators):
                right = self._literal_value(right_node)
                compare = COMPARISONS.get(type(operation))
                if left is UNKNOWN_VALUE or right is UNKNOWN_VALUE or compare is None:
                    return None
                try:
                    if not compare(left, right):
                        return False
                except (TypeError, ValueError):
                    return None
                left = right
            return True
        if isinstance(expression, ast.BoolOp):
            values = [self._expression_truth(value) for value in expression.values]
            if all(value is not None for value in values):
                return all(values) if isinstance(expression.op, ast.And) else any(values)
        if isinstance(expression, ast.Name) and not self._is_shadowed(expression.id):
            for bindings in reversed(self.expression_bindings):
                if expression.id in bindings:
                    return self._binding_truth(bindings[expression.id])
            if (mapping := self._known_mapping(expression)) is not None:
                return bool(mapping)
            for scope in self._lexical_scopes():
                if expression.id in self.variables.get(scope, {}):
                    truth = self.literal_truths.get(scope, {}).get(expression.id)
                    return truth if truth is not None else self._binding_truth(self.resolve_name(expression.id, unresolved=False))
                if expression.id in self.declared_names.get(scope, set()):
                    return self._binding_truth(self.resolve_name(expression.id, unresolved=False))
        if isinstance(expression, ast.Call):
            if id(expression) in self.call_truths:
                return self.call_truths[id(expression)]
            binding = self.call_bindings.get(id(expression)) or self.resolve_expression(expression.func, unresolved=False)
            truth = self.function_return_truth.get(binding.node_id)
            return truth if truth is not None else self._binding_truth(self._value_binding(expression))
        if isinstance(expression, ast.IfExp) and (condition := self._expression_truth(expression.test)) is not None:
            return self._expression_truth(expression.body if condition else expression.orelse)
        if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
            operand = self._expression_truth(expression.operand)
            return None if operand is None else not operand
        if isinstance(expression, ast.Subscript):
            return self._binding_truth(self._value_binding(expression))
        if isinstance(expression, ast.DictComp) and (mapping := self._known_mapping(expression)) is not None:
            return bool(mapping)
        return None

    def _value_binding(self, expression: ast.expr) -> Binding:
        selected = self._selected_expression(expression)
        if selected is not expression:
            return self._value_binding(selected)
        if isinstance(expression, ast.Subscript):
            mapping = self._known_mapping(expression.value)
            key = self._literal_value(expression.slice)
            if mapping is not None and isinstance(key, str) and key in mapping:
                return mapping[key]
        if isinstance(
            expression,
            (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Lambda, ast.GeneratorExp,
             ast.ListComp, ast.SetComp, ast.DictComp),
        ):
            return Binding("", type(expression).__name__, "noninstance", expression)
        return self.resolve_expression(expression, unresolved=False)

    def _consume_expression(self, expression: ast.expr, *, exhaust: bool = True) -> None:
        if isinstance(expression, ast.Name):
            deferred = self._lookup_deferred(expression.id)
            if deferred:
                self._consume_expression(deferred, exhaust=exhaust)
                return
        if isinstance(expression, ast.Call):
            self._execute_lazy_call(expression, exhaust=exhaust)
            return
        if isinstance(expression, ast.GeneratorExp):
            self._evaluate_comprehension(expression, visit_first=False)
            return
        self._execute_lazy_binding(self.resolve_expression(expression, unresolved=False), exhaust=exhaust)

    def _known_iterable(self, expression: ast.expr, *, reverse: bool = False) -> Iterable[ast.expr] | None:
        if isinstance(expression, (ast.List, ast.Tuple)):
            return reversed(expression.elts) if reverse else expression.elts
        if isinstance(expression, ast.Dict):
            mapping = self._known_mapping(expression)
            return None if mapping is None else map(ast.Constant, reversed(mapping) if reverse else mapping)
        if not isinstance(expression, ast.Call):
            return None
        binding = self.call_bindings.get(id(expression)) or self.resolve_expression(expression.func, unresolved=False)
        name = binding.qualified_name
        if name == "builtins.range" and 1 <= len(expression.args) <= 3 and not expression.keywords:
            arguments = [self._literal_value(argument) for argument in expression.args]
            if not all(isinstance(argument, int) for argument in arguments):
                return None
            try:
                values = range(*arguments)
            except ValueError:
                return None
            return map(ast.Constant, reversed(values) if reverse else values)
        if name == "builtins.enumerate" and 1 <= len(expression.args) <= 2 and all(
            keyword.arg == "start" for keyword in expression.keywords
        ):
            values = self._known_iterable(expression.args[0])
            starts = [*expression.args[1:], *(keyword.value for keyword in expression.keywords)]
            start = self._literal_value(starts[0]) if starts else 0
            if values is not None and isinstance(start, int) and len(starts) <= 1:
                pairs = (ast.Tuple([ast.Constant(index), value], ast.Load()) for index, value in enumerate(values, start))
                if not reverse:
                    return pairs
                bounded = list(itertools.islice(pairs, 1_001))
                return reversed(bounded) if len(bounded) <= 1_000 else None
        if name == "builtins.zip" and not expression.keywords:
            groups = [self._known_iterable(argument) for argument in expression.args]
            if all(group is not None for group in groups):
                pairs = (ast.Tuple(list(values), ast.Load()) for values in zip(*groups))
                if not reverse:
                    return pairs
                bounded = list(itertools.islice(pairs, 1_001))
                return reversed(bounded) if len(bounded) <= 1_000 else None
        if name == "builtins.reversed" and len(expression.args) == 1 and not expression.keywords:
            return self._known_iterable(expression.args[0], reverse=not reverse)
        if name in {"builtins.list", "builtins.tuple"} and len(expression.args) == 1 and not expression.keywords:
            return self._known_iterable(expression.args[0], reverse=reverse)
        return None

    def _iteration_bindings(self, target: ast.expr, value: ast.expr) -> dict[str, Binding]:
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) and len(
            target.elts
        ) == len(value.elts):
            return {
                name: binding for child, item in zip(target.elts, value.elts)
                for name, binding in self._iteration_bindings(child, item).items()
            }
        return {target.id: self._value_binding(value)} if isinstance(target, ast.Name) else {}

    def _bind_iteration_target(self, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) and len(
            target.elts
        ) == len(value.elts):
            for child, item in zip(target.elts, value.elts):
                self._bind_iteration_target(child, item)
            return
        binding = self._value_binding(value)
        self._write_target(
            target,
            self.infer_type(value),
            binding if binding.node_id or binding.category == "noninstance" else None,
        )
        self._store_literal_truth(target, self._expression_truth(value))

    @classmethod
    def _block_stops_loop(cls, statements: list[ast.stmt]) -> bool:
        for statement in statements:
            if isinstance(statement, (ast.Break, ast.Return, ast.Raise)):
                return True
            if isinstance(statement, ast.If):
                truth = literal_truth(statement.test)
                if truth is not None and cls._block_stops_loop(statement.body if truth else statement.orelse):
                    return True
                if truth is None and statement.orelse and all(
                    cls._block_stops_loop(branch) for branch in (statement.body, statement.orelse)
                ):
                    return True
        return False

    def _class_member(self, binding: Binding, name: str) -> str | None:
        for _, qualified_name in self._class_mro(binding.node_id, binding.qualified_name):
            if member_id := self.analyzer.nodes_by_qualified.get(f"{qualified_name}.{name}"):
                return member_id
        return None

    def _class_mro(self, class_id: str, qualified_name: str) -> list[tuple[str, str]]:
        def linearize(item: tuple[str, str], active: set[str]) -> list[tuple[str, str]]:
            if item[0] in active:
                return [item]
            parents = [
                (edge["target"], self.graph.nodes[edge["target"]]["qualifiedName"])
                for edge in self.graph.edges.values()
                if edge["kind"] == "inherits" and edge["source"] == item[0]
            ]
            sequences = [linearize(parent, active | {item[0]}) for parent in parents]
            sequences.append(list(parents))
            result = [item]
            while any(sequences):
                sequences = [sequence for sequence in sequences if sequence]
                candidate = next(
                    (sequence[0] for sequence in sequences if all(sequence[0] not in other[1:] for other in sequences)),
                    None,
                )
                if candidate is None:
                    break
                result.append(candidate)
                for sequence in sequences:
                    if sequence and sequence[0] == candidate:
                        sequence.pop(0)
            return result

        return linearize((class_id, qualified_name), set())

    @staticmethod
    def _lazy_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        if isinstance(node, ast.AsyncFunctionDef):
            return True
        pending = list(node.body)
        while pending:
            child = pending.pop()
            if isinstance(child, (ast.Yield, ast.YieldFrom)):
                return True
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            pending.extend(ast.iter_child_nodes(child))
        return False

    def _lambda_arguments(self, function: ast.Lambda, call: ast.Call) -> dict[str, Binding]:
        positional = [*function.args.posonlyargs, *function.args.args]
        result = {
            argument.arg: binding
            for argument, value in zip(positional[-len(function.args.defaults):], function.args.defaults)
            if (binding := self._value_binding(value)).node_id or binding.category == "noninstance"
        } if function.args.defaults else {}
        result.update({
            argument.arg: binding
            for argument, value in zip(function.args.kwonlyargs, function.args.kw_defaults)
            if value is not None and ((binding := self._value_binding(value)).node_id or binding.category == "noninstance")
        })
        result.update({
            argument.arg: binding
            for argument, value in zip(positional, call.args)
            if not isinstance(value, ast.Starred)
            and ((binding := self._value_binding(value)).node_id or binding.category == "noninstance")
        })
        result.update({
            keyword.arg: binding
            for keyword in call.keywords if keyword.arg
            if keyword.arg in {argument.arg for argument in [*positional, *function.args.kwonlyargs]}
            and ((binding := self._value_binding(keyword.value)).node_id or binding.category == "noninstance")
        })
        return result

    def _call_arguments(
        self,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        call: ast.Call,
        bound: bool,
    ) -> dict[str, Binding]:
        positional = [*function.args.posonlyargs, *function.args.args]
        if bound and positional:
            positional = positional[1:]
        parameters = {argument.arg for argument in [*positional, *function.args.kwonlyargs]}
        result = dict(self.function_defaults.get(self.module.symbols[id(function)], {}))
        values: list[ast.expr] = []
        for value in call.args:
            if isinstance(value, ast.Starred) and isinstance(value.value, (ast.Tuple, ast.List)):
                values.extend(value.value.elts)
            else:
                values.append(value)
        result.update({
            parameter.arg: binding
            for parameter, value in zip(positional, values)
            if (binding := self._value_binding(value)).node_id or binding.category == "noninstance"
        })
        keywords = [(keyword.arg, self._value_binding(keyword.value)) for keyword in call.keywords if keyword.arg]
        for keyword in call.keywords:
            if keyword.arg is None and (mapping := self._known_mapping(keyword.value)) is not None:
                keywords.extend(mapping.items())
        result.update({
            name: binding
            for name, binding in keywords
            if name in parameters
            and (binding.node_id or binding.category == "noninstance")
        })
        return result

    def _call_is_valid(
        self,
        function: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
        call: ast.Call,
        bound: bool,
    ) -> bool:
        original = [*function.args.posonlyargs, *function.args.args]
        positional = original[1:] if bound and original else original
        defaulted = {argument.arg for argument in original[-len(function.args.defaults):]} if function.args.defaults else set()
        required = {argument.arg for argument in positional if argument.arg not in defaulted}
        values: list[ast.expr] = []
        unknown_star = False
        for value in call.args:
            if isinstance(value, ast.Starred):
                if isinstance(value.value, (ast.Tuple, ast.List)):
                    values.extend(value.value.elts)
                else:
                    unknown_star = True
            else:
                values.append(value)
        keywords: set[str] = set()
        for keyword in call.keywords:
            if keyword.arg:
                if keyword.arg in keywords:
                    return False
                keywords.add(keyword.arg)
            elif (mapping := self._known_mapping(keyword.value)) is not None:
                mapping_names = set(mapping)
                if keywords & mapping_names:
                    return False
                keywords.update(mapping_names)
        unknown_mapping = any(
            keyword.arg is None and self._known_mapping(keyword.value) is None
            for keyword in call.keywords
        )
        positional_supplied = {argument.arg for argument in positional[:len(values)]}
        if positional_supplied & keywords:
            return False
        supplied = positional_supplied | keywords
        required.update(
            argument.arg
            for argument, default in zip(function.args.kwonlyargs, function.args.kw_defaults)
            if default is None
        )
        if required - supplied and not (unknown_star or unknown_mapping):
            return False
        if len(values) > len(positional) and function.args.vararg is None:
            return False
        allowed_keywords = {argument.arg for argument in [*function.args.args, *function.args.kwonlyargs]}
        if any(name not in allowed_keywords for name in keywords) and function.args.kwarg is None:
            return False
        return True

    def _write_target(self, target: ast.expr, inferred: str | None, binding: Binding | None = None) -> None:
        if isinstance(target, ast.Name):
            owner = self._binding_scope(target.id)
            self.deferred_values.setdefault(owner, {}).pop(target.id, None)
            self.literal_mappings.setdefault(owner, {}).pop(target.id, None)
            self.literal_truths.setdefault(owner, {}).pop(target.id, None)
            variable_id = self._variable(target.id, target, scope=owner)
            self.graph.add_edge("writes", self.scope, variable_id, self.module.relative_path, target)
            if inferred:
                self.variable_types.setdefault(owner, {})[target.id] = inferred
            else:
                self.variable_types.setdefault(owner, {}).pop(target.id, None)
            if binding:
                self.variable_bindings.setdefault(owner, {})[target.id] = binding
            else:
                self.variable_bindings.setdefault(owner, {}).pop(target.id, None)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for child in target.elts:
                self._write_target(child, None)
        elif isinstance(target, ast.Subscript):
            if isinstance(target.value, ast.Name):
                variable_id = self._variable(target.value.id, target.value)
                self.graph.add_edge("writes", self.scope, variable_id, self.module.relative_path, target)
            elif isinstance(target.value, ast.Attribute) and isinstance(target.value.value, ast.Name) and target.value.value.id == "self" and self.class_stack:
                variable_id = self._field(target.value.attr, target.value)
                self.graph.add_edge("writes", self.scope, variable_id, self.module.relative_path, target)
            else:
                self.visit(target.value)
            self.visit(target.slice)
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self" and self.class_stack:
            variable_id = self._field(target.attr, target)
            self.graph.add_edge("writes", self.scope, variable_id, self.module.relative_path, target)
            if inferred:
                self.field_types[self.class_stack[-1]][target.attr] = inferred
            else:
                self.field_types[self.class_stack[-1]].pop(target.attr, None)
        else:
            self.visit(target)

    def _read_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            variable_id = self.lookup_variable(target.id)
            if variable_id:
                self.graph.add_edge("reads", self.scope, variable_id, self.module.relative_path, target)
        else:
            self.visit(target)

    def _variable(self, name: str, at: ast.AST, role: str = "local", scope: str | None = None) -> str:
        owner = scope or self.scope
        variables = self.variables.setdefault(owner, {})
        if name in variables:
            return variables[name]
        owner_name = self.graph.nodes[owner]["qualifiedName"]
        qualified_name = f"{owner_name}.${role}.{name}"
        node_id = self.graph.add_node("variable", name, qualified_name, self.module.relative_path, node_range(at), modifiers=(role,))
        self.graph.add_edge("contains", owner, node_id, self.module.relative_path, at)
        variables[name] = node_id
        return node_id

    def _binding_scope(self, name: str) -> str:
        if name in self.global_names.get(self.scope, set()):
            return self.module.node_id
        if name in self.nonlocal_names.get(self.scope, set()):
            for owner in reversed(self.scope_stack[:-1]):
                if self.graph.nodes[owner]["kind"] != "class" and name in self.variables.get(owner, {}):
                    return owner
        return self.scope

    def _field(self, name: str, at: ast.AST) -> str:
        class_name = self.class_stack[-1]
        qualified_name = f"{class_name}.{name}"
        existing = self.analyzer.nodes_by_qualified.get(qualified_name)
        if existing:
            return existing
        node_id = self.graph.add_node("variable", name, qualified_name, self.module.relative_path, node_range(at), modifiers=("field",))
        class_id = self.analyzer.nodes_by_qualified[class_name]
        self.graph.add_edge("contains", class_id, node_id, self.module.relative_path, at)
        self.analyzer.nodes_by_qualified[qualified_name] = node_id
        return node_id

    def lookup_variable(self, name: str) -> str | None:
        if self._is_shadowed(name):
            return self.variables.get(self.scope, {}).get(name)
        for scope in self._lexical_scopes():
            if name in self.variables.get(scope, {}):
                return self.variables[scope][name]
            if name in self.declared_names.get(scope, set()):
                return None
        return None

    def lookup_variable_binding(self, name: str) -> Binding | None:
        if self._is_shadowed(name):
            return None
        for scope in self._lexical_scopes():
            if name in self.variables.get(scope, {}):
                return self.variable_bindings.get(scope, {}).get(name)
            if name in self.declared_names.get(scope, set()):
                return self.variable_bindings.get(scope, {}).get(name)
        return None

    def _lexical_scopes(self) -> Iterable[str]:
        for scope in reversed(self.scope_stack):
            if scope != self.scope and self.graph.nodes[scope]["kind"] == "class":
                continue
            yield scope

    def resolve_annotation(self, annotation: ast.expr | None) -> str | None:
        if annotation is None:
            return None
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            binding = self.resolve_name(annotation.value, unresolved=False)
            if binding.category == "class":
                self.graph.add_edge("type_uses", self.scope, binding.node_id, self.module.relative_path, annotation)
                return binding.qualified_name
            return None
        if isinstance(annotation, ast.Subscript):
            values = [self.resolve_annotation(annotation.value), self.resolve_annotation(annotation.slice)]
            return next((value for value in values if value), None)
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            values = [self.resolve_annotation(annotation.left), self.resolve_annotation(annotation.right)]
            return next((value for value in values if value), None)
        if isinstance(annotation, (ast.Tuple, ast.List)):
            values = [self.resolve_annotation(item) for item in annotation.elts]
            return next((value for value in values if value), None)
        binding = self.resolve_expression(annotation, unresolved=False)
        if binding.category == "class":
            self.graph.add_edge("type_uses", self.scope, binding.node_id, self.module.relative_path, annotation)
            return binding.qualified_name
        return None

    def infer_type(self, value: ast.expr | None) -> str | None:
        if not isinstance(value, ast.Call):
            return None
        binding = self.resolve_expression(value, unresolved=False)
        return binding.qualified_name if binding.category == "instance" else None

    def resolve_name(self, name: str, *, unresolved: bool = True, at: ast.AST | None = None) -> Binding:
        for bindings in reversed(self.expression_bindings):
            if name in bindings:
                return bindings[name]
        if self._is_shadowed(name):
            if unresolved and at is not None:
                return self.graph.add_unresolved(self.module, name, at, "nested scope target cannot be resolved statically")
            return Binding("", name, "unresolved")
        variable_type = self.lookup_variable_type(name)
        if variable_type:
            node_id = self.analyzer.nodes_by_qualified.get(variable_type)
            if node_id:
                return Binding(node_id, variable_type, "instance")
        variable_id = self.lookup_variable(name)
        if variable_id:
            binding = self.lookup_variable_binding(name)
            if binding:
                return binding
            if unresolved and at is not None:
                return self.graph.add_unresolved(self.module, name, at, "runtime variable target cannot be resolved statically")
            return Binding("", name, "unresolved")
        for scope in self._lexical_scopes():
            if name in self.declared_names.get(scope, set()):
                if name in self.module.ambiguous_names.get(scope, set()):
                    reason = "multiple definitions cannot be resolved statically"
                else:
                    binding = self.variable_bindings.get(scope, {}).get(name) or self.module.aliases.get(scope, {}).get(name)
                    if binding:
                        return binding
                    reason = "name is not bound on every reachable path"
                if unresolved and at is not None:
                    return self.graph.add_unresolved(self.module, name, at, reason)
                return Binding("", name, "unresolved")
            if name in self.module.ambiguous_names.get(scope, set()):
                if unresolved and at is not None:
                    return self.graph.add_unresolved(self.module, name, at, "multiple definitions cannot be resolved statically")
                return Binding("", name, "unresolved")
            node_id = self.module.scopes.get(scope, {}).get(name)
            if node_id:
                node = self.graph.nodes[node_id]
                return Binding(node_id, node["qualifiedName"], "class" if node["kind"] == "class" else "symbol")
            alias = self.module.aliases.get(scope, {}).get(name)
            if alias:
                return alias
        module_node = self.analyzer.nodes_by_qualified.get(f"{self.module.name}.{name}")
        if module_node:
            node = self.graph.nodes[module_node]
            return Binding(module_node, node["qualifiedName"], "class" if node["kind"] == "class" else "symbol")
        if name in BUILTINS:
            return self.graph.add_external(f"builtins.{name}")
        if unresolved and at is not None:
            return self.graph.add_unresolved(self.module, name, at, "name cannot be resolved statically")
        return Binding("", name, "unresolved")

    def lookup_variable_type(self, name: str) -> str | None:
        if self._is_shadowed(name):
            return None
        for scope in self._lexical_scopes():
            if name in self.variable_types.get(scope, {}):
                return self.variable_types[scope][name]
            if name in self.declared_names.get(scope, set()):
                return None
        return None

    def resolve_expression(self, expression: ast.expr, *, unresolved: bool = True) -> Binding:
        if isinstance(expression, ast.Name):
            return self.resolve_name(expression.id, unresolved=unresolved, at=expression)
        selected = self._selected_expression(expression)
        if selected is not expression:
            return self._value_binding(selected)
        if isinstance(expression, ast.Call):
            if id(expression) in self.call_results:
                return self.call_results[id(expression)]
            target = self.call_bindings.get(id(expression)) or self.resolve_expression(expression.func, unresolved=False)
            if target.category == "class":
                return Binding(target.node_id, target.qualified_name, "instance")
            if target.qualified_name == "builtins.object":
                return Binding(target.node_id, target.qualified_name, "noninstance")
            context = self.function_contexts.get(target.node_id)
            if context and self._lazy_function(context[0]):
                return Binding(target.node_id, target.qualified_name, "lazy")
        if isinstance(expression, ast.Attribute):
            if isinstance(expression.value, ast.Name) and expression.value.id == "self" and self.class_stack:
                class_name = self.class_stack[-1]
                field_type = self.field_types.get(class_name, {}).get(expression.attr)
                if field_type:
                    node_id = self.analyzer.nodes_by_qualified[field_type]
                    return Binding(node_id, field_type, "instance")
                method_id = self.analyzer.nodes_by_qualified.get(f"{class_name}.{expression.attr}")
                if method_id and self.graph.nodes[method_id]["kind"] != "variable":
                    if method_id in self.decorated_definitions:
                        if unresolved:
                            return self.graph.add_unresolved(
                                self.module,
                                ast.unparse(expression),
                                expression,
                                "decorator result cannot be resolved statically",
                            )
                        return Binding("", f"{class_name}.{expression.attr}", "unresolved")
                    return Binding(method_id, f"{class_name}.{expression.attr}", "symbol")
            base = self.resolve_expression(expression.value, unresolved=False)
            if base.node_id:
                target_name = f"{base.qualified_name}.{expression.attr}"
                if base.category == "module":
                    return self.analyzer.resolve_absolute(target_name, self.module, expression)
                if base.category == "instance" and expression.attr != "__getattribute__" and self._class_member(
                    base, "__getattribute__"
                ):
                    if unresolved:
                        return self.graph.add_unresolved(
                            self.module,
                            ast.unparse(expression),
                            expression,
                            "custom __getattribute__ controls attribute dispatch",
                        )
                    return Binding("", target_name, "unresolved")
                target_id = self.analyzer.nodes_by_qualified.get(target_name)
                if not target_id and base.category in {"class", "instance"}:
                    target_id = self._class_member(base, expression.attr)
                    if target_id:
                        target_name = self.graph.nodes[target_id]["qualifiedName"]
                if target_id:
                    if target_id in self.decorated_definitions:
                        if unresolved:
                            return self.graph.add_unresolved(
                                self.module,
                                ast.unparse(expression),
                                expression,
                                "decorator result cannot be resolved statically",
                            )
                        return Binding("", target_name, "unresolved")
                    node = self.graph.nodes[target_id]
                    return Binding(target_id, target_name, "class" if node["kind"] == "class" else "symbol")
                if base.category == "external":
                    return self.graph.add_external(target_name)
            if unresolved:
                return self.graph.add_unresolved(self.module, ast.unparse(expression), expression, "attribute receiver cannot be resolved statically")
        if unresolved:
            return self.graph.add_unresolved(self.module, ast.unparse(expression), expression, "call target cannot be resolved statically")
        return Binding("", ast.unparse(expression), "unresolved")


def analyze_repository(
    repository: str | Path,
    *,
    config_path: str | Path | None = None,
    include_tests: bool | None = None,
) -> dict[str, Any]:
    root = Path(repository).resolve(strict=True)
    if not root.is_dir():
        raise AnalysisError("repository must be a directory")
    config = load_config(root, Path(config_path).resolve() if config_path else None)
    if include_tests is not None:
        config = replace(config, include_tests=include_tests)
    files = discover_python_files(root, config.include_tests, config.exclude)
    entry = resolve_entry(root, files, config)
    return Analyzer(root, config).analyze(files, entry)


def write_ndjson(graph: dict[str, Any], stream: Any) -> None:
    stream.write(json.dumps({"record": "meta", "schemaVersion": graph["schemaVersion"], "project": graph["project"]}, sort_keys=True) + "\n")
    for node in graph["nodes"]:
        stream.write(json.dumps({"record": "node", "node": node}, sort_keys=True) + "\n")
    for edge in graph["edges"]:
        stream.write(json.dumps({"record": "edge", "edge": edge}, sort_keys=True) + "\n")
    for diagnostic in graph["diagnostics"]:
        stream.write(json.dumps({"record": "diagnostic", "diagnostic": diagnostic}, sort_keys=True) + "\n")
    stream.write(json.dumps({"record": "summary", "nodeCount": len(graph["nodes"]), "edgeCount": len(graph["edges"]), "diagnosticCount": len(graph["diagnostics"])}, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository")
    parser.add_argument("--config")
    parser.add_argument("--format", choices=("json", "ndjson"), default="json")
    parser.add_argument("--include-tests", action="store_true", default=None)
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        graph = analyze_repository(args.repository, config_path=args.config, include_tests=args.include_tests)
    except (AnalysisError, OSError) as error:
        print(f"python-analyzer: {error}", file=sys.stderr)
        return 2
    if args.format == "json":
        json.dump(graph, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        write_ndjson(graph, sys.stdout)
    if args.trace:
        elapsed_ms = (time.perf_counter() - started) * 1000
        trace = {
            "event": "python-analyzer.complete",
            "elapsedMs": round(elapsed_ms, 3),
            "files": sum(1 for node in graph["nodes"] if node["kind"] == "module"),
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "diagnostics": len(graph["diagnostics"]),
        }
        print(json.dumps(trace, sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
