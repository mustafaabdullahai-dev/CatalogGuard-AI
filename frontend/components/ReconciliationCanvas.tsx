import { NeutralBadge, RiskBadge } from "@/components/RiskBadge";
import { displayValue } from "@/lib/diff";
import { formatConfidence, humanizeField } from "@/lib/format";
import { plural } from "@/lib/pagination";
import { canonicalAttributes } from "@/lib/provenance";
import type { ProductPanel } from "@/lib/provenance";
import type { CatalogSourceRef, RiskLevel } from "@/lib/types";

/**
 * Source → Truth → Analysis (§30's reconciliation workspace).
 *
 * The three panels are the provenance flow, not three columns of the same data:
 * the left says what each source claims, the middle says what the authority rule
 * settled on, and the right says what that disagreement turned into. A reviewer
 * tracing "why is this value navy" reads left to right and never leaves the row.
 *
 * A tie renders as a tie with both values visible. It is never resolved here:
 * D-020's rule is that a human decides, and a canvas that quietly picked a
 * winner would be the one screen that could make a change nobody approved.
 */
export function ReconciliationCanvas({
  sources,
  panels,
}: {
  sources: CatalogSourceRef[];
  panels: ProductPanel[];
}) {
  return (
    <div className="stack">
      <section className="card">
        <header className="card-header">
          <span className="card-title">Sources in this workspace</span>
          <span className="card-hint">{sources.length} connected</span>
        </header>
        <div className="card-body">
          {sources.length === 0 ? (
            <p className="empty">No catalog source is connected, so there is nothing to reconcile against.</p>
          ) : (
            <ul className="source-list">
              {sources.map((source) => (
                <li key={source.id}>
                  <span className="mono">{source.name}</span>
                  <span className="badge badge-neutral">{source.kind}</span>
                  <span
                    className={`badge badge-${
                      source.status === "failed"
                        ? "CRITICAL"
                        : source.status === "active"
                          ? "LOW"
                          : "MEDIUM"
                    }`}
                  >
                    {source.status}
                  </span>
                  <span className="field-hint">{source.record_count.toLocaleString()} records</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {panels.length === 0 ? (
        <section className="card">
          <p className="empty">
            No product on this page has a recorded disagreement, so the canvas has no row to draw.
          </p>
        </section>
      ) : (
        panels.map((panel) => (
          <section className="card" key={panel.product.id}>
            <header className="card-header">
              <span className="card-title">{panel.product.title ?? panel.product.sku}</span>
              <span className="card-hint mono">
                {panel.product.sku}
                {panel.product.gtin ? ` · GTIN ${panel.product.gtin}` : ""}
              </span>
              <span className="spacer" />
              <NeutralBadge>
                {plural(panel.conflictCount, "conflict")} · {plural(panel.proposalCount, "proposal")}
              </NeutralBadge>
              {panel.ties.length > 0 ? (
                <span className="badge badge-MEDIUM">{plural(panel.ties.length, "tie")}</span>
              ) : null}
            </header>
            <div className="canvas">
              {(["source", "truth", "analysis"] as const).map((panelName) => (
                <div className="canvas-column" key={panelName}>
                  <div className="canvas-title">
                    {panelName === "source" ? "Sources" : panelName === "truth" ? "Unified truth" : "Analysis"}
                  </div>
                  {panelName === "source" ? <SourceColumn panel={panel} /> : null}
                  {panelName === "truth" ? <TruthColumn panel={panel} /> : null}
                  {panelName === "analysis" ? <AnalysisColumn panel={panel} /> : null}
                </div>
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}

function SourceColumn({ panel }: { panel: ProductPanel }) {
  return (
    <div className="canvas-body">
      {panel.attributes.length === 0 ? (
        <p className="field-hint">No source disagrees about this product.</p>
      ) : (
        panel.attributes.map((attribute) => (
          <div className="attr-row" key={attribute.field}>
            <div className="attr-field mono">{attribute.field}</div>
            {attribute.claims.length === 0 ? (
              <div className="field-hint">no recorded claim</div>
            ) : (
              attribute.claims.map((claim) => (
                <div className="claim" key={`${attribute.field}-${claim.source}`}>
                  <NeutralBadge>{claim.source}</NeutralBadge>
                  <span className="claim-value">{displayValue(claim.value)}</span>
                  <span className="field-hint">{claim.rank === null ? "unranked" : `rank ${claim.rank}`}</span>
                </div>
              ))
            )}
          </div>
        ))
      )}
    </div>
  );
}

function TruthColumn({ panel }: { panel: ProductPanel }) {
  // The catalog's own attributes first, then the resolved disagreements — with
  // a field never listed twice. Rendering both `title` rows made the truth
  // column look like it held two competing truths, which is the one thing the
  // middle panel must not do.
  const resolvedFields = new Set(
    panel.attributes.filter((attribute) => attribute.resolution !== "missing").map((attribute) => attribute.field),
  );
  const agreed = canonicalAttributes(panel.product).filter(
    (attribute) => !resolvedFields.has(attribute.field),
  );
  return (
    <div className="canvas-body">
      {agreed.slice(0, 6).map((attribute) => (
        <div className="attr-row" key={`truth-${attribute.field}`}>
          <div className="attr-field mono">{attribute.field}</div>
          <div className="claim-value truth">{displayValue(attribute.value)}</div>
        </div>
      ))}
      {panel.attributes
        .filter((attribute) => attribute.resolution !== "missing")
        .map((attribute) => (
          <div className="attr-row" key={`resolved-${attribute.field}`}>
            <div className="attr-field mono">{attribute.field}</div>
            <div className="claim-value truth">
              {attribute.resolution === "tie" ? (
                <span className="text-warn">tie — no authority winner</span>
              ) : (
                displayValue(attribute.resolvedValue)
              )}
            </div>
          </div>
        ))}
    </div>
  );
}

function AnalysisColumn({ panel }: { panel: ProductPanel }) {
  if (panel.attributes.length === 0) {
    return (
      <div className="canvas-body">
        <p className="field-hint">Nothing to analyse: no disagreement was recorded for this product.</p>
      </div>
    );
  }
  return (
    <div className="canvas-body">
      {panel.attributes.map((attribute) => (
        <div className="attr-row" key={`analysis-${attribute.field}`}>
          <div className="attr-field mono">{humanizeField(attribute.field)}</div>
          {attribute.proposal ? (
            <div className="analysis-body">
              <div className="badge-row">
                <RiskBadge level={attribute.riskLevel as RiskLevel} />
                <span className="field-hint">
                  confidence {formatConfidence(attribute.proposal.confidence)}
                </span>
              </div>
              <p className="field-hint">{attribute.proposal.reason}</p>
              <div className="mono small">
                {displayValue(attribute.proposal.current_value)} → {displayValue(attribute.proposal.proposed_value)}
              </div>
            </div>
          ) : attribute.resolution === "tie" ? (
            <div className="analysis-body">
              <span className="badge badge-MEDIUM">tie</span>
              <p className="field-hint">
                {attribute.claims.length} sources with equal authority and no proposal. A human decides; the
                engine deliberately will not.
              </p>
            </div>
          ) : (
            <p className="field-hint">
              Resolved by the authority order ({attribute.resolution}); no proposal was created.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}