/**
 * Pagination and filter-parameter plumbing.
 *
 * Filters live in the query string, not in React state, so a filtered table is
 * a URL: shareable, back-button-correct, and it survives a reload without a
 * client store. Pure functions only — the arithmetic a page needs to render
 * "showing 51–100 of 340" is testable without a browser.
 */

export interface PageSpec {
  limit: number;
  offset: number;
  total: number;
}

export interface PageWindow extends PageSpec {
  /** 1-based index of the first row on this page, 0 when the page is empty. */
  from: number;
  /** 1-based index of the last row on this page, 0 when the page is empty. */
  to: number;
  /** Number of pages, at least 1 so "page 1 of 1" reads correctly. */
  pageCount: number;
  page: number;
  hasPrevious: boolean;
  hasNext: boolean;
}

export const PAGE_SIZES = [25, 50, 100, 200] as const;
export type PageSize = (typeof PAGE_SIZES)[number];

export const DEFAULT_PAGE_SIZE: PageSize = 50;

export function clampPageSize(value: string | null | undefined): PageSize {
  const parsed = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(parsed)) return DEFAULT_PAGE_SIZE;
  const match = PAGE_SIZES.find((size) => size === parsed);
  return match ?? DEFAULT_PAGE_SIZE;
}

/**
 * Resolve `?page=` and `?limit=` into a window, clamping rather than trusting.
 *
 * An offset past the end of the collection is clamped to the last page instead
 * of rendering an empty table: the data did not disappear, the request was out
 * of date, and saying so is more useful than an empty grid.
 */
export function pageWindow(params: { page?: string | null; limit?: number | null; total: number }): PageWindow {
  const limit = params.limit && params.limit > 0 ? Math.floor(params.limit) : DEFAULT_PAGE_SIZE;
  const requested = Number.parseInt(params.page ?? "", 10);
  const total = Math.max(0, params.total);
  const pageCount = Math.max(1, Math.ceil(total / limit));
  const page = Number.isFinite(requested) ? Math.min(Math.max(requested, 1), pageCount) : 1;
  const offset = (page - 1) * limit;
  const from = total === 0 ? 0 : offset + 1;
  const to = total === 0 ? 0 : Math.min(offset + limit, total);
  return {
    limit,
    offset,
    total,
    from,
    to,
    pageCount,
    page,
    hasPrevious: page > 1,
    hasNext: page < pageCount,
  };
}

/** Build a query string that preserves every filter except the ones changed. */
export function buildQuery(
  filters: Record<string, string | undefined>,
  overrides: Record<string, string | number | undefined | null> = {},
): string {
  const merged: Record<string, string | undefined> = { ...filters };
  for (const [key, value] of Object.entries(overrides)) {
    if (value === undefined || value === null || value === "") delete merged[key];
    else merged[key] = String(value);
  }
  const search = new URLSearchParams();
  for (const key of Object.keys(merged).sort()) {
    const value = merged[key];
    if (value !== undefined && value !== "") search.set(key, value);
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

/**
 * A link to another page of the same filtered view.
 *
 * Only `page` is overridden. The row count travels in the caller's filters
 * (as `page_size`), and adding a second `limit` parameter would put a key in
 * the URL that no page reads — a filter that looks like it does something.
 */
export function pageHref(basePath: string, filters: Record<string, string | undefined>, page: number): string {
  return `${basePath}${buildQuery(filters, { page })}`;
}

/**
 * Free-text filtering for the rows already on screen.
 *
 * The catalog endpoint has no `q` parameter, so a text filter cannot be honest
 * about rows on other pages. It is applied to the loaded page and the UI says
 * so rather than implying a workspace-wide result set.
 */
export function matchesText(fields: readonly (string | number | null | undefined)[], needle: string): boolean {
  const term = needle.trim().toLowerCase();
  if (term === "") return true;
  return fields.some((field) => (field ?? "").toString().toLowerCase().includes(term));
}
/**
 * `1 conflict` / `2 conflicts`.
 *
 * Every count on every screen goes through this: a console that says
 * "1 conflict(s)" reads as software that has not been looked at, and counts are
 * exactly the words an operator repeats in a ticket.
 */
export function plural(count: number, noun: string, pluralNoun?: string): string {
  const word = count === 1 ? noun : (pluralNoun ?? `${noun}s`);
  return `${count.toLocaleString()} ${word}`;
}
