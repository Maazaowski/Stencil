import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

/**
 * Tag — a square status mark, not a pill.
 *
 * The rule this exists to enforce: **normal is silent.** A tag marks deviation.
 * When 18 of 20 table rows carry an amber chip, amber has stopped meaning
 * "attention" — which is exactly what the old pill did. If a row is fine, it
 * gets no tag at all.
 *
 * Geometry: 1px radius, 18px tall, mono, uppercase, letterspaced. It reads as a
 * stamped mark rather than a bubble, and it stops competing with the data.
 */
const badgeVariants = cva(
  "group/badge inline-flex h-[18px] w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-sm border px-1.5 font-mono text-[0.6875rem] leading-none font-medium tracking-[0.06em] whitespace-nowrap uppercase transition-colors [&>svg]:pointer-events-none [&>svg]:size-2.5!",
  {
    variants: {
      variant: {
        /** Reconciled, delivered, healthy. Use sparingly — prefer no tag. */
        success: "border-success/30 bg-success/12 text-success",
        /** Delivered but needs a look: variance, drift, partial. */
        warning: "border-warning/30 bg-warning/12 text-warning",
        /** Failed. Rare by design. */
        destructive: "border-destructive/35 bg-destructive/12 text-destructive",
        /** The zero-cost model path — the product's proudest state. */
        cut: "border-primary/35 bg-primary/12 text-primary",
        /** Structural, non-semantic label (type, source, count). */
        neutral: "border-border-strong bg-muted text-muted-foreground",

        // ── aliases kept so existing call sites compile during migration ──
        default: "border-primary/35 bg-primary/12 text-primary",
        secondary: "border-border-strong bg-muted text-muted-foreground",
        outline: "border-border-strong bg-transparent text-muted-foreground",
        ghost: "border-transparent bg-transparent text-muted-foreground",
        link: "border-transparent bg-transparent text-primary underline-offset-4 hover:underline",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  }
)

function Badge({
  className,
  variant = "neutral",
  render,
  ...props
}: useRender.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      {
        className: cn(badgeVariants({ variant }), className),
      },
      props
    ),
    render,
    state: {
      slot: "badge",
      variant,
    },
  })
}

export { Badge, badgeVariants }
