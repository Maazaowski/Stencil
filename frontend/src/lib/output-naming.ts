/** Derive Temforce XLSX filename from the source PDF name (mirrors backend). */

export function deriveOutputXlsxName(originalFilename: string): string {
  const base = originalFilename.split(/[/\\]/).pop()?.trim() ?? "";
  if (!base) return "invoice_output.xlsx";

  const dot = base.lastIndexOf(".");
  const stem = dot > 0 ? base.slice(0, dot) : base;
  const safeStem = stem.replace(/[<>:"|?*\\]/g, "_").replace(/[ .]+$/g, "");
  return `${safeStem || "invoice_output"}.xlsx`;
}

export function resolveXlsxOutputFilename(
  jobFilename: string | null | undefined,
  originalFilename: string | undefined,
): string {
  return jobFilename ?? deriveOutputXlsxName(originalFilename ?? "");
}
