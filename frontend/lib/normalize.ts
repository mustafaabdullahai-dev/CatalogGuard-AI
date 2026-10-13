/**
 * Normalising what the API returns into what the console renders.
 *
 * Phase 25 drove the console against the real API for the first time and
 * `/reconciliation` crashed the server with
 * `TypeError: Cannot read properties of undefined (reading 'toLocaleString')`.
 * The cause was not a rendering bug but a contract disagreement that had been
 * invisible for two phases:
 *
 * 1. **Every list endpoint returns a named collection with no envelope** —
 *    `{sources: [...]}`, `{products: [...]}`, `{conflicts: [...]}`. The console
 *    works in terms of `Page<T>`: `{items, total, limit, offset}` plus a named
 *    alias. So `page.total` was `undefined`, and `plural(total, ...)` calls
 *    `count.toLocaleString()`.
 * 2. **A source has no status in the API.** The console's source badges read
 *    `status`; the API sends `source_type` and nothing else.
 *
 * The rows themselves match — the console's types are the API's own field
 * names — so this module changes the envelope and the two missing source
 * fields, and nothing else. Mapping more than that would be inventing a second
 * vocabulary and drifting away from the contract the backend actually serves.
 *
 * Why it survived Phase 21 and Phase 24: the fixtures were written to the
 * *console's* shape, so the fixtures agreed with the console and both
 * disagreed with the backend. Every browser run before this one used a stub
 * serving those fixtures; every backend test asserted the response model the
 * backend returns. Each half was verified; the seam between them never was.
 */

/** A list endpoint's real response: a named array, with no envelope. */
type ListEnvelope<K extends string, T> = { [key in K]?: T[] } & Partial<{
  items: T[];
  total: number;
  limit: number;
  offset: number;
}>;

export interface PageEnvelope<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * Build the envelope from a named collection.
 *
 * `total` falls back to the number of rows received. That is exact when the
 * endpoint is unpaginated — which every current list endpoint is — and an
 * honest floor when one is not. `undefined` is the alternative, and undefined
 * is what crashed the page.
 */
export function toPage<K extends string, T>(
  payload: unknown,
  key: K,
  requested: { limit?: number; offset?: number } = {},
): PageEnvelope<T> {
  const body = (payload ?? {}) as ListEnvelope<K, T>;
  const items = Array.isArray(body[key]) ? (body[key] as T[]) : [];

  return {
    items,
    total: typeof body.total === "number" ? body.total : items.length,
    limit: typeof body.limit === "number" ? body.limit : (requested.limit ?? items.length),
    offset: typeof body.offset === "number" ? body.offset : (requested.offset ?? 0),
  };
}

/** The API's source row, plus the status the console shows and the API lacks. */
export interface ApiSource {
  id: string;
  name: string;
  source_type?: string | null;
  created_at?: string | null;
  last_sequence_timestamp?: string | null;
  record_count?: number | null;
}

export interface ConsoleSource {
  id: string;
  name: string;
  kind: string;
  status: string;
  record_count: number;
  created_at: string;
}

/**
 * A source's status, derived rather than invented.
 *
 * The API does not track one, and claiming a lifecycle state it does not send
 * would be a badge that means nothing. Records present is a fact the API does
 * send, so `active` and `idle` are reported from it, and `kind` carries the
 * source type the API does send.
 */
export function source(raw: ApiSource): ConsoleSource {
  const count = raw.record_count ?? 0;

  return {
    id: raw.id,
    name: raw.name,
    kind: raw.source_type ?? "unknown",
    status: count > 0 ? "active" : "idle",
    record_count: count,
    created_at: raw.created_at ?? "",
  };
}

/**
 * Search hits: the endpoint returns `{query, hits}`, and the console's search
 * expects the same rows the document list uses. `score` and `signals` are
 * present on the wire already.
 */
export interface ApiHit {
  chunk_id: string;
  document_id: string;
  document_title: string;
  section_reference: string | null;
  marketplace_code: string | null;
  content: string;
  score: number;
  signals: string[];
}
/* --- one normaliser per read ------------------------------------------ */

/**
 * Each returns the console's page type from the endpoint's real response.
 *
 * Only the envelope and the aliases differ; the rows are passed through, which
 * is deliberate — the console's row types are the API's field names, so any
 * further renaming here would be a second vocabulary to keep in step.
 */

export function productsPage<T>(payload: unknown, requested: { limit: number; offset: number }, alias: "products") {
  const page = toPage<"products", T>(payload, "products", requested);

  return { ...page, [alias]: page.items };
}

export function conflictsPage<T>(payload: unknown, requested: { limit: number; offset: number }, alias: "conflicts") {
  const page = toPage<"conflicts", T>(payload, "conflicts", requested);

  return { ...page, [alias]: page.items };
}

export function proposalsPage<T>(payload: unknown, requested: { limit: number; offset: number }, alias: "proposals") {
  const page = toPage<"proposals", T>(payload, "proposals", requested);

  return { ...page, [alias]: page.items };
}

export function runsPage<T>(payload: unknown, alias: "runs") {
  const page = toPage<"runs", T>(payload, "runs");

  return { ...page, [alias]: page.items };
}

export function documentsPage<T>(payload: unknown, alias: "documents") {
  const page = toPage<"documents", T>(payload, "documents");

  return { ...page, [alias]: page.items };
}

/**
 * Sources need their rows mapped, not just wrapped: the console reads `kind`
 * and `status`, and the API sends `source_type` and no status at all.
 */
export function sourcesPage(payload: unknown, alias: "sources") {
  const page = toPage<"sources", ApiSource>(payload, "sources");
  const items = page.items.map(source);

  return { ...page, items, [alias]: items };
}
