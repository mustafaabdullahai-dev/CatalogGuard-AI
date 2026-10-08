import Link from "next/link";

import { pageHref } from "@/lib/pagination";
import type { PageWindow } from "@/lib/pagination";

/**
 * Pagination as links, not a client component.
 *
 * Each page is a URL, so the browser's back button, a reload and a shared link
 * all do the obvious thing without any JavaScript. The current page is marked
 * `aria-current` and the control is a labelled `nav` so it is not announced as
 * a pile of bare numbers.
 */
export function Pagination({
  path,
  filters,
  window,
  noun,
  shown,
}: {
  path: string;
  filters: Record<string, string | undefined>;
  window: PageWindow;
  noun: string;
  /** Rows actually rendered, which is fewer than the page when a filter applies. */
  shown?: number;
}) {
  if (window.total === 0) return null;
  const previous = pageHref(path, filters, window.page - 1);
  const next = pageHref(path, filters, window.page + 1);
  const pageSize = window.to - window.from + 1;
  // A filter applied on this page shrinks the rows without shrinking the page,
  // and a status line that still said "1–25 of 37" would describe rows nobody
  // can see. Two wordings, both true: the unfiltered case and the filtered one.
  const status =
    shown === undefined || shown === pageSize ? (
      `Showing ${window.from.toLocaleString()}–${window.to.toLocaleString()} of ${window.total.toLocaleString()} ${noun}`
    ) : (
      `Showing ${shown.toLocaleString()} of the ${pageSize.toLocaleString()} ${noun} on this page · ${window.total.toLocaleString()} in the workspace`
    );
  return (
    <nav className="pagination" aria-label={`${noun} pages`}>
      <span className="pagination-status" aria-live="polite">
        {status}
      </span>
      <span className="spacer" />
      {window.hasPrevious ? (
        <Link className="button" href={previous} rel="prev">
          Previous
        </Link>
      ) : (
        <span className="button button-disabled" aria-disabled="true">
          Previous
        </span>
      )}
      <span className="pagination-page">
        Page {window.page} of {window.pageCount}
      </span>
      {window.hasNext ? (
        <Link className="button" href={next} rel="next">
          Next
        </Link>
      ) : (
        <span className="button button-disabled" aria-disabled="true">
          Next
        </span>
      )}
    </nav>
  );
}