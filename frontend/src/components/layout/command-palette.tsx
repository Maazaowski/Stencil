"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Search, CornerDownLeft } from "lucide-react";
import { useSettings } from "@/hooks/use-settings";
import { useSession } from "@/hooks/use-session";
import { NAV_SECTIONS, EXTRA_COMMANDS, visibleChildren, type NavChild } from "./nav-config";

/**
 * Command palette.
 *
 * With only three sections in the rail, depth is reached by search rather than
 * by scanning a list — and this is the discoverability answer for everything
 * demoted out of the old sixteen-item sidebar. For an operator who lives in the
 * tool all day it is faster than any navigation.
 */
type PaletteContext = { open: () => void; close: () => void };
const Ctx = React.createContext<PaletteContext>({ open: () => {}, close: () => {} });

export function useCommandPalette() {
  return React.useContext(Ctx);
}

export function CommandPaletteProvider({ children }: { children: React.ReactNode }) {
  const [isOpen, setIsOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [cursor, setCursor] = React.useState(0);
  const router = useRouter();
  const { data: settings } = useSettings();
  const { data: session } = useSession();

  // Reset on open/close in the handler, not in an effect — an effect here
  // triggers a cascading render on every toggle.
  const open = React.useCallback(() => {
    setQuery("");
    setCursor(0);
    setIsOpen(true);
  }, []);
  const close = React.useCallback(() => setIsOpen(false), []);

  const commands = React.useMemo<Array<NavChild & { section: string }>>(() => {
    const out: Array<NavChild & { section: string }> = [];
    for (const section of NAV_SECTIONS) {
      for (const child of visibleChildren(section, {
        isAdmin: session?.is_admin,
        debug: settings?.debug,
        signedIn: !!session,
      })) {
        out.push({ ...child, section: section.label });
      }
    }
    for (const extra of EXTRA_COMMANDS) out.push({ ...extra, section: "System" });
    return out;
  }, [session, settings?.debug]);

  const results = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) =>
      `${c.section} ${c.label} ${c.hint ?? ""}`.toLowerCase().includes(q),
    );
  }, [commands, query]);

  // ⌘K / Ctrl+K from anywhere.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setIsOpen((v) => {
          if (!v) {
            setQuery("");
            setCursor(0);
          }
          return !v;
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const selected = Math.min(cursor, Math.max(results.length - 1, 0));

  const run = (c: NavChild) => {
    close();
    router.push(c.href);
  };

  return (
    <Ctx.Provider value={{ open, close }}>
      {children}
      <Dialog open={isOpen} onOpenChange={setIsOpen}>
        <DialogContent
          showCloseButton={false}
          className="top-[18%] max-w-lg translate-y-0 gap-0 overflow-hidden p-0"
        >
          <DialogTitle className="sr-only">Command palette</DialogTitle>

          <div className="flex items-center gap-2 border-b border-border px-3">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <input
              type="search"
              autoFocus
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setCursor(0);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setCursor((c) => Math.min(c + 1, results.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setCursor((c) => Math.max(c - 1, 0));
                } else if (e.key === "Enter" && results[selected]) {
                  e.preventDefault();
                  run(results[selected]);
                }
              }}
              placeholder="Go to…"
              aria-label="Search commands"
              className="h-11 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>

          <ul className="max-h-80 overflow-y-auto p-1" role="listbox" aria-label="Commands">
            {results.length === 0 && (
              <li className="px-3 py-6 text-center text-sm text-muted-foreground">
                Nothing matches “{query}”.
              </li>
            )}
            {results.map((c, i) => (
              <li key={`${c.section}-${c.href}`}>
                <button
                  type="button"
                  role="option"
                  aria-selected={i === selected}
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => run(c)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors",
                    i === selected ? "bg-accent text-foreground" : "text-muted-foreground",
                  )}
                >
                  <c.icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[0.8125rem] text-foreground">
                      {c.label}
                    </span>
                    {c.hint && (
                      <span className="block truncate text-[0.6875rem]">{c.hint}</span>
                    )}
                  </span>
                  <span className="label-mono shrink-0">{c.section}</span>
                  {i === selected && (
                    <CornerDownLeft className="h-3 w-3 shrink-0" aria-hidden="true" />
                  )}
                </button>
              </li>
            ))}
          </ul>
        </DialogContent>
      </Dialog>
    </Ctx.Provider>
  );
}
