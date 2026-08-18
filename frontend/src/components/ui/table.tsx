"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Table — the workhorse surface. Most of this product is numbers in columns.
 *
 * Changes from the stock table, each with a reason:
 *   - 32px rows, not 40+. An operator scans a queue; more rows per screen is the
 *     single biggest usability win available here.
 *   - Header is condensed, mono, uppercase and STICKY, so it survives scrolling
 *     a long queue.
 *   - Tabular figures throughout, so digits align down a column.
 *   - Hairline row rules, no zebra. Zebra is decoration that fights the tags.
 *   - `numeric` on a cell right-aligns and monospaces it. Amounts must line up.
 */
function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div
      data-slot="table-container"
      className="relative w-full overflow-x-auto border border-border-strong bg-card"
    >
      <table
        data-slot="table"
        className={cn(
          "w-full caption-bottom border-collapse text-[0.8125rem] [font-variant-numeric:tabular-nums]",
          className
        )}
        {...props}
      />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      className={cn(
        "sticky top-0 z-10 bg-muted [&_tr]:border-b [&_tr]:border-border-strong",
        className
      )}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t border-border-strong bg-muted font-medium [&>tr]:last:border-b-0",
        className
      )}
      {...props}
    />
  )
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b border-border transition-colors duration-[120ms] hover:bg-accent/60 has-aria-expanded:bg-accent data-[state=selected]:bg-primary/10",
        className
      )}
      {...props}
    />
  )
}

function TableHead({
  className,
  numeric,
  ...props
}: React.ComponentProps<"th"> & { numeric?: boolean }) {
  return (
    <th
      data-slot="table-head"
      scope="col"
      className={cn(
        "h-7 px-2.5 text-left align-middle font-mono text-[0.6875rem] font-medium tracking-[0.1em] whitespace-nowrap uppercase text-muted-foreground",
        numeric && "text-right",
        className
      )}
      {...props}
    />
  )
}

function TableCell({
  className,
  numeric,
  ...props
}: React.ComponentProps<"td"> & { numeric?: boolean }) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "h-8 px-2.5 align-middle whitespace-nowrap",
        numeric && "text-right font-mono",
        className
      )}
      {...props}
    />
  )
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-3 text-left text-xs text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
