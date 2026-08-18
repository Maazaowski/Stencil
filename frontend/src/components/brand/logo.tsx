import Link from "next/link";
import { cn } from "@/lib/utils";
import { Mark } from "./mark";

/**
 * The Stencil logo system.
 *
 * Four variants, one rule: the MARK carries the concept, the WORDMARK stays
 * disciplined. The previous wordmark sliced bridges through T/N/I/L — letters
 * with no counter to hold — which reads as a scanline glitch to anyone who
 * knows how a stencil is actually cut. Rather than fake structural bridges in
 * type we do not control, the bridge idea lives in the mark, where it is real.
 *
 * Everything is SVG + text: no raster, no baked background, no white plate.
 * Ink inherits `currentColor` so it works on either theme.
 */
type LogoVariant = "primary" | "mark" | "wordmark" | "mono";

const WORDMARK_CLASS =
  "font-display font-bold uppercase leading-none tracking-[0.14em] text-[0.95rem] select-none";

export function Logo({
  variant = "primary",
  className,
  href = "/",
}: {
  variant?: LogoVariant;
  className?: string;
  /** Omit to render a non-navigating lockup (login, print, exports). */
  href?: string | null;
}) {
  const content =
    variant === "mark" ? (
      <Mark className="h-7 w-7" />
    ) : variant === "wordmark" ? (
      <span className={WORDMARK_CLASS}>Stencil</span>
    ) : (
      <span className="flex items-center gap-2.5">
        <Mark className="h-7 w-7" showCut={variant !== "mono"} />
        <span className={WORDMARK_CLASS}>Stencil</span>
      </span>
    );

  const classes = cn(
    // Ink INHERITS — never force a colour here. The mark lives on the dark
    // sidebar and on the light login page, and `text-foreground` would make it
    // invisible on one of them.
    "inline-flex items-center",
    // Focus is branded, never removed.
    href && "outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--cut)]",
    className,
  );

  if (!href) {
    return (
      <span className={classes} role="img" aria-label="Stencil">
        {content}
      </span>
    );
  }

  return (
    <Link href={href} className={classes} aria-label="Stencil home">
      {content}
    </Link>
  );
}
