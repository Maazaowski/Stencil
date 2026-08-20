import { expect, test } from "@playwright/test";

/**
 * The layout endpoint's page contract.
 *
 * `extract_layout_document` used to default to 30 pages, so this endpoint
 * silently reported a 656-page document as having 30 and the builder canvas
 * believed it. The bound is now explicit: `page_count` is the document's true
 * length, `rendered_page_count` is what the response actually carries, and any
 * truncation is named in `warnings`.
 *
 * The builder reads all three, so a regression here breaks page navigation
 * without breaking the build.
 */
test.describe("intake layout API", () => {
  async function anyIntakeId(request: import("@playwright/test").APIRequestContext) {
    const res = await request.get("/api/v1/invoices?per_page=1");
    expect(res.ok(), `listing invoices failed: ${res.status()}`).toBeTruthy();
    const body = await res.json();
    return body.items?.[0]?.id as string | undefined;
  }

  test("reports the document's true length and what it rendered", async ({ request }) => {
    const intakeId = await anyIntakeId(request);
    test.skip(!intakeId, "no intakes in this environment");

    const res = await request.get(`/api/v1/intakes/${intakeId}/layout`);
    expect(res.ok(), `layout failed: ${res.status()}`).toBeTruthy();
    const body = await res.json();

    expect(typeof body.page_count).toBe("number");
    expect(typeof body.rendered_page_count).toBe("number");
    expect(body.page_count).toBeGreaterThan(0);
    // Never claim to have rendered more than the document has.
    expect(body.rendered_page_count).toBeLessThanOrEqual(body.page_count);
    // And the pages actually present must match the count reported.
    expect(body.pages.length).toBe(body.rendered_page_count);
  });

  test("an explicit bound truncates and says so", async ({ request }) => {
    const intakeId = await anyIntakeId(request);
    test.skip(!intakeId, "no intakes in this environment");

    const full = await (await request.get(`/api/v1/intakes/${intakeId}/layout`)).json();
    test.skip(full.page_count < 2, "document too short to exercise a bound");

    const res = await request.get(`/api/v1/intakes/${intakeId}/layout?max_pages=1`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();

    expect(body.rendered_page_count).toBe(1);
    expect(body.page_count).toBe(full.page_count);
    expect(
      body.warnings.some((w: string) => /truncated to 1 of/.test(w)),
      `expected a truncation warning, got ${JSON.stringify(body.warnings)}`,
    ).toBeTruthy();
  });

  test("rows carry normalized 0-1000 geometry the canvas can draw", async ({ request }) => {
    const intakeId = await anyIntakeId(request);
    test.skip(!intakeId, "no intakes in this environment");

    const body = await (await request.get(`/api/v1/intakes/${intakeId}/layout?max_pages=1`)).json();
    const row = body.pages?.[0]?.rows?.[0];
    test.skip(!row, "first page has no rows");

    for (const box of [row.normalized_bbox, row.cells?.[0]?.normalized_bbox].filter(Boolean)) {
      expect(box.x0).toBeGreaterThanOrEqual(0);
      expect(box.x1).toBeLessThanOrEqual(1000);
    }
  });
});
