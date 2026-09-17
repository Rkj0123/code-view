import { expect, test, type Page, type Route } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { analyzerFixture } from "../fixtures";
import { normalizeGraphPayload } from "../../src/apiContract";
import { buildVisibleGraph, defaultFilters } from "../../src/graphModel";

async function mockLiveHost(page: Page, tenFiles: boolean | "navigation" = false) {
  const { graph, sourceByPath } = analyzerFixture(tenFiles);
  const state = { generation: 7, state: "ready", lastError: null as string | null, command: "a.py", running: false, approved: 0, approvals: [] as number[], starts: 0 };
  const launch = () => ({ generation: state.generation, display: `python ${state.command}`, argv: ["python", state.command], cwd: "/repo", mode: "manual", allowLaunch: !state.lastError && state.state === "ready", running: state.running });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown;
    let status = 200;
    if (path === "/api/v1/graph") body = { generation: state.generation, projectName: "live-fixture", config: {}, comparison: null, graph };
    else if (path === "/api/v1/status") body = { generation: state.generation, state: state.state, lastError: state.lastError };
    else if (path === "/api/v1/git/refs") body = { hasHead: false, branches: [], recentCommits: [] };
    else if (path === "/api/v1/launch") body = launch();
    else if (path === "/api/v1/source") {
      const query = new URL(route.request().url()).searchParams;
      const sourcePath = query.get("path")!;
      const startLine = Number(query.get("startLine"));
      const endLine = Number(query.get("endLine"));
      body = { path: sourcePath, startLine, endLine, text: sourceByPath[sourcePath].split("\n").slice(startLine - 1, endLine).join("\n") };
    } else if (path === "/api/v1/git/diff") body = null;
    else if (path === "/api/v1/launch/approve") {
      const approval = route.request().postDataJSON();
      state.approvals.push(approval?.generation);
      if (approval?.schemaVersion !== "code-view.launch-approval/v2" || approval.generation !== state.generation) {
        status = 409;
        body = { error: "Command changed since it was displayed; close this dialog and review the current command." };
      } else { state.approved = approval.generation; body = { approved: true }; }
    } else if (path === "/api/v1/launch/start" && state.approved === state.generation) {
      state.approved = 0;
      state.running = true;
      state.starts += 1;
      body = launch();
    } else if (path === "/api/v1/launch/stop") { state.running = false; body = launch(); }
    else { status = 409; body = { error: "Unexpected or unapproved request" }; }
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/?token=local-eval");
  await expect(page.getByRole("application", { name: "Interactive repository dependency graph" })).toBeVisible();
  return Object.assign(state, { graph });
}

test("live failures remain visible in both themes without losing the usable graph", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  const state = await mockLiveHost(page);
  const originalCounts = (await page.locator(".canvas-status").textContent())!;
  state.lastError = "Unknown code-view.json field: unknown";
  await expect(page.getByRole("alert")).toContainText(state.lastError);
  await expect(page.locator(".generation")).toHaveAttribute("data-state", "error");
  await expect(page.locator(".canvas-status")).toHaveText(originalCounts);
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("alert")).toContainText(state.lastError);
  state.lastError = null;
  state.state = "indexing";
  await expect(page.locator(".generation")).toContainText("Indexing");
  await expect(page.getByRole("alert")).toHaveCount(0);
  state.state = "ready";
  state.generation = 8;
  await expect(page.locator(".generation")).toHaveAttribute("title", "Revision 8");
  await expect(page.locator(".canvas-status")).toHaveText(originalCounts);
  await page.getByRole("button", { name: /^Entry Focus/ }).click();
  await page.getByRole("button", { name: /^Repository/ }).click();
  await expect(page.getByRole("application", { name: /Interactive repository dependency graph/ })).toBeVisible();
  await expect(page.locator(".canvas-status")).toHaveText(originalCounts);
});

test("a stale dialog cannot approve a command that was never shown", async ({ page }) => {
  const state = await mockLiveHost(page);
  await page.getByRole("button", { name: "Run", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Run configured start command?" });
  await expect(dialog).toContainText('["python","a.py"]');
  state.command = "b.py";
  state.generation = 8;
  await expect(page.locator(".launch-command")).toContainText("python b.py");
  await expect(dialog).toContainText('["python","a.py"]');
  await expect(dialog).not.toContainText("b.py");
  await dialog.getByRole("button", { name: "Approve and run" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Command changed since it was displayed");
  expect(state.approvals).toEqual([7]);
  expect(state.starts).toBe(0);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).last().click();
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await expect(dialog).toContainText('["python","b.py"]');
  await dialog.getByRole("button", { name: "Approve and run" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  expect(state.approvals).toEqual([7, 8]);
  expect(state.starts).toBe(1);
  state.lastError = "Invalid replacement configuration";
  await expect(page.getByRole("alert")).toContainText(state.lastError);
  await expect(page.getByText("launch disabled", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toHaveCount(0);
  expect(state.running).toBe(false);
});

test("analyzer file and module names remain distinct command choices", async ({ page }) => {
  await mockLiveHost(page);
  await page.getByRole("button", { name: "Search symbols or commands" }).click();
  await page.getByRole("textbox", { name: "Search symbols or commands" }).fill("game");
  const dialog = page.getByRole("dialog", { name: "Search code and run commands" });
  await expect(dialog.getByRole("option", { name: "tictactoe.game module", exact: true })).toBeVisible();
  await dialog.getByRole("option", { name: "tictactoe/game.py file", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "Symbol details" })).toContainText("file:tictactoe/game.py");
});

test("every entity can be revealed and imported initialization can be traced beyond a value", async ({ page }) => {
  const sourceRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/source") sourceRequests.push(url.searchParams.get("path")!);
  });
  const state = await mockLiveHost(page, "navigation");
  const document = normalizeGraphPayload({ generation: 7, graph: state.graph });
  await expect(page.getByRole("button", { name: /^Repository/ })).toHaveAttribute("data-active", "true");
  const choose = async (node: typeof document.nodes[number]) => {
    await page.getByRole("button", { name: "Search symbols or commands", exact: true }).click();
    await page.getByRole("textbox", { name: "Search symbols or commands" }).fill(node.qualifiedName);
    const duplicates = document.nodes.filter((item) => item.kind === node.kind && item.label === node.label).length > 1;
    const option = duplicates
      ? page.getByRole("option").filter({ has: page.getByText(`${node.qualifiedName}${node.source ? ` · ${node.source.path}:${node.source.start.line}` : ""}`, { exact: true }) })
      : page.getByRole("option", { name: `${node.label} ${node.kind}`, exact: true });
    await option.click();
    await expect(page.getByRole("complementary", { name: "Symbol details" }).locator(".qualified-name")).toHaveText(node.qualifiedName);
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export graph JSON" }).click();
    const exported = JSON.parse(readFileSync((await (await downloadPromise).path())!, "utf8"));
    expect(exported.nodes.map((item: { id: string }) => item.id)).toContain(node.id);
    if (node.source) await expect(page.locator(".source-view__path")).toContainText(node.source.path);
  };
  await page.getByRole("button", { name: /^Entry Focus/ }).click();
  const file = document.nodes.find((node) => node.kind === "file" && node.label === "api/__init__.py")!;
  await choose(file);
  const inspector = page.getByRole("complementary", { name: "Symbol details" });
  await expect(inspector.locator(".flow-path")).toContainText(["main.py"]);
  await expect(inspector.locator("details.members")).not.toHaveAttribute("open", "");
  await inspector.locator("details.members summary").click();
  await expect(inspector.getByRole("region", { name: "Contained members" })).toBeVisible();
  await inspector.locator("details.members summary").click();
  await inspector.getByTitle("Inspect imports relationship with api.routes.search.$local.router", { exact: true }).click();
  const relationship = page.getByRole("complementary", { name: "Relationship details" });
  await expect(relationship).toContainText("imports relationship");
  await relationship.getByRole("button", { name: "api.routes.search.$local.router", exact: true }).click();
  await expect(page.locator(".source-view__path")).toContainText("api/routes/search.py");
  await inspector.getByRole("button", { name: "In api.routes.search module", exact: true }).click();
  await expect(inspector.locator(".qualified-name")).toHaveText("api.routes.search");
  for (const kind of ["directory", "package", "file", "module", "class", "function", "method", "variable"] as const) {
    const node = document.nodes.find((item) => item.kind === kind && item.source?.path !== "independent.py")!;
    await choose(node);
  }
  await page.getByRole("button", { name: /^Entry Focus/ }).click();
  await choose(document.nodes.find((node) => node.qualifiedName === "independent.outside")!);
  await expect(page.getByRole("button", { name: /^Repository/ })).toHaveAttribute("data-active", "true");
  await inspector.getByTitle("Inspect calls relationship with independent.outside", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Selected flow", exact: true })).toHaveAttribute("aria-pressed", "true");
  const recursiveDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export graph JSON" }).click();
  const recursiveGraph = JSON.parse(readFileSync((await (await recursiveDownload).path())!, "utf8"));
  expect(recursiveGraph.edges).toHaveLength(1);
  expect(recursiveGraph.edges[0].source).toBe(recursiveGraph.edges[0].target);
  await choose(document.nodes.find((node) => node.kind === "module" && node.qualifiedName === "api.service")!);
  await inspector.getByTitle("Inspect api calls relationship with json.loads", { exact: true }).click();
  const trace = page.getByRole("combobox", { name: "Select any relationship in the filtered graph" });
  const exactExternalEdge = await trace.inputValue();
  expect(exactExternalEdge).not.toBe("");
  await trace.selectOption(exactExternalEdge);
  await expect(trace).toHaveValue(exactExternalEdge);
  await expect(page.getByRole("complementary", { name: "Relationship details" })).toContainText("json.loads");
  await choose(document.nodes.find((node) => node.qualifiedName === "api.create_app.$local.app")!);
  expect(sourceRequests.every((path) => path.endsWith(".py"))).toBe(true);
  await choose(file);
  await expect(inspector.getByTitle("Inspect imports relationship with api.routes.search.$local.router", { exact: true })).toBeVisible();
  mkdirSync("/tmp/code-view-navigation/critique", { recursive: true });
  await page.screenshot({ path: "/tmp/code-view-navigation/critique/navigation-browser.png", animations: "disabled" });
});

test("relationship Inspector keeps aggregate evidence selected as occurrences change", async ({ page }) => {
  const state = await mockLiveHost(page);
  const document = normalizeGraphPayload({ generation: 7, graph: state.graph });
  const visible = buildVisibleGraph(document, defaultFilters(), "repository", new Set());
  const call = visible.edges.find((edge) => edge.kind === "calls" && edge.count === 1)!;
  const raw = state.graph.edges.find((edge: { kind: string; source: string; target: string }) => edge.kind === "calls" && edge.source === call.source && edge.target === call.target)!;
  const selector = page.getByRole("combobox", { name: "Select any relationship in the filtered graph" });
  await selector.selectOption(call.id);
  const inspector = page.getByRole("complementary", { name: "Relationship details" });
  await expect(inspector).toContainText("1 occurrence");
  state.graph.edges.push({ ...raw, id: "duplicate-inspector-call" });
  state.generation = 8;
  await expect(page.locator(".generation")).toHaveAttribute("title", "Revision 8");
  await expect(inspector).toContainText("2 occurrences");
  await expect(selector).toHaveValue(call.id);
  state.graph.edges.pop();
  state.generation = 9;
  await expect(page.locator(".generation")).toHaveAttribute("title", "Revision 9");
  await expect(inspector).toContainText("1 occurrence");
  await expect(selector).toHaveValue(call.id);
});

for (const tenFiles of [false, true]) test(`${tenFiles ? "ten-file" : "real Python"} flow preserves exact edges, readable endpoints, and scoped export`, async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => { if (["error", "warning"].includes(message.type())) errors.push(message.text()); });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.emulateMedia({ colorScheme: "light" });
  const state = await mockLiveHost(page, tenFiles);
  await page.setViewportSize({ width: 1280, height: 720 });
  const document = normalizeGraphPayload({ generation: 7, graph: state.graph });
  const expected = buildVisibleGraph(document, defaultFilters(), "repository", new Set());
  if (tenFiles) expect(expected.nodes.filter((node) => node.kind === "file")).toHaveLength(10);
  const counts = (await page.locator(".canvas-status").textContent())!;
  const selector = page.getByRole("combobox", { name: "Select any relationship in the filtered graph" });
  await expect(selector.locator("option")).toHaveCount(expected.edges.length + 1);
  await page.getByRole("button", { name: "Search symbols or commands" }).click();
  await page.getByRole("textbox", { name: "Search symbols or commands" }).fill(tenFiles ? "main.main" : "Game.play");
  await page.getByRole("option", { name: tenFiles ? "main function" : "play method", exact: true }).click();
  await expect(page.getByRole("button", { name: "Selected flow", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("complementary", { name: "Symbol details" })).toContainText(tenFiles ? "main.main" : "tictactoe.game.Game.play");
  await expect(page.locator(".source-view pre")).toContainText(tenFiles ? "run_9()" : "self.board.place");
  const evidenceDirectory = "/tmp/code-view-revamp/critique";
  mkdirSync(evidenceDirectory, { recursive: true });
  await expect(page.locator(".canvas-stage")).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await page.screenshot({ path: `${evidenceDirectory}/${tenFiles ? "ten-files" : "python"}-flow-light.png`, animations: "disabled" });
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(page.locator(".canvas-stage")).toHaveCSS("background-color", "rgb(12, 16, 20)");
  await page.screenshot({ path: `${evidenceDirectory}/${tenFiles ? "ten-files" : "python"}-flow-dark.png`, animations: "disabled" });
  for (const edge of expected.edges.filter((edge) => edge.kind === "calls").slice(0, 3)) {
    await selector.selectOption(edge.id);
    await expect(page.getByRole("complementary", { name: "Relationship details" })).toContainText("occurrence");
    await expect(page.locator(".canvas-status")).toContainText(`1 relationships of ${expected.edges.length}`);
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export graph JSON" }).click();
    const download = await downloadPromise;
    const exported = JSON.parse(readFileSync((await download.path())!, "utf8"));
    expect(exported.edges).toEqual([edge]);
    const ids = new Set(exported.nodes.map((node: { id: string }) => node.id));
    expect(exported.nodes.every((node: { parent?: string }) => !node.parent || ids.has(node.parent))).toBe(true);
  }
  await page.getByRole("button", { name: "All connections", exact: true }).click();
  await expect(page.locator(".canvas-status")).toHaveText(counts);
  await page.screenshot({ path: `${evidenceDirectory}/${tenFiles ? "ten-files" : "python"}-all-dark.png`, animations: "disabled" });
  expect(errors).toEqual([]);
});

test("delayed occurrence evidence cannot replace a newly selected relationship", async ({ page }) => {
  const state = await mockLiveHost(page, true);
  const document = normalizeGraphPayload({ generation: 7, graph: state.graph });
  const edges = document.edges.filter((edge) => edge.kind === "calls" && edge.locations?.length);
  const first = edges[0];
  const second = edges.find((edge) => edge.locations![0].path !== first.locations![0].path)!;
  const displayed = buildVisibleGraph(document, defaultFilters(), "repository", new Set()).edges;
  const displayedId = (edge: typeof first) => displayed.find((item) => item.kind === edge.kind && item.source === edge.source && item.target === edge.target)!.id;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let captured!: (route: Route) => void;
  const request = new Promise<Route>((resolve) => { captured = resolve; });
  await page.route("**/api/v1/source?**", async (route) => {
    if (new URL(route.request().url()).searchParams.get("path") !== first.locations![0].path) return route.fallback();
    captured(route);
    await pending;
    const range = first.locations![0];
    await route.fulfill({ json: { path: range.path, startLine: range.start.line, endLine: range.end.line, text: "STALE_OCCURRENCE_EVIDENCE" } });
  });
  const selector = page.getByRole("combobox", { name: "Select any relationship in the filtered graph" });
  await selector.selectOption(displayedId(first));
  await page.locator(".occurrence-row > button").first().click();
  await request;
  await selector.selectOption(displayedId(second));
  await page.locator(".occurrence-row > button").first().click();
  await expect(page.locator(".source-view__path")).toContainText(second.locations![0].path);
  const response = page.waitForResponse((item) => item.url().includes("/api/v1/source?") && new URL(item.url()).searchParams.get("path") === first.locations![0].path);
  release();
  await response;
  await expect(page.locator(".source-view__path")).toContainText(second.locations![0].path);
  await expect(page.getByRole("complementary", { name: "Relationship details" })).not.toContainText("STALE_OCCURRENCE_EVIDENCE");
});

test("mobile code-flow workflow stays local, reachable, and motion-safe", async ({ page }) => {
  const consoleErrors: string[] = [];
  const externalRequests: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("request", (request) => {
    if (!request.url().startsWith("http://127.0.0.1:41871/")) externalRequests.push(request.url());
  });

  await page.goto("/");
  await page.evaluate(() => localStorage.setItem("code-view.theme.v1", JSON.stringify("dark")));
  await page.reload();
  await expect(page.getByRole("application", { name: "Interactive repository dependency graph" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(page.locator(".responsive-panel--left")).toHaveAttribute("inert", "");
  await expect(page.locator(".responsive-panel--right")).toHaveAttribute("inert", "");
  await page.getByRole("button", { name: "Overview" }).focus();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => Boolean(document.activeElement?.closest(".responsive-panel")))).toBe(false);

  for (const name of ["Switch to light mode", "Refresh generated graph", "Toggle graph filters", "Toggle selection details"]) {
    const box = await page.getByRole("button", { name }).boundingBox();
    expect(box, `${name} must be visible`).not.toBeNull();
    expect(box!.x, `${name} must not overflow left`).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width, `${name} must not overflow right`).toBeLessThanOrEqual(390);
  }

  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute("content", "#f3f6f7");
  await expect(page.locator(".canvas-stage")).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await page.reload();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.getByRole("button", { name: "Switch to dark mode" })).toBeVisible();
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute("content", "#0c1014");

  await page.getByRole("button", { name: "Toggle graph filters" }).click();
  await expect(page.locator(".responsive-panel--left")).not.toHaveAttribute("inert", "");
  await expect(page.getByRole("complementary", { name: "Graph filters" })).toBeVisible();
  await page.keyboard.press("2");
  await expect(page.locator(".canvas-status")).toContainText("9 nodes");
  await page.getByRole("checkbox", { name: "calls", exact: true }).uncheck();
  await expect(page.locator(".canvas-status")).toContainText("9 nodes");
  await page.getByRole("checkbox", { name: "calls", exact: true }).press("Escape");
  await expect(page.locator(".responsive-panel--left")).toHaveAttribute("inert", "");
  await expect(page.getByRole("button", { name: "Toggle graph filters" })).toBeFocused();
  await page.keyboard.press("1");
  await expect(page.locator(".canvas-status")).toContainText("15 nodes");
  await expect(page.getByRole("application", { name: "Interactive repository dependency graph" })).toBeVisible();
  await page.getByRole("button", { name: "Toggle selection details" }).click();
  await expect(page.locator(".responsive-panel--right")).not.toHaveAttribute("inert", "");
  await expect(page.getByRole("complementary", { name: "Selection details" })).toBeVisible();

  await page.getByRole("button", { name: "Search symbols or commands" }).click();
  await page.getByRole("textbox", { name: "Search symbols or commands" }).fill("game.py");
  await page.getByRole("dialog", { name: "Search code and run commands" }).getByRole("option", { name: "game.py file", exact: true }).click();
  const graph = page.getByRole("application", { name: /Interactive repository dependency graph/ });
  await graph.focus();
  await graph.press("Enter");
  await page.getByRole("button", { name: "All connections", exact: true }).click();
  await expect(page.locator(".canvas-status")).toContainText("12 nodes");
  const flowCounts = await page.locator(".flow-heading b").evaluateAll((items) => items.map((item) => Number(item.textContent)));
  expect(flowCounts.reduce((sum, value) => sum + value, 0)).toBeGreaterThan(0);
  await graph.press("e");
  await expect(page.getByRole("complementary", { name: "Relationship details" })).toContainText("occurrence");

  await page.getByRole("button", { name: "Search symbols or commands" }).click();
  await page.getByRole("textbox", { name: "Search symbols or commands" }).fill("Game.play");
  await page.getByRole("dialog", { name: "Search code and run commands" }).getByRole("option", { name: "play method", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "Symbol details" })).toContainText("src.game.Game.play");
  await page.getByRole("button", { name: "Toggle graph filters" }).click();
  await expect(page.getByRole("complementary", { name: "Graph filters" })).toBeVisible();
  await expect(page.locator(".responsive-panel--right")).not.toHaveClass(/is-open/);
  await page.getByRole("checkbox", { name: "calls", exact: true }).check();
  await page.getByRole("button", { name: "Toggle selection details" }).click();
  await expect(page.locator(".responsive-panel--left")).not.toHaveClass(/is-open/);
  await expect(page.locator(".responsive-panel--right")).toHaveClass(/is-open/);
  await expect(page.getByRole("complementary", { name: "Symbol details" })).toBeVisible();
  await page.getByRole("button", { name: "Toggle selection details" }).click();

  await graph.focus();
  await graph.press("ArrowRight");
  await page.getByRole("button", { name: "Toggle selection details" }).click();
  await page.getByRole("button", { name: "Reset graph view" }).click();
  const durations = await page.locator(".responsive-panel").first().evaluate((element) => getComputedStyle(element).transitionDuration.split(",").map(Number.parseFloat));
  expect(Math.max(...durations)).toBeLessThanOrEqual(0.001);
  expect(externalRequests).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
