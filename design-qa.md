# Code View design QA

**Findings**

No actionable P0, P1, or P2 findings remain.

## Evidence

- Source visual truth: `docs/readme/user_interface.png`, 2258 × 1354 px. It is the original Sourcetrail macOS/C++ screen.
- Rendered implementation: `/tmp/code-view-revamp/critique/python-flow-light.png` and `/tmp/code-view-revamp/critique/ten-files-flow-light.png`, each 1280 × 720 px at a 1280 × 720 CSS viewport and device scale 1. Dark captures with the same size are beside them.
- State: `Game.play` and generated ten-file `main.main`, Selected flow, read-only source open, light and dark themes. Overview captures cover All connections.
- Normalization: compared the graph/source content regions, not old macOS window chrome. The source and implementation have different aspect ratios and languages, so no pixel-distance score was used.
- Full-view comparison: the reference and all six current screenshots were opened together. The final three-column composition preserves the useful Sourcetrail pattern: callers left, selection centered, dependencies right, ownership boundaries around symbol rows, and source on the right.
- Focused-region comparison: labels, arrow endpoints, file/class headers, source locations, status counts, and graph controls were readable in the full-size captures, so a separate crop was unnecessary. A live check loaded `tictactoe/cli.py:9:12` for `run → Game` and showed `game = Game()`.

## Required fidelity surfaces

- Fonts and typography: native sans-serif controls plus monospaced code/symbol labels preserve Sourcetrail's functional hierarchy. Small lane labels, file headers, symbols, source, and status remain legible at the tested viewport.
- Spacing and layout rhythm: compact rectangular boundaries, clear column gutters, reserved top/bottom controls, and stable source-panel width. The ten-file trace extends below the viewport by design and remains reachable with pan/zoom; it is not shrunk until labels become unreadable.
- Colors and visual tokens: quiet gray boundaries, amber calls/functions, blue variables/inbound flow, white light canvas, and near-black dark canvas. Direction does not rely on color alone; lanes, arrowheads, labels, and the Inspector repeat it.
- Image quality and asset fidelity: the canvas is code-native vector UI with installed Lucide icons, as appropriate for an interactive code graph. No source product imagery or branding asset was replaced. PNG exports and captured lines/text are sharp at device scale 1.
- Copy and content: terse local-tool copy identifies repository/entry scope, Selected flow versus All connections, exact relationship counts, static-analysis limits, read-only state, and command risk without exposing implementation jargon.

## Interaction and runtime checks

Search, symbol selection, exact relationship selection, occurrence loading, keyboard navigation, filters, repository/entry switching, themes, responsive panels, JSON/PNG export, bookmarks, saved views, launch approval, Stop, refresh failures, and reduced motion have deterministic or browser coverage. The rebuilt fixture loaded with generation 1 and 62 indexed nodes. Its browser console had no warnings or errors.

## Comparison history

1. Initial layouts nested function groups and produced dense diagonal/compound routes. Replaced them with deterministic rows and Cytoscape's native orthogonal taxi routing.
2. First focused pass allowed connectors to attach to overlapping group containers and crashed on scope replacement. Introduced leaf endpoints and atomic element replacement; tests assert that compound parents have zero incident edges.
3. First ten-file compact pass put file and symbol labels on one horizontal row, so the connection bus crossed file names. File headers now sit above their symbol rows. The final light/dark captures show clear text and arrow endpoints.
4. A generic inset rule overrode the flow canvas reservation, and early dark screenshots captured a theme transition. Increased selector specificity and waited for exact settled backgrounds before recapture.
5. A late occurrence request could replace the newly selected relationship's source. Selection/generation/request guards plus unit, browser, and mutation checks now prevent it.

**Open Questions**

None for the approved Python-first, localhost-only scope. Arbitrary huge All connections layouts remain exploratory pan/zoom views; Selected flow is the readability path.

**Implementation Checklist**

- [x] Reference and rendered captures compared together.
- [x] Light, dark, ten-file, Python, overview, and focused states checked.
- [x] Core interactions, console output, exact evidence, responsive layout, and reduced motion checked.
- [x] P0/P1/P2 findings fixed and recaptured.

**Follow-up Polish**

None required for handoff.

final result: passed
