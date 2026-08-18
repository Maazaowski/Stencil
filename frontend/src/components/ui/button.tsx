import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

/**
 * Button — three tiers, not six variants.
 *
 *   solid    the one primary action on a screen
 *   hairline everything else that is a real action
 *   text     tertiary / inline
 *
 * Square (2px), no shadow, no active nudge, no 3px focus glow. Focus is the
 * branded 2px cut-coloured outline defined globally in globals.css, so it is
 * identical on every focusable thing in the product.
 *
 * `default`/`outline`/`secondary`/`ghost` are kept as aliases so the ~50 existing
 * call sites keep compiling while screens migrate. They map onto the three real
 * tiers rather than introducing new looks.
 */
const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-transparent text-sm font-medium whitespace-nowrap transition-colors duration-[120ms] outline-none select-none disabled:pointer-events-none disabled:opacity-45 aria-invalid:border-destructive [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        // ── the three tiers ──
        solid:
          "bg-primary text-primary-foreground hover:bg-primary/88 aria-expanded:bg-primary/88",
        hairline:
          "border-border-strong bg-card text-foreground hover:bg-accent hover:border-border-strong aria-expanded:bg-accent",
        text:
          "text-muted-foreground hover:text-foreground hover:bg-accent aria-expanded:bg-accent",

        // ── aliases onto the tiers (kept for migration) ──
        default:
          "bg-primary text-primary-foreground hover:bg-primary/88 aria-expanded:bg-primary/88",
        outline:
          "border-border-strong bg-card text-foreground hover:bg-accent aria-expanded:bg-accent",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-accent aria-expanded:bg-accent",
        ghost:
          "text-muted-foreground hover:text-foreground hover:bg-accent aria-expanded:bg-accent",

        // Destructive is deliberately quiet until hover. Ambient red is noise.
        destructive:
          "border-transparent text-destructive hover:bg-destructive/12 hover:border-destructive/35",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-8 px-2.5",
        xs: "h-6 gap-1 px-1.5 text-xs [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1 px-2 text-[0.8125rem] [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-9 px-3",
        icon: "size-8",
        "icon-xs": "size-6 [&_svg:not([class*='size-'])]:size-3",
        "icon-sm": "size-7 [&_svg:not([class*='size-'])]:size-3.5",
        "icon-lg": "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
