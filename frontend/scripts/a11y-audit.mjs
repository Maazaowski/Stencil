/**
 * Accessibility audit across every route.
 *
 * Reports, per route: controls with no accessible name, inputs with no label,
 * images with no alt, heading-level jumps, and duplicate ids. Read-only — it
 * finds the work, the Playwright specs then lock it in.
 */
import { chromium } from "@playwright/test";

const base = process.env.E2E_BASE_URL || "http://localhost:3000";

const ROUTES = [
  "/invoices", "/exceptions", "/upload",
  "/profiles", "/accounts", "/models", "/field-schemas", "/output-specs",
  "/settings", "/insights", "/logs", "/users", "/account",
];

const AUDIT = () => {
  const name = (el) => {
    const aria = el.getAttribute("aria-label");
    if (aria?.trim()) return aria.trim();
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const t = lb.split(/\s+/).map((i) => document.getElementById(i)?.textContent ?? "").join(" ").trim();
      if (t) return t;
    }
    const title = el.getAttribute("title");
    if (title?.trim()) return title.trim();
    const text = el.innerText?.trim();
    if (text) return text;
    const img = el.querySelector("img");
    if (img?.getAttribute("alt")?.trim()) return img.getAttribute("alt").trim();
    return "";
  };

  const visible = (el) => {
    if (el.getAttribute("aria-hidden") === "true") return false;
    const r = el.getBoundingClientRect();
    return !(r.width === 0 && r.height === 0);
  };

  const out = { unnamed: [], unlabelledInputs: [], imgNoAlt: [], headingJumps: [], dupIds: [] };

  document
    .querySelectorAll('button, a[href], [role="button"], [role="combobox"], [role="switch"], [role="tab"]')
    .forEach((el) => {
      if (visible(el) && !name(el)) out.unnamed.push(el.outerHTML.slice(0, 120));
    });

  document.querySelectorAll("input, select, textarea").forEach((el) => {
    if (el.type === "hidden" || !visible(el)) return;
    const id = el.id;
    const hasLabel = id && document.querySelector(`label[for="${CSS.escape(id)}"]`);
    if (!hasLabel && !el.getAttribute("aria-label") && !el.getAttribute("aria-labelledby") && !el.closest("label")) {
      out.unlabelledInputs.push((el.tagName + " " + (el.getAttribute("placeholder") ?? "")).trim());
    }
  });

  document.querySelectorAll("img").forEach((i) => {
    if (i.getAttribute("alt") === null) out.imgNoAlt.push(i.src.slice(-50));
  });

  // Heading order: a jump of more than one level loses structure for AT users.
  const levels = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")].map((h) => ({
    lvl: Number(h.tagName[1]),
    text: h.innerText.trim().slice(0, 40),
  }));
  for (let i = 1; i < levels.length; i++) {
    if (levels[i].lvl - levels[i - 1].lvl > 1) {
      out.headingJumps.push(`h${levels[i - 1].lvl} "${levels[i - 1].text}" -> h${levels[i].lvl} "${levels[i].text}"`);
    }
  }
  out.h1Count = levels.filter((l) => l.lvl === 1).length;

  const seen = {};
  document.querySelectorAll("[id]").forEach((e) => { seen[e.id] = (seen[e.id] || 0) + 1; });
  out.dupIds = Object.entries(seen).filter(([, n]) => n > 1).map(([k, n]) => `${k} x${n}`);

  return out;
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(`${base}/login`);
await page.getByLabel("Email").fill(process.env.E2E_EMAIL);
await page.getByLabel("Password").fill(process.env.E2E_PASSWORD);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL((u) => !u.pathname.startsWith("/login"));

let total = 0;
for (const route of ROUTES) {
  await page.goto(`${base}${route}`);
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1400);
  const r = await page.evaluate(AUDIT);

  const issues = [];
  if (r.h1Count !== 1) issues.push(`h1 count = ${r.h1Count}`);
  if (r.unnamed.length) issues.push(`${r.unnamed.length} unnamed control(s)`);
  if (r.unlabelledInputs.length) issues.push(`${r.unlabelledInputs.length} unlabelled input(s)`);
  if (r.imgNoAlt.length) issues.push(`${r.imgNoAlt.length} img w/o alt`);
  if (r.headingJumps.length) issues.push(`${r.headingJumps.length} heading jump(s)`);
  if (r.dupIds.length) issues.push(`dup ids: ${r.dupIds.join(", ")}`);

  total += issues.length;
  console.log(issues.length ? `FAIL ${route}\n       ${issues.join("\n       ")}` : `ok   ${route}`);
  if (r.unnamed.length) r.unnamed.slice(0, 3).forEach((h) => console.log(`         ${h}`));
  if (r.unlabelledInputs.length) console.log(`         inputs: ${r.unlabelledInputs.slice(0, 4).join(" | ")}`);
  if (r.headingJumps.length) r.headingJumps.slice(0, 2).forEach((j) => console.log(`         ${j}`));
}

console.log(`\n${total === 0 ? "clean" : total + " issue group(s)"}`);
await browser.close();
