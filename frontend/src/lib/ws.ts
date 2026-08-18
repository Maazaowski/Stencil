// WebSocket base URL resolution.
//
// HTTP traffic is proxied to the backend via Next.js rewrites (see
// next.config.ts), but rewrites do NOT proxy WebSocket upgrades. So live
// progress sockets must target the backend directly. In Docker the backend is
// published on :8000 and `NEXT_PUBLIC_WS_URL` is baked in at build time; for
// local dev we fall back to the current host (works when the API is served on
// the same origin, e.g. a single dev proxy).
export function wsBase(): string {
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  if (configured && configured.trim()) {
    return configured.replace(/\/+$/, "");
  }
  if (typeof window !== "undefined") {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}`;
  }
  return "";
}

// Base for pipeline live-progress sockets (production invoice processing).
export function pipelineWsBase(): string {
  return wsBase();
}
