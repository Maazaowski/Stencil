import type { Page } from "@playwright/test";

/** Test credentials, supplied by the environment — never hard-coded. */
export function credentials(): { email: string; password: string } {
  const email = process.env.E2E_EMAIL;
  const password = process.env.E2E_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "E2E_EMAIL and E2E_PASSWORD must be set. Run `npm run test:e2e`, which seeds a " +
        "throwaway admin, or export them yourself against an existing account.",
    );
  }
  return { email, password };
}

/**
 * Console/page errors collected while a test runs.
 *
 * Next.js dev overlays and browser extensions emit benign noise, so this
 * filters to errors the application itself is responsible for.
 */
const IGNORED_CONSOLE = [
  /Download the React DevTools/i,
  /React DevTools/i,
  /Fast Refresh/i,
  /\[HMR\]/i,
  /favicon/i,
];

export function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    if (IGNORED_CONSOLE.some((re) => re.test(text))) return;
    errors.push(`console: ${text}`);
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  return errors;
}

/**
 * Accessible-name audit for every focusable control on the current page.
 * Returns the outerHTML (truncated) of controls a screen reader would
 * announce with no name at all — a WCAG 4.1.2 failure.
 */
export async function unnamedControls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const named = (el: Element): string => {
      const aria = el.getAttribute("aria-label");
      if (aria?.trim()) return aria.trim();
      const labelledby = el.getAttribute("aria-labelledby");
      if (labelledby) {
        const t = labelledby
          .split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent ?? "")
          .join(" ")
          .trim();
        if (t) return t;
      }
      const title = el.getAttribute("title");
      if (title?.trim()) return title.trim();
      const text = (el as HTMLElement).innerText?.trim();
      if (text) return text;
      const img = el.querySelector("img");
      if (img?.getAttribute("alt")?.trim()) return img.getAttribute("alt")!.trim();
      return "";
    };
    const out: string[] = [];
    document
      .querySelectorAll('button, a[href], [role="button"], [role="combobox"], [role="switch"]')
      .forEach((el) => {
        // Skip anything hidden from the accessibility tree.
        if (el.getAttribute("aria-hidden") === "true") return;
        const rect = (el as HTMLElement).getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) return;
        if (!named(el)) out.push(el.outerHTML.slice(0, 140));
      });
    return out;
  });
}

/** Text of every select/combobox trigger, with the chevron glyph stripped. */
export async function comboboxLabels(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll('button[role="combobox"]')).map((el) =>
      ((el as HTMLElement).innerText || "").replace(/[▼\s]+$/g, "").trim(),
    ),
  );
}
