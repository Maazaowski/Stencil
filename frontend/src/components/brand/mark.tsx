import { cn } from "@/lib/utils";

/**
 * The Stencil mark — an aperture held open by four bridges, with the cut line
 * through it.
 *
 * Drawn on a 24-unit grid. Four chunky L-shapes form a square ring broken at
 * each midpoint; those breaks ARE the bridges — the connectors that keep a
 * stencil's counter attached to the sheet. The single accent element is the cut
 * line running through the aperture.
 *
 * Rules this must keep obeying:
 *   - Ink uses `currentColor`, so the mark inherits the theme. Never a fill.
 *   - No gradients, no baked background, no plate behind it.
 *   - Five shapes total, so it survives 16px.
 */
export function Mark({
  className,
  showCut = true,
}: {
  className?: string;
  /** The accent cut line. Off for single-colour / monochrome contexts. */
  showCut?: boolean;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={cn("h-6 w-6 shrink-0", className)}
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      {/* Broken ring — the gaps at each midpoint are the bridges */}
      <g fill="currentColor">
        <path d="M3 3H10V7H7V10H3V3Z" />
        <path d="M21 3V10H17V7H14V3H21Z" />
        <path d="M21 21H14V17H17V14H21V21Z" />
        <path d="M3 21V14H7V17H10V21H3Z" />
      </g>
      {/* The cut — the only place the accent is allowed to appear */}
      {showCut && <rect x="7" y="11" width="10" height="2" fill="var(--cut, currentColor)" />}
    </svg>
  );
}
