#!/usr/bin/env python3
"""Deterministic semantic evaluation for the Python analyzer."""

import concurrent.futures
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "services/python-analyzer"
FIXTURE = ROOT / "tests/fixtures/tic-tac-toe"
sys.path.insert(0, str(SERVICE))

from analyzer import AnalysisError, analyze_repository, validate_graph_document  # noqa: E402


def score(expected, actual):
    expected, actual = set(expected), set(actual)
    true_positive = len(expected & actual)
    precision = true_positive / len(actual) if actual else float(not expected)
    recall = true_positive / len(expected) if expected else 1.0
    return {"precision": precision, "recall": recall, "expected": len(expected), "actual": len(actual)}


def relationship_keys(graph, kind):
    names = {node["id"]: node["qualifiedName"] for node in graph["nodes"]}
    return [f"{names[edge['source']]} -> {names[edge['target']]}" for edge in graph["edges"] if edge["kind"] == kind]


def adversarial_metrics():
    rows = {}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "def target():\n    pass\n\ndef run(target):\n    target()\n"
            "class C:\n    def helper(self):\n        pass\n    def run(self):\n        helper()\n"
        )
        graph = analyze_repository(root)
        calls = relationship_keys(graph, "calls")
        rows["shadowingHasNoInventedEdge"] = (
            "main.run -> main.target" not in calls
            and "main.C.run -> main.C.helper" not in calls
            and len([node for node in graph["nodes"] if node["unresolved"]]) == 2
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "def one():\n    pass\ndef two():\n    pass\n"
            "alias = one\nif False:\n    alias = two\nalias()\n"
            "if condition:\n    branch = one\nelse:\n    branch = two\nbranch()\n"
            "for item in []:\n    loop_alias = one\nloop_alias()\n"
            "try:\n    try_alias = one\nexcept Exception:\n    try_alias = two\ntry_alias()\n"
            "def duplicate():\n    return 1\nduplicate()\ndef duplicate():\n    return 2\n"
        )
        graph = analyze_repository(root)
        calls = relationship_keys(graph, "calls")
        nodes = {node["id"]: node for node in graph["nodes"]}
        call_targets = [nodes[edge["target"]] for edge in graph["edges"] if edge["kind"] == "calls"]
        rows["controlFlowAliasHonesty"] = (
            calls.count("main -> main.one") == 2
            and "main -> main.two" not in calls
            and len([node for node in call_targets if node["reason"] == "runtime variable target cannot be resolved statically"]) == 1
            and len([node for node in call_targets if node["reason"] == "name is not bound on every reachable path"]) == 1
        )
        rows["duplicateDefinitionHonesty"] = any(
            node["reason"] == "multiple definitions cannot be resolved statically" for node in call_targets
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "from a import target\nif False:\n    from b import target\n"
            "for item in []:\n    from b import target\ntarget()\n"
        )
        (root / "a.py").write_text("def target():\n    pass\n")
        (root / "b.py").write_text("def target():\n    pass\n")
        calls = relationship_keys(analyze_repository(root), "calls")
        rows["unreachableImportPreservesLiveBinding"] = (
            "main -> a.target" in calls and "main -> b.target" not in calls
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "class Old:\n    def ping(self):\n        pass\n"
            "class One:\n    pass\nclass Two:\n    pass\nclass Use:\n"
            "    def run(self, flag):\n        self.value = Old()\n"
            "        if flag:\n            self.value = One()\n"
            "        else:\n            self.value = Two()\n"
            "        self.value.ping()\n"
        )
        graph = analyze_repository(root)
        calls = relationship_keys(graph, "calls")
        rows["uncertainFieldTypeIsUnresolved"] = (
            "main.Use.run -> main.Old.ping" not in calls
            and any(
                node["reason"] == "attribute receiver cannot be resolved statically"
                for node in graph["nodes"] if node["unresolved"]
            )
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "from a import target\n"
            "def before_import():\n    target()\n    from b import target\n"
            "def after_import():\n    from b import target\n    target()\n"
            "def before_definition():\n    target()\n    def target():\n        pass\n"
            "def after_definition():\n    def target():\n        pass\n    target()\n"
            "def before_exception():\n    target()\n    try:\n        pass\n    except Exception as target:\n        pass\n"
            "def global_call():\n    global target\n    target()\n"
            "def outer():\n    def target():\n        pass\n    def nonlocal_call():\n        nonlocal target\n        target()\n"
        )
        (root / "a.py").write_text("def target():\n    pass\n")
        (root / "b.py").write_text("def target():\n    pass\n")
        graph = analyze_repository(root)
        calls = relationship_keys(graph, "calls")
        rows["functionWideLexicalBindings"] = (
            "main.after_import -> b.target" in calls
            and "main.after_definition -> main.after_definition.target" in calls
            and "main.global_call -> a.target" in calls
            and "main.outer.nonlocal_call -> main.outer.target" in calls
            and "main.before_import -> a.target" not in calls
            and "main.before_definition -> main.before_definition.target" not in calls
            and "main.before_exception -> a.target" not in calls
            and len([relation for relation in calls if relation.startswith(("main.before_import -> main::<unresolved>", "main.before_definition -> main::<unresolved>", "main.before_exception -> main::<unresolved>"))]) == 3
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "from target import call as alias\n"
            "def run(values, value):\n"
            "    (lambda alias: alias())(value)\n"
            "    [alias() for alias in values]\n"
            "    try:\n        raise ValueError()\n"
            "    except Exception as alias:\n        alias()\n"
            "    match value:\n        case alias:\n            alias()\n"
        )
        (root / "target.py").write_text("def call():\n    pass\n")
        calls = relationship_keys(analyze_repository(root), "calls")
        rows["nestedScopeBindingsHonest"] = (
            "main.run -> target.call" not in calls
            and len([
                relation for relation in calls
                if relation.startswith("main.run -> main::<unresolved>") and relation.endswith(":alias")
            ]) == 4
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "from target import call as alias\n"
            "del alias\n"
            "alias()\n"
            "if False:\n    def hidden():\n        pass\n"
            "hidden()\n"
        )
        (root / "target.py").write_text("def call():\n    pass\n")
        calls = relationship_keys(analyze_repository(root), "calls")
        rows["deletedAndUnreachableBindings"] = (
            "main -> target.call" not in calls
            and "main -> main.hidden" not in calls
            and len([relation for relation in calls if "::<unresolved>@" in relation]) == 2
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "conflict").mkdir()
        (root / "conflict.py").write_text("def file_symbol():\n    pass\n")
        (root / "conflict/__init__.py").write_text("def package_symbol():\n    pass\n")
        (root / "main.py").write_text(
            "from conflict import file_symbol, package_symbol\nfile_symbol()\npackage_symbol()\n"
        )
        graph = analyze_repository(root)
        functions = {
            (node["qualifiedName"], node["path"])
            for node in graph["nodes"] if node["kind"] == "function"
        }
        calls = relationship_keys(graph, "calls")
        rows["modulePackageCollisionComplete"] = (
            ("conflict.file_symbol", "conflict.py") in functions
            and ("conflict.package_symbol", "conflict/__init__.py") in functions
            and "main -> conflict.file_symbol" not in calls
            and "main -> conflict.package_symbol" not in calls
            and len([relation for relation in calls if "::<unresolved>@" in relation]) == 2
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "def one():\n    pass\ndef two():\n    pass\n"
            "alias = one\ndef run():\n    alias()\nrun()\nalias = two\nrun()\n"
            "def outer():\n    nested = one\n    def inner():\n        nested()\n"
            "    inner()\n    nested = two\n    inner()\nouter()\n"
        )
        calls = relationship_keys(analyze_repository(root), "calls")
        rows["lateBindingFollowsKnownCallOrder"] = (
            "main.run -> main.two" in calls
            and "main.outer.inner -> main.two" in calls
            and "main.run -> main.one" in calls
            and "main.outer.inner -> main.one" in calls
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
            "def ignored():\n    global alias\n    alias = two\n"
            "def read():\n    alias()\nread()\n"
        )
        first_calls = relationship_keys(analyze_repository(root), "calls")
        (root / "main.py").write_text(
            "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
            "def read():\n    alias()\n"
            "def mutate():\n    global alias\n    alias = two\n"
            "mutate()\nread()\n"
        )
        second_calls = relationship_keys(analyze_repository(root), "calls")
        rows["globalWriterRequiresKnownInvocation"] = (
            "main.read -> main.one" in first_calls and "main.read -> main.two" in second_calls
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "def one():\n    pass\ndef two():\n    pass\n"
            "alias = one\nexec('alias = two')\nalias()\n"
            "other = one\nglobals()['other'] = two\nother()\n"
        )
        calls = relationship_keys(analyze_repository(root), "calls")
        rows["dynamicNamespaceStopsAtBoundary"] = (
            "main -> main.one" not in calls
            and len([relation for relation in calls if "::<unresolved>@" in relation]) == 2
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        outcomes = []
        for mutation in (
            "def mutate():\n    exec('alias = two', globals())\nmutate()\n",
            "globals().update(alias=two)\n",
        ):
            (root / "main.py").write_text(
                "def one():\n    pass\ndef two():\n    pass\nalias = one\n" + mutation + "alias()\n"
            )
            calls = relationship_keys(analyze_repository(root), "calls")
            outcomes.append(
                "main -> main.one" not in calls
                and any(relation.startswith("main -> main::<unresolved>@") and relation.endswith(":alias") for relation in calls)
            )
        rows["dynamicNamespaceEquivalentForms"] = all(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "foo").mkdir()
        (root / "foo.py").write_text("def file_target():\n    pass\n")
        (root / "foo/sub.py").write_text("def nested_target():\n    pass\n")
        (root / "main.py").write_text("import foo\nfoo.file_target()\nimport foo.sub\nfoo.sub.nested_target()\n")
        graph = analyze_repository(root)
        calls = relationship_keys(graph, "calls")
        imports = relationship_keys(graph, "imports")
        rows["regularModuleBlocksNamespaceDirectory"] = (
            "main -> foo" in imports
            and "main -> foo.file_target" in calls
            and "main -> foo.sub" not in imports
            and "main -> foo.sub.nested_target" not in calls
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "pkg").mkdir()
        (root / "pkg.py").write_text("def wrong():\n    pass\n")
        (root / "pkg/__init__.py").write_text("")
        (root / "pkg/__main__.py").write_text("def main():\n    pass\nmain()\n")
        (root / "code-view.json").write_text(json.dumps({"startCommand": "python -m pkg"}))
        graph = analyze_repository(root)
        nodes = {node["id"]: node for node in graph["nodes"]}
        strict = []
        for invalid in (
            {"entry": None}, {"startCommand": None},
            {"index": {"tests": None}}, {"canvas": {"relationships": None}},
            {"entry": ""}, {"schemaVersion": True}, {"schemaVersion": 1.0},
            {"index": {"tests": []}}, {"index": {"modules": []}},
            {"canvas": {"initialView": []}},
            {"startCommand": {"argv": ["python", "pkg.py"], "mode": []}},
        ):
            (root / "code-view.json").write_text(json.dumps(invalid))
            try:
                analyze_repository(root)
            except AnalysisError:
                strict.append(True)
            else:
                strict.append(False)
        rows["packageModuleEntryAndStrictConfig"] = (
            nodes[graph["project"]["entryNodeId"]]["qualifiedName"] == "pkg.__main__" and all(strict)
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        outcomes = []
        for source in (
            "def mutate():\n    global alias\n    alias = two\n    yield\nmutate()\n",
            "async def mutate():\n    global alias\n    alias = two\nmutate()\n",
            "def mutate():\n    global alias\n    return\n    alias = two\nmutate()\n",
            "def mutate():\n    global alias\n    alias = two\nFalse and mutate()\n",
            "def mutate():\n    global alias\n    alias = two\nNone if True else mutate()\n",
            "def mutate():\n    global alias\n    alias = two\n[mutate() for unused in []]\n",
        ):
            (root / "main.py").write_text(prefix + source + "alias()\n")
            calls = relationship_keys(analyze_repository(root), "calls")
            outcomes.append("main -> main.one" in calls and "main -> main.two" not in calls)
        rows["lazyAndUnreachableEffectsStayInactive"] = all(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        outcomes = []
        for mutation in (
            "namespace = globals\nnamespace()['alias'] = two\n",
            "import builtins\nbuiltins.globals()['alias'] = two\n",
            "runner = exec\nrunner('alias = two')\n",
            "import builtins\nbuiltins.exec('alias = two')\n",
        ):
            (root / "main.py").write_text(prefix + mutation + "alias()\n")
            calls = relationship_keys(analyze_repository(root), "calls")
            outcomes.append(
                "main -> main.one" not in calls
                and "main -> main.two" not in calls
                and any(relation.endswith(":alias") for relation in calls if "::<unresolved>@" in relation)
            )
        (root / "main.py").write_text(
            "def one():\n    pass\nalias = one\ndef globals():\n    return {}\nglobals()\nalias()\n"
        )
        rows["dynamicBuiltinIdentityBoundary"] = all(outcomes) and "main -> main.one" in relationship_keys(
            analyze_repository(root), "calls"
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
            "def invoke(function):\n    function()\n"
            "def set_alias(value):\n    global alias\n    alias = value\n"
            "class Mutator:\n    def __init__(self, value):\n        set_alias(value)\n"
            "invoke(one)\nMutator(two)\nalias()\n"
        )
        calls = relationship_keys(analyze_repository(root), "calls")
        rows["knownInvocationPropagatesEffects"] = (
            "main.invoke -> main.one" in calls
            and "main.Mutator.__init__ -> main.set_alias" in calls
            and "main -> main.two" in calls
            and "main -> main.one" not in calls
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "def replacement():\n    pass\ndef decorator(function):\n    return replacement\n"
            "@decorator\ndef decorated():\n    pass\ndecorated()\n"
        )
        calls = relationship_keys(analyze_repository(root), "calls")
        rows["knownDecoratorReplacementResolves"] = (
            "main -> main.decorated" not in calls
            and "main -> main.replacement" in calls
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "pkg").mkdir()
        (root / "pkg.py").write_text("pass\n")
        (root / "pkg/__main__.py").write_text("pass\n")
        (root / "code-view.json").write_text(json.dumps({"startCommand": "python -m pkg"}))
        graph = analyze_repository(root)
        nodes = {node["id"]: node for node in graph["nodes"]}
        rows["pythonModuleBeatsNamespaceDirectory"] = nodes[graph["project"]["entryNodeId"]]["path"] == "pkg.py"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        (root / "main.py").write_text(
            "import os\n" + prefix + "def mutate():\n    global alias\n    alias = two\n"
            "if os.environ.get('MUTATE'):\n    mutate()\nalias()\n"
        )
        uncertain = relationship_keys(analyze_repository(root), "calls")
        definite = []
        for control in ("try:\n    mutate()\nexcept Exception:\n    pass\n", "for unused in [0]:\n    mutate()\n"):
            (root / "main.py").write_text(
                prefix + "def mutate():\n    global alias\n    alias = two\n" + control + "alias()\n"
            )
            definite.append(relationship_keys(analyze_repository(root), "calls"))
        rows["uncertainAndDefiniteCallEffects"] = (
            "main -> main.one" not in uncertain
            and "main -> main.two" not in uncertain
            and any(relation.endswith(":alias") for relation in uncertain)
            and all("main -> main.two" in calls and "main -> main.one" not in calls for calls in definite)
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        outcomes = []
        for body in ("while True:\n        return\n", "try:\n        return\n    finally:\n        pass\n"):
            (root / "main.py").write_text(
                prefix + "def mutate():\n    global alias\n    " + body + "    alias = two\nmutate()\nalias()\n"
            )
            calls = relationship_keys(analyze_repository(root), "calls")
            outcomes.append("main -> main.one" in calls and "main -> main.two" not in calls)
        rows["nestedTerminationStopsEffects"] = all(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        (root / "main.py").write_text(
            "import asyncio\n" + prefix + "async def mutate():\n    global alias\n    alias = two\n"
            "async def read():\n    await mutate()\n    alias()\nasyncio.run(read())\n"
        )
        async_calls = relationship_keys(analyze_repository(root), "calls")
        (root / "main.py").write_text(
            prefix + "def mutate():\n    global alias\n    alias = two\n    yield None\n"
            "for unused in mutate():\n    pass\nalias()\n"
        )
        generator_calls = relationship_keys(analyze_repository(root), "calls")
        rows["awaitedAndIteratedLazyEffects"] = (
            "main.read -> main.two" in async_calls
            and "main.read -> main.one" not in async_calls
            and "main -> main.two" in generator_calls
            and "main -> main.one" not in generator_calls
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        (root / "main.py").write_text(
            prefix + "def decorator(function):\n    global alias\n    alias = two\n    return function\n"
            "@decorator\ndef decorated():\n    pass\nalias()\n"
        )
        effect_calls = relationship_keys(analyze_repository(root), "calls")
        (root / "main.py").write_text(
            "def decorator(function):\n    return function\nclass C:\n    @decorator\n"
            "    def decorated(self):\n        pass\ninstance = C()\ninstance.decorated()\n"
        )
        boundary_calls = relationship_keys(analyze_repository(root), "calls")
        rows["decoratorEffectsAndMethodBoundary"] = (
            "main -> main.two" in effect_calls
            and "main -> main.one" not in effect_calls
            and "main -> main.C.decorated" in boundary_calls
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        outcomes = []
        for source in (
            "class Base:\n    def __init__(self, value):\n        global alias\n        alias = value\nclass Child(Base):\n    pass\nChild(two)\n",
            "class C:\n    def mutate(self, value):\n        global alias\n        alias = value\nC().mutate(two)\n",
            "def invoke(function=two):\n    global alias\n    alias = function\ninvoke()\n",
        ):
            (root / "main.py").write_text(prefix + source + "alias()\n")
            calls = relationship_keys(analyze_repository(root), "calls")
            outcomes.append("main -> main.two" in calls and "main -> main.one" not in calls)
        rows["knownReceiversInheritanceAndDefaults"] = all(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
            "class C:\n    global alias\n    alias = two\nvars(C())\nalias()\n"
        )
        calls = relationship_keys(analyze_repository(root), "calls")
        rows["classGlobalAndObjectVarsSemantics"] = (
            "main -> main.two" in calls and "main -> main.one" not in calls
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "pkg/sub").mkdir(parents=True)
        (root / "pkg.py").write_text("pass\n")
        (root / "pkg/sub/__main__.py").write_text("pass\n")
        (root / "code-view.json").write_text(json.dumps({"startCommand": "python -m pkg.sub"}))
        try:
            analyze_repository(root)
        except AnalysisError:
            rows["pythonModuleBlocksNamespaceDescendant"] = True
        else:
            rows["pythonModuleBlocksNamespaceDescendant"] = False
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        outcomes = []
        for expression in (
            "callback = lambda: mutate()",
            "unused = (mutate() for item in [0])",
            "unused = [mutate() for item in [0] if False]",
            "() and mutate()",
        ):
            (root / "main.py").write_text(
                prefix + "def mutate():\n    global alias\n    alias = two\n" + expression + "\nalias()\n"
            )
            calls = relationship_keys(analyze_repository(root), "calls")
            outcomes.append("main -> main.one" in calls and "main -> main.two" not in calls)
        rows["lazyExpressionsAndLiteralShortCircuit"] = all(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        outcomes = []
        for body in (
            "if flag:\n        return\n        target()\n",
            "try:\n        return\n    except Exception:\n        target()\n",
            "try:\n        raise ValueError\n    except ValueError:\n        pass\n    else:\n        target()\n",
        ):
            (root / "main.py").write_text("def target():\n    pass\ndef run(flag=True):\n    " + body + "run()\n")
            outcomes.append("main.run -> main.target" not in relationship_keys(analyze_repository(root), "calls"))
        rows["branchAndTryTerminationReachability"] = all(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        outcomes = []
        for source in (
            "import asyncio\n" + prefix + "async def mutate():\n    global alias\n    alias = two\n"
            "async def read():\n    pending = mutate()\n    await pending\n    alias()\nasyncio.run(read())\n",
            prefix + "def mutate():\n    global alias\n    alias = two\n    yield None\n"
            "iterator = mutate()\nfor unused in iterator:\n    pass\nalias()\n",
            prefix + "def mutate():\n    global alias\n    alias = two\n    yield None\n"
            "def outer():\n    yield from mutate()\nfor unused in outer():\n    pass\nalias()\n",
        ):
            (root / "main.py").write_text(source)
            calls = relationship_keys(analyze_repository(root), "calls")
            outcomes.append("main -> main.two" in calls or "main.read -> main.two" in calls)
        rows["lazyValuesActivateOnConsumption"] = all(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        outcomes = []
        for source in (
            "def factory(value):\n    global alias\n    alias = value\n    def decorator(function):\n        return function\n    return decorator\n@factory(two)\ndef decorated():\n    pass\n",
            "def set_alias(value=one):\n    global alias\n    alias = value\nset_alias(*(two,))\n",
            "def set_alias(value=one):\n    global alias\n    alias = value\nset_alias(**{'value': two})\n",
        ):
            (root / "main.py").write_text(prefix + source + "alias()\n")
            calls = relationship_keys(analyze_repository(root), "calls")
            outcomes.append("main -> main.two" in calls and "main -> main.one" not in calls)
        rows["decoratorFactoryAndExpandedArguments"] = all(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\nalias = one\n"
        outcomes = []
        for source in (
            "class Base:\n    def mutate(self, value):\n        global alias\n        alias = value\nclass Child(Base):\n    pass\nChild().mutate(two)\n",
            "class Mutator:\n    def __call__(self, value):\n        global alias\n        alias = value\nMutator()(two)\n",
            "class C:\n    def __new__(cls):\n        global alias\n        alias = two\n        return object.__new__(cls)\nC()\n",
            "def replacement(self):\n    global alias\n    alias = two\ndef decorator(function):\n    return replacement\nclass C:\n    @decorator\n    def __init__(self):\n        global alias\n        alias = one\nC()\n",
            "class Replacement:\n    def __init__(self):\n        global alias\n        alias = two\ndef decorator(cls):\n    return Replacement\n@decorator\nclass Original:\n    pass\nOriginal()\n",
        ):
            (root / "main.py").write_text(prefix + source + "alias()\n")
            calls = relationship_keys(analyze_repository(root), "calls")
            outcomes.append("main -> main.two" in calls and "main -> main.one" not in calls)
        rows["inheritedCallableAndConstructorDispatch"] = all(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
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
            ("class C:\n    def __enter__(self):\n        global alias\n        alias = two\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        pass\nwith C():\n    pass\n", "main -> main.two", "main -> main.one"),
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
        outcomes = []
        for source, required, forbidden in cases:
            (root / "main.py").write_text(prefix + source + "alias()\n")
            calls = relationship_keys(analyze_repository(root), "calls")
            outcomes.append(required in calls and forbidden not in calls)
        rows["deferredExecutionAndPythonDispatchBoundaries"] = all(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prefix = "def one():\n    pass\ndef two():\n    pass\ndef three():\n    pass\nalias = one\n"
        cases = (
            ("largeRangeSuspendsAtFirstYield", "def mutate():\n    global alias\n    for item in range(1001):\n        alias = two\n        yield item\n    alias = three\niterator = mutate()\nnext(iterator)\n", "two"),
            ("enumerateKeywordStartSuspendsAtSecondYield", "def mutate():\n    global alias\n    for index, item in enumerate((one, two), start=7):\n        alias = item\n        yield index\n    alias = three\niterator = mutate()\nnext(iterator)\nnext(iterator)\n", "two"),
            ("dictComprehensionDuplicateKeywordRaises", "def mutate(value):\n    global alias\n    alias = three\noptions = {key: one for key in ('value',)}\ntry:\n    mutate(value=two, **options)\nexcept TypeError:\n    alias = two\n", "two"),
            ("subscriptWriteDuplicateKeywordRaises", "def mutate(value):\n    global alias\n    alias = three\noptions = {}\noptions['value'] = one\ntry:\n    mutate(value=two, **options)\nexcept TypeError:\n    alias = two\n", "two"),
            ("exitComparisonSuppressesException", "class C:\n    def __enter__(self):\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        global alias\n        alias = two\n        return 1 == 1\ntry:\n    with C():\n        raise RuntimeError\nexcept RuntimeError:\n    alias = three\n", "two"),
            ("exitDefaultTruthyInstanceSuppressesException", "class Marker:\n    pass\nclass C:\n    def __enter__(self):\n        return self\n    def __exit__(self, exc_type, exc, traceback):\n        global alias\n        alias = two\n        return Marker()\ntry:\n    with C():\n        raise RuntimeError\nexcept RuntimeError:\n    alias = three\n", "two"),
            ("indexedLambdaNewSkipsInitializer", "class C:\n    def __new__(cls):\n        return ((lambda: None),)[0]\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "one"),
            ("truthyLambdaNewSkipsInitializer", "class C:\n    def __new__(cls):\n        return (lambda: None) or object.__new__(cls)\n    def __init__(self):\n        global alias\n        alias = two\nC()\n", "one"),
            ("callableDecoratorLambdaReplacesOriginal", "def set_alias(value):\n    global alias\n    alias = value\nclass Decorator:\n    def __call__(self, function):\n        return lambda: set_alias(two)\n@Decorator()\ndef original():\n    set_alias(three)\noriginal()\n", "two"),
            ("raisingDecoratorInitializerRoutesTypeError", "class Decorator:\n    def __init__(self, function):\n        raise TypeError\n    def __call__(self):\n        global alias\n        alias = three\ntry:\n    @Decorator\n    def original():\n        pass\nexcept TypeError:\n    alias = two\n", "two"),
        )
        for name, body, target in cases:
            source = prefix + body + "alias()\n"
            (root / "main.py").write_text(source)
            graph = analyze_repository(root)
            names = {node["id"]: node["qualifiedName"] for node in graph["nodes"]}
            calls = {
                f"{names[edge['source']]} -> {names[edge['target']]}"
                for edge in graph["edges"] if edge["kind"] == "calls"
                and edge["range"]["start"]["line"] == len(source.splitlines())
            }
            rows[name] = calls == {f"main -> main.{target}"}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "pkg").mkdir()
        (root / "pkg.py").write_text("pass\n")
        (root / "pkg/__init__.py").write_text("")
        (root / "code-view.json").write_text(json.dumps({"startCommand": "python -m pkg"}))
        try:
            analyze_repository(root)
        except AnalysisError:
            rows["regularPackageWithoutMainRejectsSiblingModuleFallback"] = True
        else:
            rows["regularPackageWithoutMainRejectsSiblingModuleFallback"] = False
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "pkg").mkdir()
        (root / "main.py").write_text(
            "import pkg.mod\nimport pkg\nfrom pkg.mod import missing\npkg.f()\n"
            "alias = pkg.f\nalias()\n"
        )
        (root / "pkg/__init__.py").write_text("from .mod import f\n")
        (root / "pkg/mod.py").write_text("def f():\n    pass\n")
        graph = analyze_repository(root)
        imports = relationship_keys(graph, "imports")
        calls = relationship_keys(graph, "calls")
        rows["dottedImport"] = "main -> pkg.mod" in imports
        rows["missingInternalIsUnresolved"] = any(
            node["reason"] == "internal symbol not found" and node["range"]["start"]["line"] == 3
            for node in graph["nodes"] if node["unresolved"]
        )
        rows["reexportAndAlias"] = calls.count("main -> pkg.mod.f") == 2
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text(
            "import os as first, os as second\n"
            "class A:\n    pass\nclass B:\n    pass\n"
            "def dependency():\n    return 1\n"
            "def use(x: A | None, y: tuple[A, B] = dependency()) -> list[B]:\n    pass\n"
            "def duplicate():\n    return 1\ndef duplicate():\n    return 2\n"
        )
        (root / "conftest.py").write_text("def ignored():\n    pass\n")
        graph = analyze_repository(root)
        type_uses = relationship_keys(graph, "type_uses")
        rows["compoundAnnotations"] = all(
            relation in type_uses for relation in ("main.use -> main.A", "main.use -> main.B")
        )
        rows["defaultArgumentCall"] = "main -> main.dependency" in relationship_keys(graph, "calls")
        rows["testInfrastructureExcluded"] = "conftest.py" not in {node["path"] for node in graph["nodes"]}
        rows["exactOccurrences"] = (
            len([edge for edge in graph["edges"] if edge["kind"] == "imports"]) == 2
            and len([node for node in graph["nodes"] if node["qualifiedName"] == "main.duplicate"]) == 2
        )
        invalid = copy.deepcopy(graph)
        invalid["edges"][0]["target"] = "missing"
        try:
            validate_graph_document(invalid)
        except AnalysisError:
            rows["semanticIntegrityValidator"] = True
        else:
            rows["semanticIntegrityValidator"] = False
        mutations = []
        ordinary = next(node for node in graph["nodes"] if node["kind"] == "function")
        mutations.append(lambda value: value.update({"external": True, "reason": "outside"}))
        checks = []
        for mutate in mutations:
            invalid = copy.deepcopy(graph)
            mutate(next(node for node in invalid["nodes"] if node["id"] == ordinary["id"]))
            try:
                validate_graph_document(invalid)
            except AnalysisError:
                checks.append(True)
            else:
                checks.append(False)
        invalid = copy.deepcopy(graph)
        next(node for node in invalid["nodes"] if node["path"])["path"] = "../outside.py"
        try:
            validate_graph_document(invalid)
        except AnalysisError:
            checks.append(True)
        else:
            checks.append(False)
        invalid = copy.deepcopy(graph)
        invalid["project"]["entryReachableNodeIds"].append(invalid["project"]["entryNodeId"])
        try:
            validate_graph_document(invalid)
        except AnalysisError:
            checks.append(True)
        else:
            checks.append(False)
        rows["semanticIntegrityMutationTable"] = all(checks)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "main.py").write_text("\n".join(
            f"def f{i}():\n    import json\n    return json.dumps({i})" for i in range(40)
        ))
        expected = hashlib.sha256(json.dumps(analyze_repository(root), sort_keys=True).encode()).hexdigest()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            hashes = list(pool.map(
                lambda _: hashlib.sha256(json.dumps(analyze_repository(root), sort_keys=True).encode()).hexdigest(),
                range(40),
            ))
        rows["parallelDeterminism40Runs"] = set(hashes) == {expected}
    return {name: {"expected": True, "actual": value, "passed": value} for name, value in rows.items()}


def main():
    started = time.perf_counter()
    expected = json.loads((FIXTURE / "expected-graph.json").read_text())
    graph = analyze_repository(FIXTURE)
    evaluated_kinds = {item.split("|", 1)[0] for item in expected["nodes"]}
    actual_nodes = [f"{node['kind']}|{node['qualifiedName']}" for node in graph["nodes"] if node["kind"] in evaluated_kinds]
    canonical = json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()
    exact = hashlib.sha256(canonical).hexdigest() == expected["canonicalSha256"]
    boundaries = [node for node in graph["nodes"] if node["external"] or node["unresolved"]]
    unresolved = [node for node in graph["nodes"] if node["unresolved"]]
    metrics = {
        "nodes": score(expected["nodes"], actual_nodes),
        "calls": score(expected["calls"], relationship_keys(graph, "calls")),
        "imports": score(expected["imports"], relationship_keys(graph, "imports")),
        "constructs": score(expected["constructs"], relationship_keys(graph, "constructs")),
        "typeUses": score(expected["typeUses"], relationship_keys(graph, "type_uses")),
        "unresolvedHonesty": {
            "expected": len(expected["unresolved"]),
            "actual": len(unresolved),
            "allBoundariesExplainStop": bool(boundaries) and all(node["reason"] for node in boundaries),
            "noInternalGuess": exact,
        },
        "adversarial": adversarial_metrics(),
    }
    passed = exact and all(
        value[metric] == 1.0
        for name, value in metrics.items()
        if name not in {"unresolvedHonesty", "adversarial"}
        for metric in ("precision", "recall")
    ) and metrics["unresolvedHonesty"]["allBoundariesExplainStop"] and all(
        row["passed"] for row in metrics["adversarial"].values()
    )
    result = {
        "eval": "python-analyzer/tic-tac-toe-v1",
        "passed": passed,
        "exactGoldenGraph": exact,
        "metrics": metrics,
        "trace": {
            "elapsedMs": round((time.perf_counter() - started) * 1000, 3),
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
