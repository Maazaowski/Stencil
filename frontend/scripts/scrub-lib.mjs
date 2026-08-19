/**
 * Client-data scrubbing, shared by the portfolio screenshot script and its
 * verifier so the images and the leak check can never drift apart.
 *
 * The app is full of real supplier relationships — names, account numbers,
 * invoice filenames, delivery paths. None of that should end up in a public
 * portfolio, so the DOM is rewritten before any screenshot is taken.
 */

/**
 * Real supplier -> fictional stand-in.
 *
 * Matched case-insensitively on the bare brand token, because the same name
 * shows up as a display name ("Colt Technology Services"), a profile id
 * ("colt.standard.v2.ai") and a path segment ("/data/Colt/xls"). Longer
 * phrases come first so they win before the short token rules.
 */
const SUPPLIERS = [
  [/Fiber\s*AssetCo\s*LLC\s*dba\s*Zayo/gi, "Lakeside Backbone"],
  [/Rogers\s*Communications?\s*Canada\s*Inc\.?\s*-?\s*\w*/gi, "Cascade Networks"],
  [/Tata\s*Communications\s*Limited/gi, "Continental Voice"],
  [/Colt\s*Technology\s*Services/gi, "Meridian Fibre"],
  [/Granite\s*Telecommunications/gi, "Ironvale Comms"],
  [/Orange[\s_]*Business[\s_]*\d*[\s_]*\w*/gi, "Northwind Telecom"],
  [/Comcast[\s_]*Business/gi, "Riverton Cable"],
  [/Crown\s*Castle/gi, "Stonebridge"],
  [/LTM\s*Limited/gi, "Delta Services"],
  [/Bell\s*MTS/gi, "Harbour Telecom"],
  // Bare brand tokens — these catch profile ids, folder names and paths.
  [/orange/gi, "Northwind"],
  [/rogers/gi, "Cascade"],
  [/colt/gi, "Meridian"],
  [/zayo/gi, "Lakeside"],
  [/eunetworks/gi, "HighlandConnect"],
  [/crowncastle/gi, "Stonebridge"],
  [/granite/gi, "Ironvale"],
  [/comcast/gi, "Riverton"],
  [/mindtree/gi, "Delta"],
  [/singtel/gi, "PacificLink"],
  [/airtel/gi, "Bluepeak"],
  [/tangoe/gi, "OrbitBilling"],
  [/lumen/gi, "Clearpath"],
  [/gtt/gi, "Vantage"],
  [/tata/gi, "Continental"],
  [/bell/gi, "Harbour"],
  [/AT&T/gi, "Summit Wireless"],
  [/ATandT/gi, "SummitWireless"],
  [/temforce/gi, "Acme"],
];

export async function scrubOn(page) {
  await page.evaluate(
    (patterns) => {
      const rules = patterns.map(([src, flags, rep]) => [new RegExp(src, flags), rep]);

      // Stable hash, so the same input always yields the same stand-in and rows
      // stay internally consistent across a screenshot.
      const hash = (s) => {
        let h = 2166136261;
        for (const c of s) h = ((h ^ c.charCodeAt(0)) * 16777619) >>> 0;
        return h >>> 0;
      };

      const MONTHS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"];

      // Whole filenames, not digit runs. Scrambling digits inside a hyphenated
      // name produces something that reads as corrupted rather than anonymised.
      const filename = (s) =>
        s.replace(/[\w.-]*\.pdf/gi, (m) => {
          const h = hash(m);
          return `invoice-2026-${MONTHS[h % 12]}-${1000 + (h % 9000)}.pdf`;
        });

      // Account and reference numbers left over outside filenames.
      const digits = (s) =>
        filename(s).replace(/\d{6,}/g, (m) => {
          const h = hash(m);
          return String(h).repeat(3).slice(0, m.length);
        });

      const clean = (v) => {
        let out = v;
        for (const [re, rep] of rules) out = out.replace(re, rep);
        return digits(out);
      };

      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);

      for (const n of nodes) {
        const v = n.nodeValue;
        if (!v || !v.trim()) continue;
        const next = clean(v);
        if (next !== v) n.nodeValue = next;
      }

      // Tooltips carry the same data as the cells they describe.
      document.querySelectorAll("[title]").forEach((el) => {
        el.setAttribute("title", clean(el.getAttribute("title") || ""));
      });

      // The signed-in account is a real address.
      document.querySelectorAll("*").forEach((el) => {
        if (el.children.length === 0 && /@/.test(el.textContent || "")) {
          el.textContent = (el.textContent || "").replace(
            /[\w.+-]+@[\w.-]+/g,
            "operator@acme.example",
          );
        }
      });
    },
    SUPPLIERS.map(([re, rep]) => [re.source, re.flags, rep]),
  );
}
