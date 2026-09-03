import type { GraphDocument, GraphEdge, GraphNode } from "./types";

const nodes: GraphNode[] = [
  { id: "dir:src", kind: "directory", label: "src", qualifiedName: "src", resolution: "resolved", test: false, diff: "unchanged" },
  { id: "file:src/main.py", kind: "file", label: "main.py", qualifiedName: "src/main.py", parent: "dir:src", resolution: "resolved", test: false, diff: "unchanged" },
  { id: "py:module:src.main", kind: "module", label: "src.main", qualifiedName: "src.main", parent: "file:src/main.py", resolution: "resolved", test: false, diff: "unchanged" },
  { id: "py:function:src.main.main", kind: "function", label: "main", qualifiedName: "src.main.main", parent: "py:module:src.main", resolution: "resolved", test: false, entry: true, diff: "modified", signature: "def main() -> None", summary: "Creates a game and drives the command-line loop.", source: { path: "src/main.py", start: { line: 5, column: 1 }, end: { line: 10, column: 18 } }, sourceText: "def main() -> None:\n    game = Game()\n    while not game.finished:\n        game.play(read_move())\n\nif __name__ == \"__main__\":\n    main()" },
  { id: "file:src/game.py", kind: "file", label: "game.py", qualifiedName: "src/game.py", parent: "dir:src", resolution: "resolved", test: false, diff: "modified" },
  { id: "py:module:src.game", kind: "module", label: "src.game", qualifiedName: "src.game", parent: "file:src/game.py", resolution: "resolved", test: false, diff: "unchanged" },
  { id: "py:class:src.game.Game", kind: "class", label: "Game", qualifiedName: "src.game.Game", parent: "py:module:src.game", resolution: "resolved", test: false, diff: "modified", signature: "class Game", summary: "Coordinates board state, turns, and winner detection.", source: { path: "src/game.py", start: { line: 6, column: 1 }, end: { line: 25, column: 20 } }, sourceText: "class Game:\n    def __init__(self) -> None:\n        self.board = Board()\n\n    def play(self, move: Move) -> None:\n        self.board.place(move)\n        self.finished = winner(self.board) is not None" },
  { id: "py:method:src.game.Game.__init__", kind: "method", label: "__init__", qualifiedName: "src.game.Game.__init__", parent: "py:class:src.game.Game", resolution: "resolved", test: false, diff: "unchanged", signature: "def __init__(self) -> None" },
  { id: "py:method:src.game.Game.play", kind: "method", label: "play", qualifiedName: "src.game.Game.play", parent: "py:class:src.game.Game", resolution: "resolved", test: false, diff: "modified", signature: "def play(self, move: Move) -> None", summary: "Applies a move and updates the game completion state.", source: { path: "src/game.py", start: { line: 10, column: 5 }, end: { line: 13, column: 64 } }, sourceText: "def play(self, move: Move) -> None:\n    self.board.place(move)\n    self.finished = winner(self.board) is not None" },
  { id: "file:src/rules.py", kind: "file", label: "rules.py", qualifiedName: "src/rules.py", parent: "dir:src", resolution: "resolved", test: false, diff: "unchanged" },
  { id: "py:module:src.rules", kind: "module", label: "src.rules", qualifiedName: "src.rules", parent: "file:src/rules.py", resolution: "resolved", test: false, diff: "unchanged" },
  { id: "py:function:src.rules.winner", kind: "function", label: "winner", qualifiedName: "src.rules.winner", parent: "py:module:src.rules", resolution: "resolved", test: false, diff: "unchanged", signature: "def winner(board: Board) -> Mark | None", summary: "Returns the winning mark when a complete line exists.", source: { path: "src/rules.py", start: { line: 8, column: 1 }, end: { line: 14, column: 16 } }, sourceText: "def winner(board: Board) -> Mark | None:\n    for line in WINNING_LINES:\n        marks = {board.cells[index] for index in line}\n        if len(marks) == 1 and None not in marks:\n            return marks.pop()\n    return None" },
  { id: "file:src/renderer.py", kind: "file", label: "renderer.py", qualifiedName: "src/renderer.py", parent: "dir:src", resolution: "resolved", test: false, diff: "added" },
  { id: "py:module:src.renderer", kind: "module", label: "src.renderer", qualifiedName: "src.renderer", parent: "file:src/renderer.py", resolution: "resolved", test: false, diff: "added" },
  { id: "py:class:src.renderer.Renderer", kind: "class", label: "Renderer", qualifiedName: "src.renderer.Renderer", parent: "py:module:src.renderer", resolution: "resolved", test: false, diff: "added" },
  { id: "py:method:src.renderer.Renderer.render", kind: "method", label: "render", qualifiedName: "src.renderer.Renderer.render", parent: "py:class:src.renderer.Renderer", resolution: "resolved", test: false, diff: "added" },
  { id: "py:class:src.renderer.ConsoleRenderer", kind: "class", label: "ConsoleRenderer", qualifiedName: "src.renderer.ConsoleRenderer", parent: "py:module:src.renderer", resolution: "resolved", test: false, diff: "added" },
  { id: "py:method:src.renderer.ConsoleRenderer.render", kind: "method", label: "render", qualifiedName: "src.renderer.ConsoleRenderer.render", parent: "py:class:src.renderer.ConsoleRenderer", resolution: "resolved", test: false, diff: "added" },
  { id: "external:rich", kind: "external_package", label: "rich", qualifiedName: "rich", resolution: "external", test: false, diff: "unchanged", summary: "Third-party package, collapsed by configuration.", boundaryReason: "Collapsed at the configured external-package boundary." },
  { id: "unresolved:src.main.plugin.load", kind: "unresolved", label: "plugin.load", qualifiedName: "src.main.plugin.load", resolution: "unresolved", test: false, diff: "unchanged", summary: "Static analysis could not resolve this runtime plugin call.", boundaryReason: "Dynamic plugin name is computed at runtime." },
  { id: "dir:tests", kind: "directory", label: "tests", qualifiedName: "tests", resolution: "resolved", test: true, diff: "unchanged" },
  { id: "file:tests/test_rules.py", kind: "file", label: "test_rules.py", qualifiedName: "tests/test_rules.py", parent: "dir:tests", resolution: "resolved", test: true, diff: "unchanged" },
  { id: "py:function:tests.test_winner", kind: "function", label: "test_winner", qualifiedName: "tests.test_rules.test_winner", parent: "file:tests/test_rules.py", resolution: "resolved", test: true, diff: "unchanged" },
];

const edge = (kind: GraphEdge["kind"], source: string, target: string, diff: GraphEdge["diff"] = "unchanged", resolution: GraphEdge["resolution"] = "resolved"): GraphEdge => {
  const sourceRange = nodes.find((node) => node.id === source)?.source;
  return {
    id: `${kind}|${source}|${target}`,
    kind,
    source,
    target,
    resolution,
    confidence: resolution === "unresolved" ? "unresolved" : "exact",
    count: 1,
    diff,
    locations: sourceRange ? [sourceRange] : [],
    boundaryReason: resolution === "unresolved" ? "Dynamic dispatch target could not be proven statically." : resolution === "external" ? "Target is outside the indexed repository." : undefined,
  };
};

const edges: GraphEdge[] = [
  edge("contains", "py:module:src.main", "py:function:src.main.main"),
  edge("contains", "py:module:src.game", "py:class:src.game.Game"),
  edge("contains", "py:class:src.game.Game", "py:method:src.game.Game.__init__"),
  edge("contains", "py:class:src.game.Game", "py:method:src.game.Game.play"),
  edge("contains", "py:module:src.rules", "py:function:src.rules.winner"),
  edge("contains", "py:module:src.renderer", "py:class:src.renderer.Renderer", "added"),
  edge("contains", "py:module:src.renderer", "py:class:src.renderer.ConsoleRenderer", "added"),
  edge("contains", "py:class:src.renderer.Renderer", "py:method:src.renderer.Renderer.render", "added"),
  edge("contains", "py:class:src.renderer.ConsoleRenderer", "py:method:src.renderer.ConsoleRenderer.render", "added"),
  edge("imports", "py:module:src.main", "py:module:src.game"),
  edge("imports", "py:module:src.game", "py:module:src.rules"),
  edge("imports", "py:module:src.renderer", "external:rich", "added", "external"),
  edge("constructs", "py:function:src.main.main", "py:class:src.game.Game", "modified"),
  edge("calls", "py:function:src.main.main", "py:method:src.game.Game.play", "modified"),
  edge("calls", "py:method:src.game.Game.play", "py:function:src.rules.winner"),
  edge("calls", "py:function:src.main.main", "unresolved:src.main.plugin.load", "unchanged", "unresolved"),
  edge("inherits", "py:class:src.renderer.ConsoleRenderer", "py:class:src.renderer.Renderer", "added"),
  edge("decorates", "py:method:src.renderer.ConsoleRenderer.render", "py:method:src.renderer.Renderer.render", "added"),
  edge("type_uses", "py:method:src.game.Game.play", "py:class:src.game.Game"),
  edge("reads", "py:function:src.rules.winner", "py:class:src.game.Game"),
  edge("writes", "py:method:src.game.Game.play", "py:class:src.game.Game"),
  edge("api_calls", "py:method:src.renderer.ConsoleRenderer.render", "external:rich", "added", "external"),
  edge("calls", "py:function:tests.test_winner", "py:function:src.rules.winner"),
  edge("test_covers", "py:function:tests.test_winner", "py:function:src.rules.winner"),
];

export const mockGraph: GraphDocument = {
  schemaVersion: 1,
  project: "tic-tac-toe",
  snapshot: "WORKTREE vs HEAD",
  revision: 42,
  generatedAt: "2026-08-23T10:42:00.000Z",
  entryKey: "py:function:src.main.main",
  preferences: {
    initialView: "repository",
    showModules: false,
    includeTests: false,
    relationships: ["contains", "imports", "calls", "inherits", "constructs", "reads", "writes", "decorates", "type_uses", "api_calls"],
    gitBase: "HEAD",
  },
  nodes,
  edges,
  diagnostics: [{ severity: "warning", message: "plugin.load remains unresolved", path: "src/main.py" }],
};
