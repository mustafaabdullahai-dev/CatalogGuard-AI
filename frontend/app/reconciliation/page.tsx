import { Shell } from "@/components/Shell";
import { ReconciliationCanvas } from "@/components/ReconciliationCanvas";
import { DataSourceNotice, EmptyState } from "@/components/StateBlocks";
import { getConflicts, getProducts, getProposals, getRuns, getSources, pageContext } from "@/lib/api";
import { buildProductPanel } from "@/lib/provenance";
import type { ProductPanel } from "@/lib/provenance";
import { formatRelative } from "@/lib/format";
import { plural } from "@/lib/pagination";

export const dynamic = "force-dynamic";

/**
 * Reconciliation workspace: Source → Truth → Analysis.
 *
 * The panel is assembled from the rows the API already returns — conflicts
 * carry the competing values with their authority ranks, proposals carry the
 * winning value and why — because there is no provenance endpoint and inventing
 * one client-side would have meant computing authority in the browser.
 */
export default async function ReconciliationPage() {
  const context = await pageContext();
  const [products, conflicts, proposals, sources, runs] = await Promise.all([
    getProducts(context, { limit: 25 }),
    getConflicts(context, { limit: 200 }),
    getProposals(context, { limit: 200 }),
    getSources(context),
    getRuns(context),
  ]);

  const byProduct = new Map<string, ProductPanel>();
  for (const product of products.data.products) {
    const panel = buildProductPanel(product, conflicts.data.conflicts, proposals.data.proposals);
    if (panel.conflictCount > 0 || panel.proposalCount > 0) byProduct.set(product.id, panel);
  }
  const panels = Array.from(byProduct.values()).slice(0, 10);
  const source = products.source;

  return (
    <Shell
      title="Reconciliation workspace"
      subtitle={`${plural(conflicts.data.total, "open conflict")} · ${plural(proposals.data.total, "proposal")} · ${plural(sources.data.total, "source")}`}
    >
      <DataSourceNotice source={source} reason={products.reason} />

      <section className="card">
        <header className="card-header">
          <span className="card-title">Reconciliation runs</span>
          <span className="card-hint">most recent first</span>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Status</th>
                <th className="num">Catalog version</th>
                <th className="num">Products analyzed</th>
                <th className="num">Conflicts</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.runs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="empty">
                    No reconciliation run has been recorded, so the conflict rows below are whatever was
                    detected outside a run.
                  </td>
                </tr>
              ) : (
                runs.data.runs.map((run) => (
                  <tr key={run.id}>
                    <td className="mono">{run.id.slice(0, 8)}</td>
                    <td>
                      <span className={`badge badge-${run.status === "completed" ? "LOW" : "MEDIUM"}`}>
                        {run.status}
                      </span>
                    </td>
                    <td className="num">v{run.catalog_version}</td>
                    <td className="num">{run.products_analyzed.toLocaleString()}</td>
                    <td className="num">{run.conflicts_found.toLocaleString()}</td>
                    <td style={{ color: "rgb(var(--text-subtle))", whiteSpace: "nowrap" }}>
                      {formatRelative(run.started_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      {panels.length === 0 ? (
        <EmptyState
          title="No product on this page has a recorded disagreement"
          body="The canvas draws one row per product that has a conflict or a proposal. Run a reconciliation, or open the catalog explorer to see products."
        />
      ) : (
        <ReconciliationCanvas sources={sources.data.sources} panels={panels} />
      )}

      <p className="page-footnote">
        Conflicts are read with <code>open_only=true</code> and a 200-row window; the workspace total above is
        the API&apos;s own count. A field where two sources hold equal authority is shown as a tie and is
        deliberately left unresolved — the engine will not pick, and neither does this page.
      </p>
    </Shell>
  );
}