import { test, expect } from "@playwright/test";
import { comboboxLabels, unnamedControls } from "./helpers";

/**
 * Settings is the highest-blast-radius screen in the app: it holds the provider
 * credentials, the debug switch, and (behind debug) the destructive purge.
 */
test.describe("settings", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: /settings/i, level: 1 })).toBeVisible();
  });

  test("never renders a stored API key", async ({ page }) => {
    const body = await page.locator("body").innerText();
    // Keys must only ever be reported as configured/not configured.
    expect(body).not.toMatch(/sk-[A-Za-z0-9_-]{16,}/);
  });

  test("the provider dropdown shows a display label, not the raw enum", async ({ page }) => {
    // The page renders skeletons until /settings resolves — wait for the real form.
    await expect(page.locator('button[role="combobox"]').first()).toBeVisible();

    const labels = await comboboxLabels(page);
    expect(labels.length).toBeGreaterThan(0);
    expect(labels, "provider select renders the raw value").not.toContain("openai");
    expect(labels, "provider select renders the raw value").not.toContain("anthropic");
  });

  test("the destructive purge requires typing an exact confirmation phrase", async ({ page }) => {
    const trigger = page.getByRole("button", { name: /clear all invoice data/i });
    test.skip(!(await trigger.isVisible()), "danger zone hidden (debug mode off)");

    await trigger.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    const confirmButton = dialog.getByRole("button", { name: /delete everything/i });
    // Guard must start disabled...
    await expect(confirmButton).toBeDisabled();

    // ...stay disabled for a near-miss...
    await dialog.getByRole("textbox").fill("DELETE");
    await expect(confirmButton).toBeDisabled();

    // ...and only enable on the exact phrase.
    await dialog.getByRole("textbox").fill("DELETE ALL");
    await expect(confirmButton).toBeEnabled();

    // Leave without destroying anything.
    await dialog.getByRole("button", { name: /cancel/i }).click();
    await expect(dialog).not.toBeVisible();
  });

  test("no interactive control is missing an accessible name", async ({ page }) => {
    await expect(page.locator('button[role="combobox"]').first()).toBeVisible();
    expect(await unnamedControls(page)).toEqual([]);
  });
});

test.describe("save failures", () => {
  test("a failed save reports inline and stays put", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: /settings/i, level: 1 })).toBeVisible();
    await expect(page.locator('button[role="combobox"]').first()).toBeVisible();

    // Force the save to fail.
    await page.route("**/api/v1/settings", async (route) => {
      if (route.request().method() === "PUT") {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Provider is unreachable." }),
        });
      } else {
        await route.continue();
      }
    });

    // Dirty the form so Save enables, then save.
    const timeout = page.getByLabel(/timeout/i).first();
    await timeout.fill("120");
    await page.getByRole("button", { name: /save/i }).first().click();

    // The failure is inline, carries the API's reason, and does NOT vanish.
    const err = page.getByRole("alert").filter({ hasText: /not saved/i });
    await expect(err).toBeVisible();
    await expect(err).toContainText("Provider is unreachable.");

    // A toast would be gone by now; this must still be here.
    await page.waitForTimeout(5000);
    await expect(err).toBeVisible();

    // And it is dismissible.
    await err.getByRole("button", { name: /dismiss/i }).click();
    await expect(err).not.toBeVisible();
  });
});
