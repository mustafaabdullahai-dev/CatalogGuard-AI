import { ComplianceSearch } from "@/components/ComplianceSearch";
import { Shell } from "@/components/Shell";
import { DataSourceNotice } from "@/components/StateBlocks";
import { getDocuments, pageContext, searchCompliance } from "@/lib/api";
import { formatRelative } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * Compliance center.
 *
 * Search over the chunked corpus with the citation attached to every hit, plus
 * the ingested document list. The page says in words what the retrieval layer
 * says in its `note`: this is evidence for a recommendation, never a
 * compliance decision (D-005 rules out a rule-cited verdict in the MVP).
 */
export default async function CompliancePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const query = first(params.q) ?? "";
  const context = await pageContext();
  const [results, documents] = await Promise.all([
    searchCompliance(context, { q: query, topK: 8 }),
    getDocuments(context),
  ]);

  return (
    <Shell
      title="Compliance center"
      subtitle={`${documents.data.total} document${documents.data.total === 1 ? "" : "s"} indexed · retrieval returns cited evidence, not a verdict`}
    >
      <DataSourceNotice source={results.source} reason={results.reason} />

      <ComplianceSearch initialQuery={query} initial={results.data} live={results.source === "live"} />

      <section className="card">
        <header className="card-header">
          <span className="card-title">Indexed documents</span>
          <span className="card-hint">{documents.data.total}</span>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Marketplace</th>
                <th>Kind</th>
                <th>Version</th>
                <th className="num">Chunks</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {documents.data.documents.length === 0 ? (
                <tr>
                  <td colSpan={6} className="empty">
                    No compliance document has been ingested, so retrieval has nothing to cite.
                  </td>
                </tr>
              ) : (
                documents.data.documents.map((document) => (
                  <tr key={document.id}>
                    <td>{document.title}</td>
                    <td>{document.marketplace_code ?? "internal"}</td>
                    <td style={{ color: "rgb(var(--text-muted))" }}>{document.document_kind ?? "—"}</td>
                    <td className="mono">{document.version_label ?? "—"}</td>
                    <td className="num">{document.chunk_count.toLocaleString()}</td>
                    <td style={{ color: "rgb(var(--text-subtle))", whiteSpace: "nowrap" }}>
                      {formatRelative(document.updated_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </Shell>
  );
}

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}