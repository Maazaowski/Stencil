/** Build frontend/API paths for model IDs that may contain dots (e.g. colt.standard.v1). */

export function modelDetailPath(modelId: string): string {
  return `/models/${encodeURIComponent(modelId)}`;
}

export function modelApiPath(modelId: string, suffix = ""): string {
  return `/models/${encodeURIComponent(modelId)}${suffix}`;
}

export function parseModelIdParam(segments: string | string[] | undefined): string {
  if (!segments) return "";
  const parts = Array.isArray(segments) ? segments : [segments];
  return decodeURIComponent(parts.join("/"));
}
