import { test as setup, expect } from "@playwright/test";
import { credentials } from "./helpers";

const AUTH_FILE = "e2e/.auth/user.json";

/**
 * Logs in once and persists the session cookie for every authenticated project.
 * Doubles as the happy-path login test — if this fails, nothing else can pass.
 */
setup("authenticate", async ({ page }) => {
  const { email, password } = credentials();

  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();

  // Landing on the queue is the signal that the session cookie stuck.
  await expect(page.getByRole("heading", { name: "Work", level: 1 })).toBeVisible();
  await expect(page).toHaveURL(/\/invoices/);

  await page.context().storageState({ path: AUTH_FILE });
});
