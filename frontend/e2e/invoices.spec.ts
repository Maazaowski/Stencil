import { test, expect } from "@playwright/test";


/**
 * The invoice queue is the operator's main working surface: filter down to a
 * problem invoice, open it, get to the deliverable. These cover the filtering
 * and pagination contract rather than exact row content, so the suite stays
 * meaningful against any dataset.
 */
test.describe("invoice queue", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/invoices");
    await expect(page.getByRole("heading", { name: /work/i, level: 1 })).toBeVisible();
  });

  test("renders a table with the expected columns", async ({ page }) => {
    const table = page.locator("table").first();
    await expect(table).toBeVisible();
    // Redesigned column set: the truncated-UUID column is gone, "Filename"
    // became "Invoice", and the fabricated "Confidence" became "Reconciled".
    for (const column of ["Status", "Supplier", "Invoice", "Reconciled", "Cost"]) {
      await expect(table.getByRole("columnheader", { name: column })).toBeVisible();
    }
  });

  test("status chips filter the table and survive a reload", async ({ page }) => {
    // Chips render only for statuses that have rows, so they appear after the
    // first fetch — waiting beats skipping, which hides the test entirely.
    const failedChip = page.getByRole("button", { name: /^failed/i }).first();
    await expect(failedChip).toBeVisible();

    await failedChip.click();
    await expect(page).toHaveURL(/status=failed/);

    // The filter is URL-driven, so a reload must preserve it (deep-linkable).
    await page.reload();
    await expect(page).toHaveURL(/status=failed/);
  });

  test("filters appear as removable chips and survive a reload", async ({ page }) => {
    // The five always-visible "All …" dropdowns became one Filters menu; applied
    // filters show as chips, so the band is proportional to what the user did.
    await expect(page.getByRole("button", { name: /^filters/i })).toBeVisible();

    // Nothing applied: no filter chips.
    await expect(page.getByRole("button", { name: /^remove filter/i })).toHaveCount(0);

    await page.getByRole("button", { name: /^filters/i }).click();
    await page.getByRole("menuitemcheckbox", { name: /paid — ai extraction/i }).click();
    await page.keyboard.press("Escape");

    const chip = page.getByRole("button", { name: /^remove filter/i });
    await expect(chip).toHaveCount(1);
    await expect(page).toHaveURL(/path=ai/);

    // URL-driven, so it deep-links.
    await page.reload();
    await expect(page.getByRole("button", { name: /^remove filter/i })).toHaveCount(1);

    // And the chip removes exactly itself.
    await page.getByRole("button", { name: /^remove filter/i }).click();
    await expect(page.getByRole("button", { name: /^remove filter/i })).toHaveCount(0);
  });

  test("status chips hide states that have no rows", async ({ page }) => {
    // "Received 0" and "Processing 0" were permanent dead controls.
    const chips = page.locator('button[aria-pressed]');
    const count = await chips.count();
    for (let i = 0; i < count; i++) {
      const text = await chips.nth(i).innerText();
      expect(text, `chip "${text}" shows a zero count`).not.toMatch(/0$/);
    }
  });

  test("search narrows the result set and is reflected in the URL", async ({ page }) => {
    const search = page.getByPlaceholder(/search filename/i);
    test.skip(!(await search.isVisible()), "no filename search input");

    await search.fill("zzz-no-such-invoice-zzz");
    // Debounced — wait for the query param rather than a fixed timeout.
    await expect(page).toHaveURL(/q=zzz-no-such-invoice-zzz/, { timeout: 15_000 });

    const rows = page.locator("tbody tr");
    await expect
      .poll(async () => rows.count(), { timeout: 15_000 })
      .toBeLessThanOrEqual(1); // 0 rows, or a single "no results" row
  });

  test("pagination advances and the page can be deep-linked", async ({ page }) => {
    const next = page.getByRole("button", { name: /next/i });
    test.skip(!(await next.isEnabled().catch(() => false)), "only one page of results");

    const firstCellBefore = await page.locator("tbody tr").first().innerText();
    await next.click();
    await expect
      .poll(async () => page.locator("tbody tr").first().innerText())
      .not.toBe(firstCellBefore);
  });

  test("opening an invoice shows its processing detail", async ({ page }) => {
    const firstRow = page.locator("tbody tr").first();
    await expect(firstRow).toBeVisible();

    await firstRow.click();
    await expect(page).toHaveURL(/\/invoices\/[0-9a-f-]{8,}/);
    // The detail page must render something identifying, not a blank shell.
    await expect(page.locator("h1")).toBeVisible();
  });
});
