import { test, expect } from "@playwright/test";

test("overview follows the handoff and uses real run filters", async ({ page }, info) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "Run queue" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Needs attention" })).toBeVisible();
  await page.getByLabel("Trend period").selectOption("30");
  await expect(page.getByLabel("Trend period")).toHaveValue("30");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/overview-${info.project.name}-light.png`, fullPage: true });
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({ path: `test-results/overview-${info.project.name}-dark.png`, fullPage: true });
  await page.getByRole("link", { name: /Queued ↗/ }).click();
  await expect(page).toHaveURL(/runs\?status=queued/);
});

test("search and run dialog support keyboard operation", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: "Search AutoBench" })).toBeVisible();
  await page.getByLabel("Search runs, tasks and pages").fill("Budget settings");
  await page.getByRole("link", { name: /Budget settings/ }).click();
  await expect(page).toHaveURL(/settings\/budgets/);
  await expect(page.getByRole("heading", { name: "Weekly budget" })).toBeVisible();
  await page.goto("/");
  await page.getByRole("button", { name: "New run", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Launch a new run" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("core pages load without browser errors or horizontal page overflow", async ({ page }, info) => {
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  for (const route of ["/runs", "/tasks", "/suites", "/compare", "/leaderboard", "/agents", "/settings", "/settings/providers", "/settings/budgets", "/settings/keys", "/settings/members", "/settings/audit", "/ops"]) {
    await page.goto(route);
    await expect(page.locator("main h1")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${route} should fit ${info.project.name}`).toBe(true);
  }
  expect(errors).toEqual([]);
});

test("offline evaluation launches and exposes trial evidence", async ({ page }, info) => {
  test.setTimeout(120_000);
  test.skip(info.project.name !== "desktop", "One real offline evaluation is sufficient.");
  await page.goto("/"); await page.getByRole("button", { name: "New run", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Suite", { exact: true }).selectOption("core-reference");
  await dialog.getByLabel("Agent", { exact: true }).selectOption("oracle");
  await dialog.getByLabel("Model", { exact: true }).selectOption("mock/mock-1");
  await dialog.getByLabel("Label (optional)").fill("UI acceptance · offline oracle");
  await dialog.getByRole("button", { name: "Launch", exact: true }).click();
  await expect(page).toHaveURL(/runs\/[a-f0-9-]+$/, { timeout: 15_000 });
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 90_000 });
  const runId = new URL(page.url()).pathname.split("/").at(-1)!;
  const detail = await (await page.request.get(`/api/runs/${runId}`)).json();
  expect(detail.metrics.pass_at_1.point).toBe(1);
  expect(detail.metrics.infra_errors).toBe(0);
  await page.screenshot({ path: "test-results/run-desktop.png", fullPage: true });
  await page.getByRole("button", { name: /hello-world.*attempt 0/i }).click();
  await expect(page.getByRole("dialog", { name: "Trial inspector" })).toBeVisible();
  await page.getByRole("link", { name: "Open full page" }).click();
  await expect(page).toHaveURL(/trials\//);
  await expect(page.getByRole("heading", { name: "Verdict", exact: true })).toBeVisible();
  await expect(page.getByText("greeting_written")).toBeVisible();
  const trialId = new URL(page.url()).pathname.split("/").at(-1)!;
  const trial = await (await page.request.get(`/api/trials/${trialId}`)).json();
  await expect(page.getByText(trial.seed_exact, { exact: true })).toBeVisible();
  const replay = await (await page.request.get(`/api/trials/${trialId}/replay`)).json();
  expect(replay.commands.some((command: string) => command.includes(`AUTOBENCH_SEED=${trial.seed_exact}`))).toBe(true);
  await page.screenshot({ path: "test-results/trial-desktop.png", fullPage: true });
});

test("API proxy accepts same-origin requests and rejects cross-origin mutations", async ({ request, baseURL }) => {
  const origin = new URL(baseURL!).origin;
  const invalidRun = await request.post("/api/runs", { headers: { origin }, data: {} });
  expect(invalidRun.status()).toBe(422);
  const crossOrigin = await request.post("/api/runs", { headers: { origin: "https://unrelated.example" }, data: {} });
  expect(crossOrigin.status()).toBe(403);
  const invalidSignIn = await request.post("/api/session", { headers: { origin }, data: {} });
  expect(invalidSignIn.status()).toBe(400);
  const crossOriginSignIn = await request.post("/api/session", { headers: { origin: "https://unrelated.example" }, data: {} });
  expect(crossOriginSignIn.status()).toBe(403);
  const crossOriginMember = await request.patch("/api/auth/members/00000000-0000-0000-0000-000000000000", { headers: { origin: "https://unrelated.example" }, data: { role: "admin", active: true } });
  expect(crossOriginMember.status()).toBe(403);
});

test("configured Google sign-in explains workspace access", async ({ page }) => {
  await page.route("**/api/auth/options", route => route.fulfill({ json: { google_enabled: true } }));
  await page.goto("/login");
  await expect(page.getByRole("link", { name: "Continue with Google Workspace" })).toHaveAttribute("href", "/api/auth/google/start");
  await expect(page.getByText(/New members receive viewer access/)).toBeVisible();
  await page.getByText("Administrator or integration API key", { exact: true }).click();
  await expect(page.getByLabel("API key", { exact: true })).toBeVisible();
});
