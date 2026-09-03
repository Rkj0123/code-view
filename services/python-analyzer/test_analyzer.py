import collections
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest


SERVICE = Path(__file__).resolve().parent
ROOT = SERVICE.parents[1]
FIXTURE = ROOT / "tests/fixtures/tic-tac-toe"
sys.path.insert(0, str(SERVICE))

from analyzer import (  # noqa: E402
    AnalysisError,
    CANONICAL_EDGE_KINDS,
    LEGACY_EDGE_MAP,
    analyze_repository,
    load_config,
    parse_start_command,
    validate_graph_document,
    write_ndjson,
)


def canonical_hash(graph):
    value = json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(value).hexdigest()


def relationship_set(graph, kind):
    names = {node["id"]: node["qualifiedName"] for node in graph["nodes"]}
    return {f"{names[edge['source']]} -> {names[edge['target']]}" for edge in graph["edges"] if edge["kind"] == kind}


def analyze_files(files):
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return temporary, root, analyze_repository(root)


class GoldenFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = analyze_repository(FIXTURE)
        cls.expected = json.loads((FIXTURE / "expected-graph.json").read_text())

    def test_exact_golden_graph(self):
        self.assertEqual(self.expected["canonicalSha256"], canonical_hash(self.graph))
        self.assertEqual(self.expected["schemaVersion"], self.graph["schemaVersion"])
        self.assertEqual(self.expected["counts"]["nodes"], dict(sorted(collections.Counter(node["kind"] for node in self.graph["nodes"]).items())))
        self.assertEqual(self.expected["counts"]["edges"], dict(sorted(collections.Counter(edge["kind"] for edge in self.graph["edges"]).items())))

    def test_semantic_reference(self):
        evaluated_kinds = {item.split("|", 1)[0] for item in self.expected["nodes"]}
        actual_nodes = {f"{node['kind']}|{node['qualifiedName']}" for node in self.graph["nodes"] if node["kind"] in evaluated_kinds}
        self.assertEqual(set(self.expected["nodes"]), actual_nodes)
        self.assertEqual(set(self.expected["calls"]), relationship_set(self.graph, "calls"))
        self.assertEqual(set(self.expected["imports"]), relationship_set(self.graph, "imports"))
        self.assertEqual(set(self.expected["constructs"]), relationship_set(self.graph, "constructs"))
        self.assertEqual(set(self.expected["typeUses"]), relationship_set(self.graph, "type_uses"))

    def test_files_modules_ranges_and_entry_focus(self):
        nodes = {(node["kind"], node["qualifiedName"]): node for node in self.graph["nodes"]}
        self.assertIn(("file", "file:tictactoe/game.py"), nodes)
        self.assertIn(("module", "tictactoe.game"), nodes)
        run = nodes[("function", "tictactoe.cli.run")]
        self.assertEqual("def run():", run["signature"])
        self.assertEqual("Play until the board is full or won.", run["docstring"])
        self.assertEqual({"line": 7, "column": 1}, run["range"]["start"])
        reachable = set(self.graph["project"]["entryReachableNodeIds"])
        self.assertIn(nodes[("method", "tictactoe.board.Board.has_winner")]["id"], reachable)
        self.assertNotIn(nodes[("function", "tictactoe.unused.debug_board")]["id"], reachable)

    def test_external_and_unresolved_boundaries_have_reasons(self):
        boundaries = [node for node in self.graph["nodes"] if node["external"] or node["unresolved"]]
        self.assertTrue(boundaries)
        self.assertTrue(all(node["reason"] for node in boundaries))
        unresolved = [node for node in boundaries if node["unresolved"]]
        self.assertEqual(self.expected["unresolved"][0]["qualifiedName"], unresolved[0]["qualifiedName"])
        self.assertEqual(self.expected["unresolved"][0]["reason"], unresolved[0]["reason"])

    def test_tests_are_excluded_by_default_and_configurable(self):
        default_paths = {node["path"] for node in self.graph["nodes"]}
        self.assertNotIn("tests/test_game.py", default_paths)
        included = analyze_repository(FIXTURE, include_tests=True)
        included_paths = {node["path"] for node in included["nodes"]}
        self.assertIn("tests/test_game.py", included_paths)
        self.assertIn(
            "tests.test_game.test_new_game_is_not_over -> tictactoe.game.Game.is_over",
            relationship_set(included, "test_covers"),
        )

    def test_output_is_stable_and_ndjson_is_ordered(self):
        self.assertEqual(self.graph, analyze_repository(FIXTURE))
        first = io.StringIO()
        second = io.StringIO()
        write_ndjson(self.graph, first)
        write_ndjson(self.graph, second)
        self.assertEqual(first.getvalue(), second.getvalue())
        records = [json.loads(line) for line in first.getvalue().splitlines()]
        self.assertEqual("meta", records[0]["record"])
        self.assertEqual("summary", records[-1]["record"])


class AnalyzerBehaviorTest(unittest.TestCase):
    def test_shadowing_stops_at_an_unresolved_boundary(self):
        temporary, _, graph = analyze_files({
            "main.py": (
                "def target():\n    pass\n\n"
                "def parameter(target):\n    target()\n\n"
                "class C:\n    def helper(self):\n        pass\n"
                "    def run(self):\n        helper()\n"
            )
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertNotIn("main.parameter -> main.target", calls)
            self.assertNotIn("main.C.run -> main.C.helper", calls)
            self.assertEqual(2, len([node for node in graph["nodes"] if node["unresolved"]]))

    def test_exact_internal_imports_aliases_and_reexports(self):
        temporary, root, graph = analyze_files({
            "main.py": "import pkg.mod\nimport pkg\nfrom pkg.mod import missing\npkg.f()\n",
            "pkg/__init__.py": "from .mod import f\n",
            "pkg/mod.py": "def f():\n    pass\n",
        })
        with temporary:
            imports = relationship_set(graph, "imports")
            self.assertIn("main -> pkg.mod", imports)
            self.assertIn("main -> main::<unresolved>@3:21:pkg.mod.missing", imports)
            self.assertIn("main -> pkg.mod.f", relationship_set(graph, "calls"))
            completed = subprocess.run(
                [sys.executable, str(SERVICE / "analyzer.py"), str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_resolvable_python_constructs_and_occurrences(self):
        temporary, _, graph = analyze_files({
            "main.py": (
                "import os as first, os as second\n"
                "class A:\n    pass\nclass B:\n    pass\n"
                "def dependency():\n    return 1\n"
                "def use(x: A | None, y: tuple[A, B] = dependency()) -> list[B]:\n    pass\n"
                "def duplicate():\n    return 1\ndef duplicate():\n    return 2\n"
                "alias = dependency\nalias()\n"
            ),
            "conftest.py": "def pytest_configure():\n    pass\n",
        })
        with temporary:
            self.assertIn("main -> main.dependency", relationship_set(graph, "calls"))
            self.assertNotIn("conftest.py", {node["path"] for node in graph["nodes"]})
            self.assertEqual(2, len([edge for edge in graph["edges"] if edge["kind"] == "imports"]))
            self.assertEqual(2, len([node for node in graph["nodes"] if node["qualifiedName"] == "main.duplicate"]))
            type_uses = relationship_set(graph, "type_uses")
            self.assertIn("main.use -> main.A", type_uses)
            self.assertIn("main.use -> main.B", type_uses)

    def test_parallel_analysis_is_byte_identical(self):
        source = "\n".join(f"def f{i}():\n    import json\n    return json.dumps({i})" for i in range(40))
        temporary, root, expected = analyze_files({"main.py": source})
        with temporary:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                hashes = list(pool.map(lambda _: canonical_hash(analyze_repository(root)), range(16)))
            self.assertEqual({canonical_hash(expected)}, set(hashes))

    def test_aliases_are_conservative_at_control_flow_joins(self):
        temporary, _, graph = analyze_files({
            "main.py": (
                "def one():\n    pass\ndef two():\n    pass\n"
                "alias = one\nif False:\n    alias = two\nalias()\n"
                "if condition:\n    branch = one\nbranch()\n"
            )
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertIn("main -> main.one", calls)
            self.assertNotIn("main -> main.two", calls)
            self.assertTrue(any(node["reason"] == "runtime variable target cannot be resolved statically" for node in graph["nodes"] if node["unresolved"]))
        cases = (
            "if condition:\n    alias = one\nelse:\n    alias = two\n",
            "for item in []:\n    alias = one\n",
        )
        for control_flow in cases:
            temporary, _, graph = analyze_files({
                "main.py": "def one():\n    pass\ndef two():\n    pass\n" + control_flow + "alias()\n"
            })
            with temporary:
                targets = {
                    node["kind"]
                    for edge in graph["edges"] if edge["kind"] == "calls"
                    for node in graph["nodes"] if node["id"] == edge["target"]
                }
                self.assertEqual({"unresolved"}, targets)
        temporary, _, graph = analyze_files({
            "main.py": "def one():\n    pass\ndef two():\n    pass\ntry:\n    alias = one\nexcept Exception:\n    alias = two\nalias()\n"
        })
        with temporary:
            self.assertIn("main -> main.one", relationship_set(graph, "calls"))

    def test_unreachable_imports_and_uncertain_fields_do_not_invent_calls(self):
        for control_flow in (
            "if False:\n    from b import target\n",
            "for item in []:\n    from b import target\n",
        ):
            temporary, _, graph = analyze_files({
                "main.py": "from a import target\n" + control_flow + "target()\n",
                "a.py": "def target():\n    pass\n",
                "b.py": "def target():\n    pass\n",
            })
            with temporary:
                calls = relationship_set(graph, "calls")
                self.assertIn("main -> a.target", calls)
                self.assertNotIn("main -> b.target", calls)

        temporary, _, graph = analyze_files({
            "main.py": (
                "class Old:\n    def ping(self):\n        pass\n"
                "class One:\n    pass\nclass Two:\n    pass\nclass Use:\n"
                "    def run(self, flag):\n        self.value = Old()\n"
                "        if flag:\n            self.value = One()\n"
                "        else:\n            self.value = Two()\n"
                "        self.value.ping()\n"
            )
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertNotIn("main.Use.run -> main.Old.ping", calls)
            self.assertTrue(any(
                node["reason"] == "attribute receiver cannot be resolved statically"
                for node in graph["nodes"] if node["unresolved"]
            ))

    def test_function_wide_bindings_follow_python_lexical_scope(self):
        temporary, _, graph = analyze_files({
            "main.py": (
                "from a import target\n"
                "def before_import():\n    target()\n    from b import target\n"
                "def after_import():\n    from b import target\n    target()\n"
                "def before_definition():\n    target()\n    def target():\n        pass\n"
                "def after_definition():\n    def target():\n        pass\n    target()\n"
                "def before_class():\n    target()\n    class target:\n        pass\n"
                "def after_class():\n    class target:\n        pass\n    target()\n"
                "def before_exception():\n    target()\n    try:\n        pass\n    except Exception as target:\n        pass\n"
                "def global_call():\n    global target\n    target()\n"
                "def outer():\n    def target():\n        pass\n    def nonlocal_call():\n        nonlocal target\n        target()\n"
            ),
            "a.py": "def target():\n    pass\n",
            "b.py": "def target():\n    pass\n",
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertIn("main.after_import -> b.target", calls)
            self.assertIn("main.after_definition -> main.after_definition.target", calls)
            self.assertIn("main.global_call -> a.target", calls)
            self.assertIn("main.outer.nonlocal_call -> main.outer.target", calls)
            self.assertNotIn("main.before_import -> a.target", calls)
            self.assertNotIn("main.before_definition -> main.before_definition.target", calls)
            self.assertNotIn("main.before_exception -> a.target", calls)
            constructs = relationship_set(graph, "constructs")
            self.assertIn("main.after_class -> main.after_class.target", constructs)
            self.assertNotIn("main.before_class -> main.before_class.target", constructs)
            unresolved_sources = {
                source for source, target in (relation.split(" -> ", 1) for relation in calls)
                if "::<unresolved>@" in target
            }
            self.assertTrue({"main.before_import", "main.before_definition", "main.before_class", "main.before_exception"} <= unresolved_sources)

    def test_nested_scope_bindings_never_reuse_outer_aliases(self):
        temporary, _, graph = analyze_files({
            "main.py": (
                "from target import call as alias\n"
                "def run(values, value):\n"
                "    (lambda alias: alias())(value)\n"
                "    [alias() for alias in values]\n"
                "    try:\n        raise ValueError()\n"
                "    except Exception as alias:\n        alias()\n"
                "    match value:\n"
                "        case alias:\n            alias()\n"
            ),
            "target.py": "def call():\n    pass\n",
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertNotIn("main.run -> target.call", calls)
            unresolved_aliases = [
                relation for relation in calls
                if relation.startswith("main.run -> main::<unresolved>") and relation.endswith(":alias")
            ]
            self.assertEqual(4, len(unresolved_aliases))

    def test_deleted_and_unreachable_bindings_are_unresolved(self):
        temporary, _, graph = analyze_files({
            "main.py": (
                "from target import call as alias\n"
                "del alias\n"
                "alias()\n"
                "if False:\n"
                "    def hidden():\n        pass\n"
                "hidden()\n"
            ),
            "target.py": "def call():\n    pass\n",
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertNotIn("main -> target.call", calls)
            self.assertFalse(any(relation.endswith(" -> main.hidden") for relation in calls))
            self.assertEqual(2, len([relation for relation in calls if "::<unresolved>@" in relation]))

    def test_module_package_name_collision_preserves_both_files(self):
        temporary, _, graph = analyze_files({
            "conflict.py": "def file_symbol():\n    pass\n",
            "conflict/__init__.py": "def package_symbol():\n    pass\n",
            "main.py": "from conflict import file_symbol, package_symbol\nfile_symbol()\npackage_symbol()\n",
        })
        with temporary:
            functions = {
                (node["qualifiedName"], node["path"])
                for node in graph["nodes"] if node["kind"] == "function"
            }
            self.assertIn(("conflict.file_symbol", "conflict.py"), functions)
            self.assertIn(("conflict.package_symbol", "conflict/__init__.py"), functions)
            calls = relationship_set(graph, "calls")
            self.assertNotIn("main -> conflict.file_symbol", calls)
            self.assertNotIn("main -> conflict.package_symbol", calls)
            self.assertEqual(2, len([relation for relation in calls if "::<unresolved>@" in relation]))

    def test_late_and_dynamic_bindings_do_not_invent_targets(self):
        temporary, _, graph = analyze_files({
            "main.py": (
                "def one():\n    pass\ndef two():\n    pass\n"
                "alias = one\ndef run():\n    alias()\nrun()\nalias = two\nrun()\n"
                "def outer():\n    nested = one\n    def inner():\n        nested()\n"
                "    inner()\n    nested = two\n    inner()\nouter()\n"
            )
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertIn("main.run -> main.two", calls)
            self.assertIn("main.outer.inner -> main.two", calls)
            self.assertIn("main.run -> main.one", calls)
            self.assertIn("main.outer.inner -> main.one", calls)

        temporary, _, graph = analyze_files({
            "main.py": (
                "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
                "def ignored():\n    global alias\n    alias = two\n"
                "def read():\n    alias()\nread()\n"
            )
        })
        with temporary:
            self.assertIn("main.read -> main.one", relationship_set(graph, "calls"))

        temporary, _, graph = analyze_files({
            "main.py": (
                "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
                "def read():\n    alias()\n"
                "def mutate():\n    global alias\n    alias = two\n"
                "mutate()\nread()\n"
            )
        })
        with temporary:
            self.assertIn("main.read -> main.two", relationship_set(graph, "calls"))

        temporary, _, graph = analyze_files({
            "main.py": (
                "def one():\n    pass\ndef two():\n    pass\n"
                "dynamic = one\nexec('dynamic = two')\ndynamic()\n"
                "other = one\nglobals()['other'] = two\nother()\n"
            )
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertNotIn("main -> main.one", calls)
            self.assertEqual(2, len([relation for relation in calls if "::<unresolved>@" in relation]))

        for mutation in (
            "def mutate():\n    exec('alias = two', globals())\nmutate()\n",
            "globals().update(alias=two)\n",
        ):
            temporary, _, graph = analyze_files({
                "main.py": "def one():\n    pass\ndef two():\n    pass\nalias = one\n" + mutation + "alias()\n"
            })
            with temporary:
                calls = relationship_set(graph, "calls")
                self.assertNotIn("main -> main.one", calls)
                self.assertTrue(any(relation.startswith("main -> main::<unresolved>@") and relation.endswith(":alias") for relation in calls))

    def test_lazy_and_unreachable_code_does_not_apply_runtime_effects(self):
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        sources = (
            "def mutate():\n    global alias\n    alias = two\n    yield\niterator = mutate()\niterator.close()\n",
            "async def mutate():\n    global alias\n    alias = two\ncoroutine = mutate()\ncoroutine.close()\n",
            "def mutate():\n    global alias\n    return\n    alias = two\nmutate()\n",
            "def mutate():\n    global alias\n    alias = two\nFalse and mutate()\n",
            "def mutate():\n    global alias\n    alias = two\nNone if True else mutate()\n",
            "def mutate():\n    global alias\n    alias = two\n[mutate() for unused in []]\n",
            "def mutate():\n    global alias\n    alias = two\ncallback = lambda: mutate()\n",
            "def mutate():\n    global alias\n    alias = two\nunused = (mutate() for item in [0])\n",
            "def mutate():\n    global alias\n    alias = two\n[mutate() for item in [0] if False]\n",
            "def mutate():\n    global alias\n    alias = two\n() and mutate()\n",
        )
        for source in sources:
            temporary, _, graph = analyze_files({"main.py": prefix + source + "alias()\n"})
            with temporary:
                calls = relationship_set(graph, "calls")
                self.assertIn("main -> main.one", calls)
                self.assertNotIn("main -> main.two", calls)

        for body in (
            "if flag:\n        return\n        target()\n",
            "try:\n        return\n    except Exception:\n        target()\n",
            "try:\n        raise ValueError\n    except ValueError:\n        pass\n    else:\n        target()\n",
        ):
            temporary, _, graph = analyze_files({
                "main.py": "def target():\n    pass\ndef run(flag=True):\n    " + body + "run()\n"
            })
            with temporary:
                self.assertNotIn("main.run -> main.target", relationship_set(graph, "calls"))

    def test_known_effects_merge_at_uncertain_and_apply_at_definite_control_flow(self):
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        temporary, _, graph = analyze_files({
            "main.py": "import os\n" + prefix
            + "def mutate():\n    global alias\n    alias = two\n"
            + "if os.environ.get('MUTATE'):\n    mutate()\nalias()\n"
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertNotIn("main -> main.one", calls)
            self.assertNotIn("main -> main.two", calls)
            self.assertTrue(any(relation.endswith(":alias") for relation in calls))

        for control in ("try:\n    mutate()\nexcept Exception:\n    pass\n", "for unused in [0]:\n    mutate()\n"):
            temporary, _, graph = analyze_files({
                "main.py": prefix + "def mutate():\n    global alias\n    alias = two\n" + control + "alias()\n"
            })
            with temporary:
                calls = relationship_set(graph, "calls")
                self.assertIn("main -> main.two", calls)
                self.assertNotIn("main -> main.one", calls)

        for body in ("while True:\n        return\n", "try:\n        return\n    finally:\n        pass\n"):
            temporary, _, graph = analyze_files({
                "main.py": prefix + "def mutate():\n    global alias\n    " + body + "    alias = two\nmutate()\nalias()\n"
            })
            with temporary:
                calls = relationship_set(graph, "calls")
                self.assertIn("main -> main.one", calls)
                self.assertNotIn("main -> main.two", calls)

    def test_await_and_iteration_apply_lazy_function_effects(self):
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        temporary, _, graph = analyze_files({
            "main.py": "import asyncio\n" + prefix
            + "async def mutate():\n    global alias\n    alias = two\n"
            + "async def read():\n    await mutate()\n    alias()\nasyncio.run(read())\n"
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertIn("main.read -> main.two", calls)
            self.assertNotIn("main.read -> main.one", calls)

        temporary, _, graph = analyze_files({
            "main.py": prefix
            + "def mutate():\n    global alias\n    alias = two\n    yield None\n"
            + "for unused in mutate():\n    pass\nalias()\n"
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertIn("main -> main.two", calls)
            self.assertNotIn("main -> main.one", calls)

        for source in (
            "import asyncio\n" + prefix
            + "async def mutate():\n    global alias\n    alias = two\n"
            + "async def read():\n    pending = mutate()\n    await pending\n    alias()\nasyncio.run(read())\n",
            prefix + "def mutate():\n    global alias\n    alias = two\n    yield None\n"
            + "iterator = mutate()\nfor unused in iterator:\n    pass\nalias()\n",
            prefix + "def mutate():\n    global alias\n    alias = two\n    yield None\n"
            + "def outer():\n    yield from mutate()\nfor unused in outer():\n    pass\nalias()\n",
        ):
            temporary, _, graph = analyze_files({"main.py": source})
            with temporary:
                calls = relationship_set(graph, "calls")
                self.assertTrue("main -> main.two" in calls or "main.read -> main.two" in calls)

    def test_dynamic_namespace_calls_follow_resolved_builtin_identity(self):
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        for mutation in (
            "namespace = globals\nnamespace()['alias'] = two\n",
            "import builtins\nbuiltins.globals()['alias'] = two\n",
            "runner = exec\nrunner('alias = two')\n",
            "import builtins\nbuiltins.exec('alias = two')\n",
        ):
            temporary, _, graph = analyze_files({"main.py": prefix + mutation + "alias()\n"})
            with temporary:
                calls = relationship_set(graph, "calls")
                self.assertNotIn("main -> main.one", calls)
                self.assertNotIn("main -> main.two", calls)
                self.assertTrue(any(relation.endswith(":alias") for relation in calls if "::<unresolved>@" in relation))

        temporary, _, graph = analyze_files({
            "main.py": "def one():\n    pass\nalias = one\ndef globals():\n    return {}\nglobals()\nalias()\n"
        })
        with temporary:
            self.assertIn("main -> main.one", relationship_set(graph, "calls"))

    def test_known_calls_apply_constructor_and_argument_bindings(self):
        temporary, _, graph = analyze_files({
            "main.py": (
                "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
                "def invoke(function):\n    function()\n"
                "def set_alias(value):\n    global alias\n    alias = value\n"
                "class Mutator:\n    def __init__(self, value):\n        set_alias(value)\n"
                "invoke(one)\nMutator(two)\nalias()\n"
            )
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertIn("main.invoke -> main.one", calls)
            self.assertIn("main.Mutator.__init__ -> main.set_alias", calls)
            self.assertIn("main -> main.two", calls)
            self.assertNotIn("main -> main.one", calls)

        temporary, _, graph = analyze_files({
            "main.py": (
                "def replacement():\n    pass\ndef decorator(function):\n    return replacement\n"
                "@decorator\ndef decorated():\n    pass\ndecorated()\n"
            )
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertNotIn("main -> main.decorated", calls)
            self.assertIn("main -> main.replacement", calls)

    def test_definition_defaults_inheritance_and_temporary_receivers_apply_known_effects(self):
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        temporary, _, graph = analyze_files({
            "main.py": prefix
            + "def decorator(function):\n    global alias\n    alias = two\n    return function\n"
            + "@decorator\ndef decorated():\n    pass\nalias()\n"
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertIn("main -> main.two", calls)
            self.assertNotIn("main -> main.one", calls)

    def test_deferred_execution_and_python_dispatch_boundaries(self):
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        cases = (
            ("def mutate():\n    global alias\n    alias = two\ncallback = lambda: mutate()\ncallback()\n", "main -> main.two", "main -> main.one"),
            ("def mutate():\n    global alias\n    alias = two\nitems = (mutate() for unused in [0])\nfor unused in items:\n    pass\n", "main -> main.two", "main -> main.one"),
            ("def mutate(value=one):\n    global alias\n    alias = value\n    yield None\niterator = mutate(two)\nfor unused in iterator:\n    pass\n", "main -> main.two", "main -> main.one"),
            ("import asyncio\nasync def mutate():\n    global alias\n    alias = two\npending = mutate()\nasyncio.run(pending)\n", "main -> main.two", "main -> main.one"),
            ("try:\n    pass\nexcept ValueError:\n    alias = two\n", "main -> main.one", "main -> main.two"),
            ("def factory():\n    def decorator(function):\n        global alias\n        alias = two\n        return function\n    return decorator\n@factory()\ndef decorated():\n    pass\n", "main -> main.two", "main -> main.one"),
            ("def mutate(required):\n    global alias\n    alias = two\ntry:\n    mutate()\nexcept TypeError:\n    pass\n", "main -> main.one", "main -> main.two"),
            ("class C:\n    @staticmethod\n    def mutate():\n        global alias\n        alias = two\nC.mutate()\n", "main -> main.two", "main -> main.one"),
            ("class C:\n    def __new__(cls):\n        raise RuntimeError\n    def __init__(self):\n        global alias\n        alias = two\ntry:\n    C()\nexcept RuntimeError:\n    pass\n", "main -> main.one", "main -> main.two"),
            ("def mutate():\n    global alias\n    alias = two\nunused = [mutate() for first in [0] for second in []]\n", "main -> main.one", "main -> main.two"),
            ("def mutate():\n    global alias\n    yield None\n    alias = two\nfor unused in mutate():\n    break\n", "main -> main.one", "main -> main.two"),
            ("def mutate():\n    global alias\n    alias = two\n    yield None\nlist(mutate())\n", "main -> main.two", "main -> main.one"),
            (
                "class Root:\n    def __init__(self):\n        global alias\n        alias = one\n"
                "class Left(Root):\n    pass\nclass Right:\n    def __init__(self):\n        global alias\n        alias = two\n"
                "class Child(Left, Right):\n    pass\nChild()\n",
                "main -> main.Root.__init__",
                "main -> main.Right.__init__",
            ),
            ("def set_alias(value):\n    global alias\n    alias = value\n(lambda: set_alias(two))()\n", "main -> main.two", "main -> main.one"),
            ("def safe():\n    return None\ndef target():\n    global alias\n    alias = two\ntry:\n    safe()\nexcept Exception:\n    target()\n", "main -> main.one", "main -> main.target"),
            ("class C:\n    @classmethod\n    def mutate(cls, value=one):\n        global alias\n        alias = value\nC.mutate(two)\n", "main -> main.two", "main -> main.one"),
            ("def mutate(value):\n    global alias\n    alias = two\ntry:\n    mutate(one, value=one)\nexcept TypeError:\n    pass\n", "main -> main.one", "main -> main.two"),
            ("def target():\n    global alias\n    alias = two\ntry:\n    raise ValueError\nexcept TypeError:\n    target()\nexcept ValueError:\n    pass\n", "main -> main.one", "main -> main.target"),
            ("def three():\n    pass\ndef mutate():\n    global alias\n    alias = two\n    yield None\n    alias = three\niterator = mutate()\nnext(iterator)\nnext(iterator, None)\n", "main -> main.three", "main -> main.two"),
            ("def mutate(value=one):\n    global alias\n    alias = value\n    yield None\niterator = mutate(two)\nalias_iterator = iterator\nfor unused in alias_iterator:\n    pass\n", "main -> main.two", "main -> main.one"),
            ("def replacement(self):\n    global alias\n    alias = two\ndef replace(function):\n    return replacement\ndef identity(function):\n    return function\nclass C:\n    @identity\n    @replace\n    def __init__(self):\n        global alias\n        alias = one\nC()\n", "main -> main.two", "main -> main.C.__init__"),
            ("class C:\n    def __new__(cls):\n        return object()\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "main -> main.one", "main -> main.C.__init__"),
            ("class C:\n    def __enter__(self):\n        global alias\n        alias = two\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        pass\nwith C():\n    pass\n", "main -> main.C.__enter__", "main -> main.one"),
            ("def mutate(value):\n    global alias\n    alias = two\ntry:\n    mutate(one, **{'value': one})\nexcept TypeError:\n    pass\n", "main -> main.one", "main -> main.two"),
            ("def mutate(*, value):\n    global alias\n    alias = value\nmutate(**{'value': two})\n", "main -> main.two", "main -> main.one"),
            ("def mutate(value, /):\n    global alias\n    alias = two\ntry:\n    mutate(value=one)\nexcept TypeError:\n    pass\n", "main -> main.one", "main -> main.two"),
            ("try:\n    raise KeyError\nexcept LookupError:\n    alias = two\n", "main -> main.two", "main -> main.one"),
            ("class ParentError(Exception):\n    pass\nclass ChildError(ParentError):\n    pass\ntry:\n    raise ChildError\nexcept ParentError:\n    alias = two\n", "main -> main.two", "main -> main.one"),
            ("def wrong():\n    global alias\n    alias = one\ndef boom():\n    raise ValueError\ntry:\n    boom()\nexcept TypeError:\n    wrong()\nexcept ValueError:\n    alias = two\n", "main -> main.two", "main -> main.wrong"),
            ("def three():\n    pass\ndef mutate():\n    global alias\n    if True:\n        alias = two\n        yield None\n    alias = three\niterator = mutate()\nnext(iterator)\nnext(iterator, None)\n", "main -> main.three", "main -> main.two"),
            ("def mutate():\n    global alias\n    yield None\n    alias = two\niterator = mutate()\nnext(iterator)\nnext(iterator, None)\nalias = one\nnext(iterator, None)\n", "main -> main.one", "main -> main.two"),
            ("def mutate(value=one):\n    global alias\n    alias = value\n    yield None\niterator = mutate(two)\n(alias_iterator,) = (iterator,)\nfor unused in alias_iterator:\n    pass\n", "main -> main.two", "main -> main.one"),
            ("def mutate():\n    global alias\n    alias = two\ndef identity(function):\n    saved = function\n    return saved\n@identity\ndef decorated():\n    mutate()\ndecorated()\n", "main -> main.decorated", "main -> main.one"),
            ("class C:\n    def __new__(cls):\n        return 42\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "main -> main.one", "main -> main.C.__init__"),
            ("class Other:\n    pass\nclass C:\n    def __new__(cls):\n        return Other()\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "main -> main.one", "main -> main.C.__init__"),
            ("class C:\n    def __enter__(self):\n        global alias\n        alias = two\n        raise RuntimeError\n    def __exit__(self, exc_type, exc, traceback):\n        global alias\n        alias = one\ntry:\n    with C():\n        alias = one\nexcept RuntimeError:\n    pass\n", "main -> main.two", "main -> main.C.__exit__"),
            ("class Target:\n    def mutate(self):\n        global alias\n        alias = two\nclass C:\n    def __enter__(self):\n        return Target()\n    def __exit__(self, exc_type, exc, traceback):\n        pass\nwith C() as target:\n    target.mutate()\n", "main -> main.Target.mutate", "main -> main.one"),
            ("def set_alias(value):\n    global alias\n    alias = value\n(lambda value: set_alias(value))(two)\n", "main -> main.two", "main -> main.one"),
            ("def set_alias(value):\n    global alias\n    alias = value\n(lambda value=two: set_alias(value))()\n", "main -> main.two", "main -> main.one"),
            ("def safe():\n    return 1\ndef target():\n    global alias\n    alias = two\ntry:\n    value = safe()\nexcept Exception:\n    target()\n", "main -> main.one", "main -> main.target"),
            ("def safe():\n    value = 1\n    return None\ndef target():\n    global alias\n    alias = two\ntry:\n    safe()\nexcept Exception:\n    target()\n", "main -> main.one", "main -> main.target"),
            ("def set_alias():\n    global alias\n    alias = two\ntry:\n    (lambda required: set_alias())()\nexcept TypeError:\n    pass\n", "main -> main.one", "main -> main.two"),
            ("def three():\n    pass\ndef safe():\n    if False:\n        raise RuntimeError\ntry:\n    safe()\n    alias = two\nexcept RuntimeError:\n    alias = three\n", "main -> main.two", "main -> main.three"),
            ("def three():\n    pass\ndef mutate(value):\n    global alias\n    alias = three\ntry:\n    mutate(**{'value': one}, **{'value': two})\nexcept TypeError:\n    alias = two\n", "main -> main.two", "main -> main.three"),
            ("def three():\n    pass\ndef mutate():\n    global alias\n    try:\n        alias = two\n        yield None\n    finally:\n        pass\n    alias = three\niterator = mutate()\nnext(iterator)\nnext(iterator, None)\n", "main -> main.three", "main -> main.two"),
            ("def d1(function):\n    def wrapped():\n        function()\n    return wrapped\ndef d2(function):\n    def wrapped():\n        function()\n    return wrapped\ndef d3(function):\n    def wrapped():\n        function()\n    return wrapped\ndef d4(function):\n    def wrapped():\n        function()\n    return wrapped\n@d1\n@d2\n@d3\n@d4\ndef mutate():\n    global alias\n    alias = two\nmutate()\n", "main -> main.two", "main -> main.one"),
            ("class C:\n    def __new__(cls):\n        return []\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "main -> main.one", "main -> main.C.__init__"),
            ("def three():\n    pass\nclass First:\n    def __enter__(self):\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        global alias\n        alias = three\nclass Second:\n    def __enter__(self):\n        global alias\n        alias = two\n        raise RuntimeError\n    def __exit__(self, exc_type, exc, traceback):\n        pass\ntry:\n    with First(), Second():\n        alias = one\nexcept RuntimeError:\n    pass\n", "main -> main.three", "main -> main.two"),
            ("def three():\n    pass\ndef mutate():\n    global alias\n    for item in [0, 1]:\n        alias = two\n        yield item\n    alias = three\niterator = mutate()\nnext(iterator)\nnext(iterator)\n", "main -> main.two", "main -> main.three"),
            ("def three():\n    pass\ndef mutate(value):\n    global alias\n    alias = three\noptions = {'value': one}\ntry:\n    mutate(value=one, **options)\nexcept TypeError:\n    alias = two\n", "main -> main.two", "main -> main.three"),
            ("def three():\n    pass\nclass C:\n    def __enter__(self):\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        global alias\n        alias = two\n        return True\ntry:\n    with C():\n        raise RuntimeError\nexcept RuntimeError:\n    alias = three\n", "main -> main.two", "main -> main.three"),
            ("def three():\n    pass\nclass First:\n    def __enter__(self):\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        raise KeyError\nclass Second:\n    def __enter__(self):\n        raise RuntimeError\n    def __exit__(self, exc_type, exc, traceback):\n        pass\ntry:\n    with First(), Second():\n        pass\nexcept KeyError:\n    alias = two\nexcept RuntimeError:\n    alias = three\n", "main -> main.two", "main -> main.three"),
            ("class C:\n    def __new__(cls):\n        return lambda: None\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "main -> main.one", "main -> main.C.__init__"),
            ("class C:\n    def __new__(cls):\n        return (item for item in [])\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "main -> main.one", "main -> main.C.__init__"),
            ("def replacement():\n    global alias\n    alias = two\nclass Decorator:\n    def __call__(self, function):\n        return replacement\ndef identity(function):\n    return function\n@identity\n@Decorator()\ndef original():\n    pass\noriginal()\n", "main -> main.two", "main -> main.one"),
            ("def three():\n    pass\ndef mutate():\n    global alias\n    for item in range(2):\n        alias = two\n        yield item\n    alias = three\niterator = mutate()\nnext(iterator)\n", "main -> main.two", "main -> main.three"),
            ("def three():\n    pass\ndef mutate():\n    global alias\n    for index, item in enumerate((one, two)):\n        alias = item\n        yield index\n    alias = three\niterator = mutate()\nnext(iterator)\nnext(iterator)\n", "main -> main.two", "main -> main.three"),
            ("def three():\n    pass\ndef mutate(value):\n    global alias\n    alias = three\noptions = {'value': one} | {}\ntry:\n    mutate(value=two, **options)\nexcept TypeError:\n    alias = two\n", "main -> main.two", "main -> main.three"),
            ("def three():\n    pass\ndef mutate(value):\n    global alias\n    alias = three\noptions = dict(value=one)\ntry:\n    mutate(value=two, **options)\nexcept TypeError:\n    alias = two\n", "main -> main.two", "main -> main.three"),
            ("def three():\n    pass\nSUPPRESS = True\nclass C:\n    def __enter__(self):\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        global alias\n        alias = two\n        return SUPPRESS\ntry:\n    with C():\n        raise RuntimeError\nexcept RuntimeError:\n    alias = three\n", "main -> main.two", "main -> main.three"),
            ("def three():\n    pass\ndef suppress():\n    return True\nclass C:\n    def __enter__(self):\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        global alias\n        alias = two\n        return suppress()\ntry:\n    with C():\n        raise RuntimeError\nexcept RuntimeError:\n    alias = three\n", "main -> main.two", "main -> main.three"),
            ("class C:\n    def __new__(cls):\n        return (lambda: None) if True else object.__new__(cls)\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "main -> main.one", "main -> main.C.__init__"),
            ("class C:\n    def __new__(cls, factory):\n        return factory\n    def __init__(self, factory):\n        global alias\n        alias = two\nC(lambda: None)\n", "main -> main.one", "main -> main.C.__init__"),
            ("class Decorator:\n    def __init__(self, function):\n        pass\n    def __call__(self):\n        global alias\n        alias = two\n@Decorator\ndef original():\n    global alias\n    alias = one\noriginal()\n", "main -> main.two", "main -> main.one"),
            ("def chosen():\n    pass\ndef actual():\n    pass\nclass C:\n    def chosen(self):\n        chosen()\n    def __getattribute__(self, name):\n        return actual\nC().chosen()\n", "main -> main.one", "main -> main.C.chosen"),
        )
        for source, required, forbidden in cases:
            temporary, _, graph = analyze_files({"main.py": prefix + source + "alias()\n"})
            with temporary:
                calls = relationship_set(graph, "calls")
                self.assertIn(required, calls, source)
                self.assertNotIn(forbidden, calls, source)

        known_effects = (
            "def factory(value):\n    global alias\n    alias = value\n    def decorator(function):\n        return function\n    return decorator\n@factory(two)\ndef decorated():\n    pass\n",
            "def set_alias(value=one):\n    global alias\n    alias = value\nset_alias(*(two,))\n",
            "def set_alias(value=one):\n    global alias\n    alias = value\nset_alias(**{'value': two})\n",
            "class Base:\n    def mutate(self, value):\n        global alias\n        alias = value\nclass Child(Base):\n    pass\nChild().mutate(two)\n",
            "class Mutator:\n    def __call__(self, value):\n        global alias\n        alias = value\nMutator()(two)\n",
            "class C:\n    def __new__(cls):\n        global alias\n        alias = two\n        return object.__new__(cls)\nC()\n",
            "def replacement(self):\n    global alias\n    alias = two\ndef decorator(function):\n    return replacement\nclass C:\n    @decorator\n    def __init__(self):\n        global alias\n        alias = one\nC()\n",
            "class Replacement:\n    def __init__(self):\n        global alias\n        alias = two\ndef decorator(cls):\n    return Replacement\n@decorator\nclass Original:\n    pass\nOriginal()\n",
        )
        for source in known_effects:
            temporary, _, graph = analyze_files({"main.py": prefix + source + "alias()\n"})
            with temporary:
                calls = relationship_set(graph, "calls")
                self.assertIn("main -> main.two", calls)
                self.assertNotIn("main -> main.one", calls)

        temporary, _, graph = analyze_files({
            "main.py": prefix
            + "class Base:\n    def __init__(self, value):\n        global alias\n        alias = value\n"
            + "class Child(Base):\n    pass\nChild(two)\nalias()\n"
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertIn("main -> main.Base.__init__", calls)
            self.assertIn("main -> main.two", calls)
            self.assertNotIn("main -> main.one", calls)

        temporary, _, graph = analyze_files({
            "main.py": prefix
            + "class C:\n    def mutate(self, value):\n        global alias\n        alias = value\n"
            + "C().mutate(two)\nalias()\n"
        })
        with temporary:
            self.assertIn("main -> main.two", relationship_set(graph, "calls"))

        temporary, _, graph = analyze_files({
            "main.py": "def one():\n    pass\ndef invoke(function=one):\n    function()\ninvoke()\n"
        })
        with temporary:
            self.assertIn("main.invoke -> main.one", relationship_set(graph, "calls"))

        temporary, _, graph = analyze_files({
            "main.py": prefix + "class C:\n    global alias\n    alias = two\nvars(C())\nalias()\n"
        })
        with temporary:
            calls = relationship_set(graph, "calls")
            self.assertIn("main -> main.two", calls)
            self.assertNotIn("main -> main.one", calls)

    def test_regular_module_blocks_same_name_namespace_directory(self):
        temporary, _, graph = analyze_files({
            "foo.py": "def file_target():\n    pass\n",
            "foo/sub.py": "def nested_target():\n    pass\n",
            "main.py": "import foo\nfoo.file_target()\nimport foo.sub\nfoo.sub.nested_target()\n",
        })
        with temporary:
            imports = relationship_set(graph, "imports")
            calls = relationship_set(graph, "calls")
            self.assertIn("main -> foo", imports)
            self.assertIn("main -> foo.file_target", calls)
            self.assertNotIn("main -> foo.sub", imports)
            self.assertNotIn("main -> foo.sub.nested_target", calls)
            self.assertTrue(any("::<unresolved>@" in relation for relation in imports))

    def test_static_values_preserve_suspension_keywords_and_callable_results(self):
        prefix = "def one():\n    pass\ndef two():\n    pass\ndef three():\n    pass\nalias = one\n"
        cases = (
            ("large-range", "def mutate():\n    global alias\n    for item in range(1001):\n        alias = two\n        yield item\n    alias = three\niterator = mutate()\nnext(iterator)\n", "two"),
            ("enumerate-start", "def mutate():\n    global alias\n    for index, item in enumerate((one, two), start=7):\n        alias = item\n        yield index\n    alias = three\niterator = mutate()\nnext(iterator)\nnext(iterator)\n", "two"),
            ("comprehension-keyword", "def mutate(value):\n    global alias\n    alias = three\noptions = {key: one for key in ('value',)}\ntry:\n    mutate(value=two, **options)\nexcept TypeError:\n    alias = two\n", "two"),
            ("subscript-keyword", "def mutate(value):\n    global alias\n    alias = three\noptions = {}\noptions['value'] = one\ntry:\n    mutate(value=two, **options)\nexcept TypeError:\n    alias = two\n", "two"),
            ("comparison-truth", "class C:\n    def __enter__(self):\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        global alias\n        alias = two\n        return 1 == 1\ntry:\n    with C():\n        raise RuntimeError\nexcept RuntimeError:\n    alias = three\n", "two"),
            ("instance-truth", "class Marker:\n    pass\nclass C:\n    def __enter__(self):\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        global alias\n        alias = two\n        return Marker()\ntry:\n    with C():\n        raise RuntimeError\nexcept RuntimeError:\n    alias = three\n", "two"),
            ("indexed-lambda", "class C:\n    def __new__(cls):\n        return ((lambda: None),)[0]\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "one"),
            ("short-circuited-lambda", "class C:\n    def __new__(cls):\n        return (lambda: None) or object.__new__(cls)\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "one"),
            ("callable-decorator-lambda", "def set_alias(value):\n    global alias\n    alias = value\nclass Decorator:\n    def __call__(self, function):\n        return lambda: set_alias(two)\n@Decorator()\ndef original():\n    set_alias(three)\noriginal()\n", "two"),
            ("raising-decorator-initializer", "class Decorator:\n    def __init__(self, function):\n        raise TypeError\n    def __call__(self):\n        global alias\n        alias = three\ntry:\n    @Decorator\n    def original():\n        pass\nexcept TypeError:\n    alias = two\n", "two"),
        )
        for name, body, target in cases:
            with self.subTest(name=name):
                source = prefix + body + "alias()\n"
                temporary, _, graph = analyze_files({"main.py": source})
                with temporary:
                    names = {node["id"]: node["qualifiedName"] for node in graph["nodes"]}
                    calls = {
                        f"{names[edge['source']]} -> {names[edge['target']]}"
                        for edge in graph["edges"] if edge["kind"] == "calls"
                        and edge["range"]["start"]["line"] == len(source.splitlines())
                    }
                    self.assertEqual({f"main -> main.{target}"}, calls)

    def test_package_module_entry_and_schema_types_are_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pkg").mkdir()
            (root / "pkg.py").write_text("def wrong():\n    pass\n")
            (root / "pkg/__init__.py").write_text("")
            (root / "pkg/__main__.py").write_text("def main():\n    pass\nmain()\n")
            (root / "code-view.json").write_text(json.dumps({"startCommand": "python -m pkg"}))
            graph = analyze_repository(root)
            nodes = {node["id"]: node for node in graph["nodes"]}
            self.assertEqual("pkg.__main__", nodes[graph["project"]["entryNodeId"]]["qualifiedName"])
            for invalid in ({"entry": ""}, {"schemaVersion": True}, {"schemaVersion": 1.0}):
                (root / "code-view.json").write_text(json.dumps(invalid))
                with self.assertRaises(AnalysisError):
                    load_config(root)
            for invalid in (
                {"entry": None}, {"startCommand": None},
                {"index": {"tests": None}}, {"canvas": {"relationships": None}},
                {"index": {"tests": []}},
                {"index": {"modules": []}},
                {"canvas": {"initialView": []}},
                {"startCommand": {"argv": ["python", "pkg.py"], "mode": []}},
            ):
                (root / "code-view.json").write_text(json.dumps(invalid))
                with self.assertRaises(AnalysisError):
                    load_config(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pkg").mkdir()
            (root / "pkg.py").write_text("pass\n")
            (root / "pkg/__main__.py").write_text("pass\n")
            (root / "code-view.json").write_text(json.dumps({"startCommand": "python -m pkg"}))
            graph = analyze_repository(root)
            nodes = {node["id"]: node for node in graph["nodes"]}
            self.assertEqual("pkg.py", nodes[graph["project"]["entryNodeId"]]["path"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pkg/sub").mkdir(parents=True)
            (root / "pkg.py").write_text("pass\n")
            (root / "pkg/sub/__main__.py").write_text("pass\n")
            (root / "code-view.json").write_text(json.dumps({"startCommand": "python -m pkg.sub"}))
            with self.assertRaisesRegex(AnalysisError, "blocked"):
                analyze_repository(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pkg").mkdir()
            (root / "pkg.py").write_text("pass\n")
            (root / "pkg/__init__.py").write_text("")
            (root / "code-view.json").write_text(json.dumps({"startCommand": "python -m pkg"}))
            with self.assertRaisesRegex(AnalysisError, "does not resolve"):
                analyze_repository(root)

    def test_duplicate_definition_calls_are_not_guessed(self):
        temporary, _, graph = analyze_files({
            "main.py": "def duplicate():\n    return 1\nduplicate()\ndef duplicate():\n    return 2\n"
        })
        with temporary:
            call = next(edge for edge in graph["edges"] if edge["kind"] == "calls")
            target = next(node for node in graph["nodes"] if node["id"] == call["target"])
            self.assertEqual("unresolved", target["kind"])
            self.assertEqual("multiple definitions cannot be resolved statically", target["reason"])

    def test_semantic_graph_validator_rejects_integrity_failures(self):
        graph = analyze_repository(FIXTURE)
        dangling = json.loads(json.dumps(graph))
        dangling["edges"][0]["target"] = "missing"
        with self.assertRaisesRegex(AnalysisError, "dangling"):
            validate_graph_document(dangling)
        invalid_boundary = json.loads(json.dumps(graph))
        boundary = next(node for node in invalid_boundary["nodes"] if node["kind"] == "external")
        boundary["reason"] = None
        with self.assertRaisesRegex(AnalysisError, "reason"):
            validate_graph_document(invalid_boundary)
        ordinary_boundary = json.loads(json.dumps(graph))
        ordinary = next(node for node in ordinary_boundary["nodes"] if node["kind"] == "function")
        ordinary["external"] = True
        ordinary["reason"] = "outside"
        with self.assertRaisesRegex(AnalysisError, "flags"):
            validate_graph_document(ordinary_boundary)
        escaped = json.loads(json.dumps(graph))
        next(node for node in escaped["nodes"] if node["path"])["path"] = "../outside.py"
        with self.assertRaisesRegex(AnalysisError, "repository-relative"):
            validate_graph_document(escaped)
        duplicate_reachable = json.loads(json.dumps(graph))
        duplicate_reachable["project"]["entryReachableNodeIds"].append(duplicate_reachable["project"]["entryNodeId"])
        with self.assertRaisesRegex(AnalysisError, "duplicate"):
            validate_graph_document(duplicate_reachable)

    def test_config_is_data_and_never_a_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text("print('safe')\n")
            marker = root / "executed"
            (root / "code-view.json").write_text(json.dumps({"startCommand": f"python main.py; touch {marker}"}))
            with self.assertRaisesRegex(AnalysisError, "shell metacharacters"):
                analyze_repository(root)
            self.assertFalse(marker.exists())
        self.assertEqual("pkg/main.py", parse_start_command("python3 -m pkg.main --port 1"))

    def test_entry_autodetection_and_extensionless_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app.py").write_text("if __name__ == '__main__':\n    print('ready')\n")
            graph = analyze_repository(root)
            nodes = {node["id"]: node for node in graph["nodes"]}
            self.assertEqual("app", nodes[graph["project"]["entryNodeId"]]["qualifiedName"])
            (root / "code-view.json").write_text('{"entry":"app"}')
            self.assertEqual(graph["project"]["entryNodeId"], analyze_repository(root)["project"]["entryNodeId"])

    def test_full_v1_config_entry_symbol_and_exclusions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text("def run():\n    return 1\n")
            (root / "generated.py").write_text("def generated():\n    pass\n")
            (root / "test_main.py").write_text("from main import run\n\ndef test_run():\n    run()\n")
            config = {
                "schemaVersion": 1,
                "entry": {"file": "main.py", "symbol": "run", "command": "run"},
                "startCommand": {"argv": ["python3", "ignored.py"], "mode": "document"},
                "languages": ["python"],
                "includeTests": False,
                "index": {"tests": "include", "modules": "show", "externalPackages": "collapse", "exclude": ["generated.py"]},
                "canvas": {"initialView": "whole-repo", "relationships": ["calls", "test_covers"]},
                "git": {"base": "HEAD~1"},
            }
            (root / "code-view.json").write_text(json.dumps(config))
            parsed = load_config(root)
            self.assertTrue(parsed.include_tests)
            self.assertEqual(("generated.py",), parsed.exclude)
            graph = analyze_repository(root)
            nodes = {node["id"]: node for node in graph["nodes"]}
            self.assertEqual("main.run", nodes[graph["project"]["entryNodeId"]]["qualifiedName"])
            paths = {node["path"] for node in graph["nodes"]}
            self.assertIn("test_main.py", paths)
            self.assertNotIn("generated.py", paths)

    def test_exclude_wins_and_hostile_symlink_is_ignored(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            (root / "main.py").write_text("")
            (root / "tests").mkdir()
            (root / "tests/test_hidden.py").write_text("def hidden():\n    pass\n")
            outside_file = Path(outside) / "secret.py"
            outside_file.write_text("SECRET = 'outside'\n")
            try:
                (root / "linked.py").symlink_to(outside_file)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")
            config = {"includeTests": True, "index": {"tests": "include", "exclude": ["tests/**"]}}
            (root / "code-view.json").write_text(json.dumps(config))
            paths = {node["path"] for node in analyze_repository(root)["nodes"]}
            self.assertNotIn("tests/test_hidden.py", paths)
            self.assertNotIn("linked.py", paths)

    def test_full_config_strict_nested_validation(self):
        schema = json.loads((ROOT / "contracts/code-view-config-v1.schema.json").read_text())
        example = ROOT / "contracts/code-view-config-v1.example.json"
        self.assertFalse(schema["additionalProperties"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text("")
            (root / "code-view.json").write_text(example.read_text())
            parsed = load_config(root)
            self.assertEqual("main.py", parsed.entry)
            self.assertEqual(("python3", "main.py"), parsed.entry_command)
            self.assertEqual("manual", parsed.entry_command_mode)
            self.assertIn("reads", parsed.relationships)
            self.assertNotIn("test_covers", parsed.relationships)
            (root / "code-view.json").write_text('{"index":{"exclude":["../secret.py"]}}')
            with self.assertRaisesRegex(AnalysisError, "inside"):
                load_config(root)
            (root / "code-view.json").write_text('{"canvas":{"unknown":true}}')
            with self.assertRaisesRegex(AnalysisError, "unknown canvas"):
                load_config(root)

    def test_relationship_object_normalizes_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text("")
            (root / "code-view.json").write_text('{"canvas":{"relationships":{"calls":false,"test_covers":true}}}')
            parsed = load_config(root)
            self.assertNotIn("calls", parsed.relationships)
            self.assertIn("imports", parsed.relationships)
            self.assertIn("test_covers", parsed.relationships)

    def test_invalid_config_and_repository_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text("")
            (root / "code-view.json").write_text('{"unknown":true}')
            with self.assertRaisesRegex(AnalysisError, "unknown"):
                analyze_repository(root)
            (root / "code-view.json").write_text('{"entry":"../main.py"}')
            with self.assertRaisesRegex(AnalysisError, "inside"):
                analyze_repository(root)

    def test_syntax_error_is_a_diagnostic_not_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "broken.py").write_text("def broken(:\n")
            graph = analyze_repository(root)
            self.assertEqual("PY_SYNTAX_ERROR", graph["diagnostics"][0]["code"])
            self.assertEqual("broken.py", graph["diagnostics"][0]["path"])

    def test_repository_code_is_never_imported_or_executed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "repository-code-ran"
            source = (
                "from pathlib import Path\n"
                "from urllib.request import urlopen\n"
                f"Path({str(marker)!r}).write_text('executed')\n"
                "urlopen('https://example.invalid/')\n"
            )
            (root / "main.py").write_text(source)
            graph = analyze_repository(root)
            self.assertFalse(marker.exists())
            self.assertFalse(graph["diagnostics"])
            self.assertIn("main -> urllib.request.urlopen", relationship_set(graph, "calls"))

    def test_safe_relationships_and_dynamic_honesty(self):
        source = '''\
def decorator(fn):
    return fn

class Base:
    pass

class Child(Base):
    @decorator
    def use(self, other: Base):
        self.value = other
        return self.value

def dynamic(name):
    globals()[name]()
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text(source)
            graph = analyze_repository(root)
            self.assertIn("main.Child -> main.Base", relationship_set(graph, "inherits"))
            self.assertIn("main.Child.use -> main.decorator", relationship_set(graph, "decorates"))
            self.assertIn("main.Child.use -> main.Base", relationship_set(graph, "type_uses"))
            self.assertTrue(relationship_set(graph, "reads"))
            self.assertTrue(relationship_set(graph, "writes"))
            unresolved = [node for node in graph["nodes"] if node["unresolved"]]
            self.assertEqual(1, len(unresolved))
            self.assertIn("cannot be resolved statically", unresolved[0]["reason"])

    def test_contract_edge_vocabulary_and_legacy_mapping(self):
        schema = json.loads((ROOT / "contracts/code-view-graph-v1.schema.json").read_text())
        enum = schema["$defs"]["edge"]["properties"]["kind"]["enum"]
        self.assertEqual(set(CANONICAL_EDGE_KINDS), set(enum))
        self.assertEqual("EDGE_MEMBER", LEGACY_EDGE_MAP["contains"])
        self.assertEqual("EDGE_IMPORT", LEGACY_EDGE_MAP["imports"])
        self.assertEqual("EDGE_CALL", LEGACY_EDGE_MAP["constructs"])
        self.assertEqual("EDGE_ANNOTATION_USAGE", LEGACY_EDGE_MAP["decorates"])

    def test_fixture_gate_budget(self):
        started = time.perf_counter()
        for _ in range(5):
            analyze_repository(FIXTURE)
        self.assertLess(time.perf_counter() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
