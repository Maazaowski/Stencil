/**
 * Verify no real client identifier survives the portfolio scrub.
 *
 * Fails loudly rather than trusting the screenshots by eye — the whole point is
 * that these images go somewhere public.
 */
import { chromium } from "@playwright/test";
import { scrubOn } from "./scrub-lib.mjs";

const base = process.env.E2E_BASE_URL || "http://localhost:3000";

// Anything here appearing in a scrubbed page is a leak.
const FORBIDDEN = [
  "orange", "rogers", "colt", "zayo", "eunetworks", "crowncastle", "granite",
  "comcast", "mindtree", "singtel", "airtel", "tangoe", "lumen", "temforce",
  "at&t", "atandt", "gtt", "tata", "bell",
  "82824706", "59958650", "804284200", "287291052842", "802620646",
];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1560, height: 940 } });
const page = await ctx.newPage();

await page.goto(`${base}/login`);
await page.getByLabel("Email").fill(process.env.E2E_EMAIL);
await page.getByLabel("Password").fill(process.env.E2E_PASSWORD);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL((u) => !u.pathname.startsWith("/login"));

let failures = 0;
for (const url of ["/invoices?status=completed_with_warnings", "/profiles", "/insights"]) {
  await page.goto(`${base}${url}`);
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(2800); // all queries resolved before we rewrite the DOM
  await scrubOn(page);
  await page.waitForTimeout(120);

  const text = (await page.locator("body").innerText()).toLowerCase();
  const hits = FORBIDDEN.filter((t) => text.includes(t.toLowerCase()));
  if (hits.length) {
    console.log(`  LEAK on ${url}: ${hits.join(", ")}`);
    failures += hits.length;
  } else {
    console.log(`  clean: ${url}`);
  }
}

await browser.close();
process.exit(failures ? 1 : 0);
