"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { LogOut } from "lucide-react";
import { Logo } from "@/components/brand/logo";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSettings } from "@/hooks/use-settings";
import { useLogout, useSession } from "@/hooks/use-session";
import {
  NAV_SECTIONS,
  sectionForPath,
  visibleChildren,
  type NavChild,
} from "./nav-config";

/**
 * Two-tier navigation: a narrow section rail, then the pages within that
 * section. Sixteen flat destinations became three, and depth is reached by
 * being *in* a section rather than by scanning a list.
 *
 * Everything is square, hairline-ruled, and mono-labelled — the sidebar is
 * where the brand reads first, so it carries the system most literally.
 */
export function Sidebar() {
  const pathname = usePathname();
  const { data: settings } = useSettings();
  const { data: session } = useSession();
  const logout = useLogout();

  const active = sectionForPath(pathname) ?? NAV_SECTIONS[0];
  const children = visibleChildren(active, {
    isAdmin: session?.is_admin,
    debug: settings?.debug,
    signedIn: !!session,
  });

  const isCurrent = (child: NavChild) =>
    pathname === child.href || pathname.startsWith(child.href + "/");

  return (
    <aside className="flex shrink-0 border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      {/* ── Section rail ── */}
      <div className="flex w-14 flex-col items-center gap-1 border-r border-sidebar-border py-3">
        <Link
          href="/"
          aria-label="Stencil home"
          className="mb-3 flex h-8 w-8 items-center justify-center"
        >
          <Logo variant="mark" href={null} />
        </Link>

        <nav aria-label="Sections" className="flex flex-col items-center gap-1">
          {NAV_SECTIONS.map((section) => {
            const on = section.id === active.id;
            return (
              <Tooltip key={section.id}>
                <TooltipTrigger
                  render={
                    <Link
                      href={section.href}
                      // Icon-only: the tooltip is a VISUAL label, not an
                      // accessible one. Without this a screen reader announces
                      // the bare URL.
                      aria-label={section.label}
                      aria-current={on ? "page" : undefined}
                      className={cn(
                        "relative flex h-10 w-10 items-center justify-center rounded-md transition-colors duration-[120ms]",
                        on
                          ? "bg-sidebar-accent text-sidebar-accent-foreground"
                          : "text-sidebar-foreground/55 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
                      )}
                    >
                      {/* The active mark is a cut line, not a pill */}
                      {on && (
                        <span className="absolute -left-[13px] h-6 w-[2px] bg-[var(--cut-line)]" />
                      )}
                      <section.icon className="h-[18px] w-[18px]" aria-hidden="true" />
                    </Link>
                  }
                />
                <TooltipContent side="right">{section.label}</TooltipContent>
              </Tooltip>
            );
          })}
        </nav>
      </div>

      {/* ── Pages within the section ── */}
      <div className="flex w-52 flex-col">
        <div className="flex h-12 items-center border-b border-sidebar-border px-3">
          <span className="font-display text-[0.8125rem] font-semibold uppercase tracking-[0.13em]">
            {active.label}
          </span>
        </div>

        <nav aria-label={`${active.label} pages`} className="flex-1 overflow-y-auto p-2">
          {children.map((child) => {
            const on = isCurrent(child);
            return (
              <Link
                key={child.href}
                href={child.href}
                aria-current={on ? "page" : undefined}
                className={cn(
                  "group flex flex-col gap-0.5 rounded-md px-2.5 py-1.5 transition-colors duration-[120ms]",
                  on
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground",
                )}
              >
                <span className="text-[0.8125rem] font-medium leading-tight">{child.label}</span>
                {child.hint && (
                  <span
                    aria-hidden="true"
                    className="text-[0.6875rem] leading-tight text-sidebar-foreground/45"
                  >
                    {child.hint}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>

        {session && (
          <div className="border-t border-sidebar-border p-2">
            <button
              type="button"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sidebar-foreground/65 transition-colors hover:bg-sidebar-accent/50 hover:text-sidebar-foreground disabled:opacity-50"
            >
              <LogOut className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span className="min-w-0 flex-1">
                <span className="block text-[0.8125rem] leading-tight">Sign out</span>
                <span className="block truncate font-mono text-[0.625rem] leading-tight text-sidebar-foreground/40">
                  {session.email}
                </span>
              </span>
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
