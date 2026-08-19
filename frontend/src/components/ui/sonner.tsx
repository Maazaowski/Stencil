"use client"

import { useTheme } from "next-themes"
import { Toaster as Sonner, type ToasterProps } from "sonner"

/**
 * Toasts confirm; they do not report failures.
 *
 * A toast is a message that removes itself. That is fine for "Saved." and wrong
 * for anything the user must act on — which is why failures now render inline,
 * next to the control that failed (see <InlineError>). Errors that still reach
 * this component are a fallback, not the intended path.
 *
 * Bottom-LEFT by design: the queue's primary actions sit on the right, and a
 * toast that covers them is a toast that gets dismissed unread.
 */
const Toaster = ({ ...props }: ToasterProps) => {
  const { theme = "system" } = useTheme()

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      position="bottom-left"
      // The system's one permitted shadow lives on floating layers only.
      className="toaster group"
      // Square geometry, hairline border, no icon furniture — the text is the
      // message. Matches the tags and inputs rather than looking imported.
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border-strong)",
          "--border-radius": "2px",
        } as React.CSSProperties
      }
      toastOptions={{
        duration: 4000,
        classNames: {
          toast:
            "cn-toast !rounded-sm !border !border-border-strong !bg-popover !text-popover-foreground !shadow-[var(--shadow-overlay)] !font-sans !text-[0.8125rem]",
          title: "!font-medium",
          description: "!text-muted-foreground !text-[0.75rem]",
          actionButton:
            "!rounded-sm !bg-primary !text-primary-foreground !text-[0.75rem] !font-medium",
          cancelButton:
            "!rounded-sm !bg-transparent !text-muted-foreground !text-[0.75rem]",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
