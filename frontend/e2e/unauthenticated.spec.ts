import { test, expect } from "@playwright/test";
import { credentials } from "./helpers";

/**
 * Auth boundary. Runs with NO session cookie (see the `chromium-anonymous`
 * project) — these must fail closed.
 */
test.describe("unauthenticated access", () => {
  const protectedRoutes = [
    "/",
    "/invoices",
    "/profiles",
    "/models",
    "/settings",
    "/users",
    "/accounts",
    "/exceptions",
  ];

  for (const route of protectedRoutes) {
    test(`${route} redirects an anonymous visitor to /login`, async ({ page }) => {
      await page.goto(route);
      await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
      await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    });
  }

  test("wrong password shows a generic error and does not enumerate users", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill("definitely-not-a-user@example.com");
    await page.getByLabel("Password").fill("wrong-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    // Scoped to the form: Next.js injects its own route-announcer with role="alert".
    const error = page.locator("form").getByRole("alert");
    await expect(error).toBeVisible();
    // Same message regardless of whether the account exists.
    await expect(error).toHaveText(/invalid email or password/i);
    await expect(page).toHaveURL(/\/login/);
  });

  test("the login form is keyboard operable and labelled", async ({ page }) => {
    await page.goto("/login");
    const email = page.getByLabel("Email");
    const password = page.getByLabel("Password");

    await email.focus();
    await page.keyboard.press("Tab");
    await expect(password).toBeFocused();

    // Password managers rely on these.
    await expect(email).toHaveAttribute("autocomplete", "username");
    await expect(password).toHaveAttribute("autocomplete", "current-password");
  });

  test("submitting valid credentials lands on the queue", async ({ page }) => {
    const { email, password } = credentials();
    await page.goto("/login");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByRole("heading", { name: "Work", level: 1 })).toBeVisible();
  });
});
