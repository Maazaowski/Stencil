import { test, expect } from "@playwright/test";
import { collectErrors, unnamedControls } from "./helpers";

/**
 * Every primary destination must render its own H1 and log no console errors.
 * This is the cheapest possible guard against a page that 500s, renders blank,
 * or throws during hydration — none of which the type checker catches.
 */
const ROUTES: Array<{ path: string; heading: RegExp }> = [
  // "/" redirects to the queue: the operator lands on work, not on metrics.
  { path: "/", heading: /work/i },
  { path: "/upload", heading: /upload/i },
  { path: "/invoices", heading: /work/i },
  { path: "/insights", heading: /insights/i },
  // Nav says "Exceptions"/"Logs"; the pages title themselves differently.
  { path: "/exceptions", heading: /exception queue/i },
  // The page is titled "Suppliers" — the route keeps its old name.
  { path: "/profiles", heading: /suppliers/i },
  { path: "/accounts", heading: /accounts/i },
  { path: "/field-schemas", heading: /field schemas/i },
  { path: "/output-specs", heading: /output specs/i },
  { path: "/models", heading: /models/i },
  { path: "/logs", heading: /audit log/i },
  { path: "/settings", heading: /settings/i },
  { path: "/users", heading: /users/i },
  { path: "/account", heading: /account/i },
];

for (const { path, heading } of ROUTES) {
  test(`${path} renders without console errors`, async ({ page }) => {
    const errors = collectErrors(page);
    const response = await page.goto(path);

    expect(response?.status(), `${path} HTTP status`).toBeLessThan(400);
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
    expect(errors, `${path} console errors`).toEqual([]);
  });
}

test("an unknown route renders a not-found page rather than crashing", async ({ page }) => {
  const response = await page.goto("/this-route-does-not-exist");
  expect(response?.status()).toBe(404);
  // Should not leave the user staring at a blank document.
  await expect(page.locator("body")).not.toBeEmpty();
});

test("primary navigation exposes every destination with an accessible name", async ({ page }) => {
  await page.goto("/");
  const navLinks = page.locator("nav a, aside a");
  const count = await navLinks.count();
  expect(count).toBeGreaterThan(5);

  for (let i = 0; i < count; i++) {
    const link = navLinks.nth(i);
    const name = (await link.getAttribute("aria-label")) ?? (await link.innerText());
    expect(name.trim(), `nav link ${i} has no accessible name`).not.toBe("");
  }
});

test("no interactive control on the landing queue is missing an accessible name", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /work/i, level: 1 })).toBeVisible();
  expect(await unnamedControls(page)).toEqual([]);
});

test("the section rail switches sections and the shell stays mounted", async ({ page }) => {
  await page.goto("/");

  // Rail → section, then a page within that section.
  await page.getByRole("link", { name: "Work", exact: true }).click();
  await expect(page).toHaveURL(/\/invoices/);
  await page.getByRole("link", { name: "Queue", exact: true }).click();
  await expect(page.getByRole("heading", { name: /work/i, level: 1 })).toBeVisible();

  await page.getByRole("link", { name: "Suppliers", exact: true }).click();
  await expect(page).toHaveURL(/\/profiles/);
  await expect(page.getByRole("heading", { name: /suppliers/i, level: 1 })).toBeVisible();
});

test("the command palette opens on the keyboard and navigates", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("ControlOrMeta+k");

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  await page.getByRole("searchbox", { name: /search commands/i }).fill("audit");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/logs/);
});
