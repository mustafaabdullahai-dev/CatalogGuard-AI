import { NeutralBadge, RiskBadge } from "@/components/RiskBadge";
import { Shell } from "@/components/Shell";
import { getProducts, usingFixtures } from "@/lib/api";
import { formatCompact, formatMoney } from "@/lib/format";
import type { Product, RiskLevel } from "@/lib/types";

export const dynamic = "force-dynamic";

const STATUS_BADGE: Record<Product["status"], string> = {
  active: "badge-LOW",
  draft: "badge-MEDIUM",
  archived: "badge-neutral",
  suppressed: "badge-CRITICAL",
};

/**
 * Band derived from open conflicts, mirroring the dashboard's rule.
 *
 * It is not the risk engine — nothing here proposes a change. It answers a
 * different question: which rows need attention first.
 */
function urgency(conflicts: number): RiskLevel {
  if (conflicts >= 5) return "HIGH";
  if (conflicts >= 2) return "MEDIUM";
  if (conflicts >= 1) return "LOW";
  return "LOW";
}

export default async function CatalogPage() {
  const products = await getProducts();
  const parents = products.filter((p) => p.is_parent).length;
  const conflicted = products.filter((p) => p.open_conflicts > 0).length;

  return (
    <Shell
      title="Catalog explorer"
      subtitle={`${products.length.toLocaleString()} products in view · ${parents} families · ${conflicted} with open conflicts`}
    >
      {usingFixtures() ? (
        <div className="notice">
          Showing fixture data. Set <code>CATALOGGUARD_WORKSPACE_ID</code> and run the
          backend to read a real workspace.
        </div>
      ) : null}

      <div className="toolbar">
        <input className="input" placeholder="Filter by SKU, title, brand, GTIN…" aria-label="Filter products" />
        <select className="input" aria-label="Status" defaultValue="all" style={{ maxWidth: 150 }}>
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="draft">Draft</option>
          <option value="suppressed">Suppressed</option>
        </select>
        <select className="input" aria-label="Conflicts" defaultValue="any" style={{ maxWidth: 160 }}>
          <option value="any">Any conflict count</option>
          <option value="with">Has conflicts</option>
          <option value="clean">Clean</option>
        </select>
        <span className="spacer" />
        <button type="button" className="button">
          Export CSV
        </button>
      </div>

      <section className="card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>SKU</th>
                <th>Title</th>
                <th>Brand</th>
                <th>Category</th>
                <th>Status</th>
                <th className="num">Price</th>
                <th className="num">Children</th>
                <th className="num">Channels</th>
                <th>Attention</th>
              </tr>
            </thead>
            <tbody>
              {products.map((product) => (
                <tr key={product.id}>
                  <td className="mono" style={{ whiteSpace: "nowrap" }}>
                    {product.sku}
                  </td>
                  <td>
                    <div style={{ fontWeight: 550 }}>{product.title ?? "—"}</div>
                    {product.is_parent ? (
                      <div style={{ marginTop: 2 }}>
                        <NeutralBadge>family</NeutralBadge>
                      </div>
                    ) : null}
                  </td>
                  <td>{product.brand ?? "—"}</td>
                  <td style={{ color: "rgb(var(--text-muted))" }}>{product.category_path ?? "—"}</td>
                  <td>
                    <span className={`badge ${STATUS_BADGE[product.status]}`}>{product.status}</span>
                  </td>
                  <td className="num">
                    {product.price === null ? "—" : formatMoney(product.price, product.currency)}
                  </td>
                  <td className="num">{product.child_count || "—"}</td>
                  <td className="num">{product.channel_count || "—"}</td>
                  <td>
                    {product.open_conflicts === 0 ? (
                      <NeutralBadge>clean</NeutralBadge>
                    ) : (
                      <RiskBadge level={urgency(product.open_conflicts)} score={product.open_conflicts} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <p style={{ color: "rgb(var(--text-subtle))", fontSize: 12, margin: 0 }}>
        Showing {formatCompact(products.length)} of {formatCompact(12480)} products. Filters run
        server-side once a workspace is connected.
      </p>
    </Shell>
  );
}
