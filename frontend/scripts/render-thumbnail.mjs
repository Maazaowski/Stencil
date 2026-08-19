/** Render scripts/thumbnail.html to a 2x PNG for the portfolio. */
import { chromium } from "@playwright/test";
import { pathToFileURL } from "node:url";
import path from "node:path";

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1200, height: 900 },
  deviceScaleFactor: 2,
});
const page = await ctx.newPage();
await page.goto(pathToFileURL(path.resolve("scripts/thumbnail.html")).href);
await page.waitForLoadState("networkidle").catch(() => {});
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(600);
await page.screenshot({ path: ".portfolio/00-thumbnail.png" });
console.log("wrote .portfolio/00-thumbnail.png");
await browser.close();
