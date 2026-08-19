/**
 * Capture PORTFOLIO screenshots with client data scrubbed.
 *
 * The normal `shots.mjs` captures the app as-is, which is fine internally but
 * exposes real supplier names, account numbers and invoice filenames. This
 * variant rewrites those in the DOM before capture so the images can be shown
 * publicly without leaking a client's billing relationships.
 *
 *   node scripts/portfolio-shots.mjs <outDir>
 */
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { scrubOn } from "./scrub-lib.mjs";

const outDir = process.argv[2] || ".portfolio";
const base = process.env.E2E_BASE_URL || "http://localhost:3000";
const email = process.env.E2E_EMAIL;
const password = process.env.E2E_PASSWORD;

mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1560, height: 940 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();

await page.goto(`${base}/login`);
await page.getByLabel("Email").fill(email);
await page.getByLabel("Password").fill(password);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

const shots = [
  { name: "01-queue", url: "/invoices?status=completed_with_warnings", theme: "light" },
  { name: "02-suppliers", url: "/profiles", theme: "light" },
  { name: "03-queue-dark", url: "/invoices?status=completed_with_warnings", theme: "dark" },
  { name: "04-insights", url: "/insights", theme: "light" },
];

for (const s of shots) {
  await page.emulateMedia({ colorScheme: s.theme });
  await page.goto(`${base}${s.url}`);
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(2800); // all queries resolved before we rewrite the DOM
  await scrubOn(page);
  await page.waitForTimeout(120);
  await page.screenshot({ path: path.join(outDir, `${s.name}.png`) });
  console.log("  ", `${s.name}.png`);
}

// Invoice detail — the reconciliation view.
await page.emulateMedia({ colorScheme: "light" });
await page.goto(`${base}/invoices?status=completed_with_warnings`);
await page.waitForTimeout(1200);
await page.locator("tbody tr").first().click();
await page.waitForTimeout(2800);
await scrubOn(page);
await page.waitForTimeout(150);
await page.screenshot({ path: path.join(outDir, "05-reconciliation.png") });
console.log("   05-reconciliation.png");

await browser.close();
