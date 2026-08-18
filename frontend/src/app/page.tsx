import { redirect } from "next/navigation";

/**
 * There is no dashboard.
 *
 * The operator arrives asking "what needs me today?" — so the queue IS the
 * landing page. The wall of stat cards that used to live here answered a
 * different question, asked monthly by a different person; it now lives at
 * /insights under System.
 *
 * A server-side redirect rather than a client one, so there is no flash of an
 * empty shell before the queue appears.
 */
export default function HomePage() {
  redirect("/invoices");
}
