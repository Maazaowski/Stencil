import {
  AlertTriangle,
  Boxes,
  Bug,
  Database,
  FileText,
  FlaskConical,
  Gauge,
  Landmark,
  ScrollText,
  Settings,
  Table,
  Upload,
  UserCircle2,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";

/**
 * Navigation — three destinations, not sixteen.
 *
 * The old sidebar gave "Field Schemas" the same prominence as the daily invoice
 * queue. The operator's job is three things: work the queue, configure how a
 * supplier is handled, administer the system. Everything else is a sub-view of
 * one of those, reachable by tab or by the command palette.
 *
 * Routes are unchanged — this is an information-architecture change, not a
 * routing one, so nothing breaks and existing links keep working.
 */

export type NavChild = {
  href: string;
  label: string;
  /** Shown in the section rail and the command palette. */
  hint?: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  debugOnly?: boolean;
  sessionOnly?: boolean;
};

export type NavSection = {
  id: string;
  href: string;
  label: string;
  icon: LucideIcon;
  /** Any route under these prefixes lights this section up. */
  match: string[];
  children: NavChild[];
};

export const NAV_SECTIONS: NavSection[] = [
  {
    id: "work",
    href: "/invoices",
    label: "Work",
    icon: FileText,
    // Exceptions is a FILTER on invoices, not a separate object — keeping it as
    // its own destination meant two lists for one job, with counts that disagreed.
    match: ["/invoices", "/exceptions", "/upload", "/"],
    children: [
      { href: "/invoices", label: "Queue", hint: "Everything flowing through", icon: FileText },
      { href: "/exceptions", label: "Needs review", hint: "Failed and variance", icon: AlertTriangle },
      { href: "/upload", label: "Upload", hint: "Add an invoice by hand", icon: Upload },
    ],
  },
  {
    id: "suppliers",
    href: "/profiles",
    label: "Suppliers",
    icon: Users,
    // Profiles, Accounts and Models all answer "how do we handle supplier X?".
    match: ["/profiles", "/accounts", "/models", "/field-schemas", "/output-specs"],
    children: [
      { href: "/profiles", label: "Profiles", hint: "Per-supplier extraction config", icon: Users },
      { href: "/accounts", label: "Accounts", hint: "Folders on disk", icon: Landmark },
      { href: "/models", label: "Templates", hint: "The cut stencils", icon: Boxes },
      { href: "/field-schemas", label: "Field schemas", hint: "What we extract", icon: Database },
      { href: "/output-specs", label: "Output specs", hint: "What we deliver", icon: Table },
    ],
  },
  {
    id: "system",
    href: "/settings",
    label: "System",
    icon: Settings,
    match: ["/settings", "/insights", "/logs", "/users", "/account", "/ai-calls", "/evals"],
    children: [
      { href: "/settings", label: "Settings", hint: "Provider, thresholds, watcher", icon: Settings },
      { href: "/insights", label: "Insights", hint: "Cost and volume over time", icon: Gauge },
      { href: "/logs", label: "Audit & usage", hint: "Processing trail and AI spend", icon: ScrollText },
      { href: "/users", label: "Users", hint: "People and roles", icon: UserCog, adminOnly: true },
      { href: "/account", label: "My account", hint: "Password and session", icon: UserCircle2, sessionOnly: true },
      { href: "/ai-calls", label: "AI calls", hint: "Captured prompts", icon: Bug, debugOnly: true },
      { href: "/evals", label: "Evals", hint: "Labelled regression corpus", icon: FlaskConical, debugOnly: true },
    ],
  },
];

/** Reserved for commands that are not also nav destinations. */
export const EXTRA_COMMANDS: NavChild[] = [];

export function sectionForPath(pathname: string): NavSection | undefined {
  return NAV_SECTIONS.find((s) =>
    s.match.some((m) =>
      m === "/" ? pathname === "/" : pathname === m || pathname.startsWith(m + "/"),
    ),
  );
}

export function visibleChildren(
  section: NavSection,
  { isAdmin, debug, signedIn }: { isAdmin?: boolean; debug?: boolean; signedIn?: boolean },
): NavChild[] {
  return section.children.filter((c) => {
    if (c.adminOnly && !isAdmin) return false;
    if (c.debugOnly && !debug) return false;
    if (c.sessionOnly && !signedIn) return false;
    return true;
  });
}
