import { test, expect } from "@playwright/test";

/**
 * Accessibility floor for every route.
 *
 * The app shipped with 16 aria-labels total; an audit found unlabelled search
 * boxes on five screens, four unnamed *destructive* buttons, and seventeen bare
 * inputs on the users page. All fixed — this keeps them fixed.
 *
 * Deliberately structural rather than a full WCAG sweep: exactly one h1, no
 * heading-level jumps, every control nameable, every input labelled, no
 * duplicate ids. Those are the failures that actually recur as screens change.
 */
const ROUTES = [
  "/invoices",
  "/exceptions",
  "/upload",
  "/profiles",
  "/accounts",
  "/models",
  "/field-schemas",
  "/output-specs",
  "/settings",
  "/insights",
  "/logs",
  "/users",
  "/account",
];

type Audit = {
  unnamed: string[];
  unlabelledInputs: string[];
  imgNoAlt: string[];
  headingJumps: string[];
  dupIds: string[];
  h1Count: number;
};

async function audit(page: import("@playwright/test").Page): Promise<Audit> {
  return page.evaluate(() => {
    const name = (el: Element): string => {
      const aria = el.getAttribute("aria-label");
      if (aria?.trim()) return aria.trim();
      const lb = el.getAttribute("aria-labelledby");
      if (lb) {
        const t = lb
          .split(/\s+/)
          .map((i) => document.getElementById(i)?.textContent ?? "")
          .join(" ")
          .trim();
        if (t) return t;
      }
      const title = el.getAttribute("title");
      if (title?.trim()) return title.trim();
      const text = (el as HTMLElement).innerText?.trim();
      if (text) return text;
      const alt = el.querySelector("img")?.getAttribute("alt");
      return alt?.trim() ?? "";
    };

    const shown = (el: Element) => {
      if (el.getAttribute("aria-hidden") === "true") return false;
      const r = (el as HTMLElement).getBoundingClientRect();
      return !(r.width === 0 && r.height === 0);
    };

    const out: Audit = {
      unnamed: [],
      unlabelledInputs: [],
      imgNoAlt: [],
      headingJumps: [],
      dupIds: [],
      h1Count: 0,
    };

    document
      .querySelectorAll(
        'button, a[href], [role="button"], [role="combobox"], [role="switch"], [role="tab"]',
      )
      .forEach((el) => {
        if (shown(el) && !name(el)) out.unnamed.push(el.outerHTML.slice(0, 100));
      });

    document.querySelectorAll("input, select, textarea").forEach((el) => {
      const input = el as HTMLInputElement;
      if (input.type === "hidden" || !shown(el)) return;
      const labelled =
        (input.id && document.querySelector(`label[for="${CSS.escape(input.id)}"]`)) ||
        el.getAttribute("aria-label") ||
        el.getAttribute("aria-labelledby") ||
        el.closest("label");
      if (!labelled) out.unlabelledInputs.push(`${el.tagName} ${input.placeholder ?? ""}`.trim());
    });

    document.querySelectorAll("img").forEach((i) => {
      if (i.getAttribute("alt") === null) out.imgNoAlt.push(i.src.slice(-40));
    });

    const levels = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")].map((h) => ({
      lvl: Number(h.tagName[1]),
      text: (h as HTMLElement).innerText.trim().slice(0, 30),
    }));
    for (let i = 1; i < levels.length; i++) {
      if (levels[i].lvl - levels[i - 1].lvl > 1) {
        out.headingJumps.push(
          `h${levels[i - 1].lvl} "${levels[i - 1].text}" -> h${levels[i].lvl} "${levels[i].text}"`,
        );
      }
    }
    out.h1Count = levels.filter((l) => l.lvl === 1).length;

    const seen: Record<string, number> = {};
    document.querySelectorAll("[id]").forEach((e) => {
      seen[e.id] = (seen[e.id] ?? 0) + 1;
    });
    out.dupIds = Object.entries(seen)
      .filter(([, n]) => n > 1)
      .map(([k, n]) => `${k} x${n}`);

    return out;
  });
}

for (const route of ROUTES) {
  test(`${route} meets the accessibility floor`, async ({ page }) => {
    await page.goto(route);
    await expect(page.locator("h1").first()).toBeVisible();
    // Data-driven controls appear after the first fetch.
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(1200);

    const a = await audit(page);

    expect(a.h1Count, `${route}: expected exactly one h1`).toBe(1);
    expect(a.unnamed, `${route}: controls with no accessible name`).toEqual([]);
    expect(a.unlabelledInputs, `${route}: inputs with no label`).toEqual([]);
    expect(a.imgNoAlt, `${route}: images with no alt`).toEqual([]);
    expect(a.headingJumps, `${route}: heading levels skip`).toEqual([]);
    expect(a.dupIds, `${route}: duplicate element ids`).toEqual([]);
  });
}
