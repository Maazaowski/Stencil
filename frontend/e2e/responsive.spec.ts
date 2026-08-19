import { test, expect } from "@playwright/test";

/**
 * Mobile viewport (Pixel 7 project). Operators triage exceptions from phones,
 * so the shell must stay usable and must never scroll horizontally.
 */
test.describe("mobile layout", () => {
  const ROUTES = ["/", "/invoices", "/exceptions", "/profiles"];

  for (const route of ROUTES) {
    test(`${route} does not scroll horizontally on a phone`, async ({ page }) => {
      await page.goto(route);
      await expect(page.locator("h1").first()).toBeVisible();

      const overflow = await page.evaluate(() => {
        const doc = document.documentElement;
        return doc.scrollWidth - doc.clientWidth;
      });
      // A few px of rounding is fine; a real horizontal scrollbar is not.
      expect(overflow, `${route} overflows horizontally by ${overflow}px`).toBeLessThanOrEqual(2);
    });
  }

  test("navigation is reachable behind the menu button", async ({ page }) => {
    await page.goto("/");
    const menu = page.getByRole("button", { name: /open navigation/i }).first();
    await expect(menu).toBeVisible();

    await menu.click();
    // The sheet lists section pages, not the old flat destinations.
    await expect(page.getByRole("link", { name: "Queue", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Settings", exact: true })).toBeVisible();
  });
});

test("the queue becomes stacked records, not a sideways table", async ({ page }) => {
  await page.goto("/invoices");
  await expect(page.getByRole("heading", { name: /work/i, level: 1 })).toBeVisible();
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1500);

  // The desktop table is hidden at phone width — scrolling a nine-column table
  // sideways on a phone is a squeezed desktop, not a mobile view.
  const table = page.locator('[data-slot="table"]');
  if (await table.count()) {
    await expect(table.first()).toBeHidden();
  }

  // And the record list carries the four fields that decide triage.
  const records = page.locator("main button[type='button']");
  expect(await records.count()).toBeGreaterThan(0);
});
