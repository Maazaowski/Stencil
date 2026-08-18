"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { CommandPaletteProvider } from "@/components/layout/command-palette";

/** App chrome (rail + section nav + header) for every page except bare /login. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  if (pathname === "/login") {
    return <main className="h-full">{children}</main>;
  }

  return (
    <CommandPaletteProvider>
      <div className="flex h-full">
        <div className="hidden md:flex">
          <Sidebar />
        </div>
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <Header />
          {/* Tighter gutters — this is a dense working surface, not a brochure. */}
          <main className="flex-1 overflow-auto p-4 md:p-5">{children}</main>
        </div>
      </div>
    </CommandPaletteProvider>
  );
}
