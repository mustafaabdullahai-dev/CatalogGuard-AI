"use client";

import { useState } from "react";

import { displayValue } from "@/lib/diff";
import { plural } from "@/lib/pagination";
import type { ComplianceSearchResponse } from "@/lib/types";
import { WriteApiError, searchCompliance } from "@/lib/writeClient";

/**
 * Compliance search with its citations.
 *
 * The results panel is the product: a hit without the section reference it came
 * from is an assertion, and §13 asks for "the exact rule or document section".
 * The label above the results says the same thing the API's `note` says — this
 * is evidence for a recommendation, not a compliance decision (D-005) — rather
 * than leaving the distinction to the reader.
 */
export function ComplianceSearch({
  initialQuery,
  initial,
  live,
}: {
  initialQuery: string;
  initial: ComplianceSearchResponse;
  live: boolean;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [result, setResult] = useState<ComplianceSearchResponse>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const term = query.trim();
    if (term === "" || !live) {
      setError(
        live ? "Enter a query." : "Search calls the backend, which is not connected on this screen.",
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await searchCompliance(term));
    } catch (cause) {
      setError(cause instanceof WriteApiError ? cause.message : "The search could not be sent.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <form className="toolbar filter-bar" action="/compliance" method="get" onSubmit={submit} role="search">
        <span className="field-inline grow">
          <label className="field-label" htmlFor="compliance-query">
            Rule text, claim or category
          </label>
          <input
            className="input"
            id="compliance-query"
            name="q"
            type="search"
            value={query}
            placeholder="e.g. title length, material claims"
            onChange={(event) => setQuery(event.target.value)}
          />
        </span>
        <button type="submit" className="button button-primary" disabled={!live || busy}>
          {busy ? "Searching…" : "Search"}
        </button>
        <span className="spacer" />
        <span className="field-hint">
          Hybrid retrieval: keyword <code>ts_rank</code> plus vector distance, merged and reranked.
        </span>
      </form>

      {error ? (
        <div className="notice notice-bad" role="alert">
          {error}
        </div>
      ) : null}

      <section className="card">
        <header className="card-header">
          <span className="card-title">Results</span>
          <span className="card-hint">
            {plural(result.hits.length, "hit")} from {plural(result.candidates_considered, "candidate chunk")}
          </span>
        </header>
        <div className="card-body stack">
          <p className="compliance-note">{result.note}</p>
          {result.signals.length > 0 ? (
            <p className="field-hint">Signals: {result.signals.join(", ")}</p>
          ) : null}
          {result.hits.length === 0 ? (
            <p className="empty">
              {result.query === ""
                ? "Enter a query to search the compliance corpus."
                : "Nothing matched. An empty result means no chunk matched, not that the workspace has no rules."}
            </p>
          ) : (
            result.hits.map((hit) => (
              <article className="hit" key={hit.chunk_id}>
                <header className="hit-header">
                  <span className="hit-title">{hit.document_title}</span>
                  {hit.section_reference ? (
                    <span className="badge badge-neutral mono">{hit.section_reference}</span>
                  ) : null}
                  {hit.marketplace_code ? (
                    <span className="badge badge-neutral">{hit.marketplace_code}</span>
                  ) : null}
                  <span className="hit-score" title="Deterministic rerank score">
                    {hit.score.toFixed(3)}
                  </span>
                </header>
                <p className="hit-content">{hit.content}</p>
                <footer className="field-hint">
                  chunk {displayValue(hit.chunk_id).slice(0, 8)} · signals: {hit.signals.join(", ") || "none"}
                </footer>
              </article>
            ))
          )}
        </div>
      </section>
    </div>
  );
}