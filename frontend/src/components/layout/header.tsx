"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetClose } from "@/components/ui/sheet";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Menu, Sun, Moon, Monitor, Search } from "lucide-react";
import { Logo } from "@/components/brand/logo";
import { useSettings } from "@/hooks/use-settings";
import { useSession } from "@/hooks/use-session";
import { NAV_SECTIONS, sectionForPath, visibleChildren } from "./nav-config";
import { useCommandPalette } from "./command-palette";

function ThemeToggle() {
  const { setTheme } = useTheme();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="text" size="icon-sm" aria-label="Change theme">
            <Sun className="h-4 w-4 dark:hidden" aria-hidden="true" />
            <Moon className="hidden h-4 w-4 dark:block" aria-hidden="true" />
          </Button>
        }
      />
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => setTheme("light")}>
          <Sun className="h-4 w-4" /> Light
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("dark")}>
          <Moon className="h-4 w-4" /> Dark
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("system")}>
          <Monitor className="h-4 w-4" /> System
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Header — a thin utility bar, not a title bar.
 *
 * It no longer renders a page title. "Dashboard" used to appear three times on
 * one screen (rail, header, H1); the page's own <PageHeader> owns the name, and
 * this bar owns search and preferences.
 */
export function Header() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { data: settings } = useSettings();
  const { data: session } = useSession();
  const { open: openPalette } = useCommandPalette();

  const active = sectionForPath(pathname) ?? NAV_SECTIONS[0];

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-background px-3 md:px-4">
      <Button
        variant="text"
        size="icon-sm"
        className="md:hidden"
        onClick={() => setMobileOpen(true)}
        aria-label="Open navigation"
      >
        <Menu className="h-4 w-4" aria-hidden="true" />
      </Button>

      {/* Search is the real navigation once there are only three sections. */}
      <button
        type="button"
        onClick={openPalette}
        className="group flex h-7 min-w-0 flex-1 items-center gap-2 rounded-md border border-border bg-card px-2.5 text-left text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground md:max-w-sm"
      >
        <Search className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="truncate text-[0.8125rem]">Search invoices, suppliers, actions…</span>
        <kbd className="ml-auto hidden shrink-0 rounded-sm border border-border px-1 font-mono text-[0.625rem] text-muted-foreground md:block">
          ⌘K
        </kbd>
      </button>

      <div className="ml-auto flex items-center gap-1">
        <ThemeToggle />
      </div>

      {/* Mobile navigation — the full three-section map, flattened */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" showCloseButton>
          <div className="flex h-full flex-col">
            <div className="flex items-center border-b border-border p-4">
              <Logo variant="primary" />
              <SheetTitle className="sr-only">Stencil navigation</SheetTitle>
            </div>
            <nav className="flex-1 overflow-y-auto p-2">
              {NAV_SECTIONS.map((section) => (
                <div key={section.id} className="mb-3">
                  <p className="label-mono px-2.5 py-1">{section.label}</p>
                  {visibleChildren(section, {
                    isAdmin: session?.is_admin,
                    debug: settings?.debug,
                    signedIn: !!session,
                  }).map((child) => {
                    const on =
                      pathname === child.href || pathname.startsWith(child.href + "/");
                    return (
                      <SheetClose
                        key={child.href}
                        render={
                          <Link
                            href={child.href}
                            aria-current={on ? "page" : undefined}
                            className={cn(
                              "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                              on
                                ? "bg-primary/12 text-primary"
                                : "text-muted-foreground hover:bg-accent hover:text-foreground",
                            )}
                          >
                            <child.icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                            {child.label}
                          </Link>
                        }
                      />
                    );
                  })}
                </div>
              ))}
            </nav>
          </div>
        </SheetContent>
      </Sheet>

      {/* Keeps the active section available to tests/a11y without drawing a title */}
      <span className="sr-only" aria-live="polite">
        {active.label} section
      </span>
    </header>
  );
}
