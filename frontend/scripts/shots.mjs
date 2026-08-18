/**
 * Capture design screenshots of the running app in both themes.
 *   node scripts/shots.mjs <outDir> [route ...]
 * Credentials come from E2E_EMAIL / E2E_PASSWORD.
 */
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

const outDir = process.argv[2] || "shots";
const routes = process.argv.slice(3);
const base = process.env.E2E_BASE_URL || "http://localhost:3000";
const email = process.env.E2E_EMAIL;
const password = process.env.E2E_PASSWORD;

mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
const page = await ctx.newPage();

await page.goto(`${base}/login`);
await page.getByLabel("Email").fill(email);
await page.getByLabel("Password").fill(password);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

for (const theme of ["light", "dark"]) {
  await page.emulateMedia({ colorScheme: theme });
  for (const route of routes) {
    await page.goto(`${base}${route}`);
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(700);
    const name = (route === "/" ? "home" : route.replace(/\W+/g, "-").replace(/^-|-$/g, "")) + `.${theme}.png`;
    await page.screenshot({ path: path.join(outDir, name) });
    console.log("  ", name);
  }
}

await browser.close();
