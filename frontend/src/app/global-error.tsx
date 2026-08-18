"use client";

/**
 * Last-resort boundary: catches errors thrown in the root layout itself, where
 * `error.tsx` cannot run. Must render its own <html>/<body>.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          display: "flex",
          minHeight: "100vh",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "1rem",
          fontFamily: "system-ui, sans-serif",
          padding: "2rem",
          textAlign: "center",
        }}
      >
        <h1 style={{ fontSize: "1.25rem", fontWeight: 600 }}>Stencil failed to start</h1>
        <p style={{ color: "#666", maxWidth: "32rem", fontSize: "0.875rem" }}>
          The application shell could not render. Reload the page; if this
          persists, check that the backend API is reachable.
        </p>
        {error.digest && (
          <p style={{ color: "#888", fontSize: "0.75rem", fontFamily: "monospace" }}>
            Reference: {error.digest}
          </p>
        )}
        <button
          onClick={reset}
          style={{
            padding: "0.5rem 1rem",
            borderRadius: "0.5rem",
            border: "1px solid #ccc",
            cursor: "pointer",
            background: "transparent",
          }}
        >
          Try again
        </button>
      </body>
    </html>
  );
}
