import { NeutralBadge } from "@/components/RiskBadge";
import { Shell } from "@/components/Shell";
import { CarryFilters, FilterBar, SelectFilter, TextFilter } from "@/components/FilterBar";
import { Pagination } from "@/components/Pagination";
import { DataSourceNotice, EmptyState } from "@/components/StateBlocks";
import { getProducts, pageContext } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { PAGE_SIZES, clampPageSize, matchesText, pageWindow, plural } from "@/lib/pagination";

export const dynamic = "force-dynamic";

const STATUS_TONE: Record<string, string> = {
  active: "LOW",
  draft: "MEDIUM",
  archived: "neutral",
  suppressed: "CRITICAL",
};

/**
 * Catalog explorer.
 *
 * Filters are a GET form over the query string, so a filtered view is a URL
 * someone can share or return to. One honest limitation: `GET /products` takes
 * `limit` and `offset` and nothing else, so `q` is matched against the rows
 * loaded on this page and the filter bar says exactly that. Wiring it to a real
 * query parameter is a backend change, and pretending otherwise would let a
 * reviewer conclude a SKU does not exist because it sits on page 3.
 */
export default async function CatalogPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const context = await pageContext();
  const limit = clampPageSize(first(params.page_size));
  const q = first(params.q) ?? "";
  const status = first(params.status) ?? "";
  const requestedPage = Number.parseInt(first(params.page) ?? "", 10);

  // Two reads at most: the first resolves the total so the page can be clamped,
  // and a second only happens when the URL points past the end of the data.
  let products = await getProducts(context, {
    limit,
    offset: Number.isFinite(requestedPage) ? (requestedPage - 1) * limit : 0,
  });
  let window = pageWindow({ page: first(params.page), limit, total: products.data.total });
  if (window.offset !== products.data.offset) {
    products = await getProducts(context, { limit, offset: window.offset });
    window = pageWindow({ page: first(params.page), limit, total: products.data.total });
  }

  const rows = products.data.products.filter((product) => {
    if (status !== "" && product.status !== status) return false;
    return matchesText([product.sku, product.title, product.brand, product.gtin, product.category_path], q);
  });

  const filters: Record<string, string | undefined> = {
    q: q === "" ? undefined : q,
    status: status === "" ? undefined : status,
    page_size: String(limit),
  };

  return (
    <Shell title="Catalog explorer" subtitle={`${plural(window.total, "canonical product")} in this workspace`}>
      <DataSourceNotice source={products.source} reason={products.reason} />

      <FilterBar
        action="/catalog"
        resultNote={
          q === "" ? undefined : `“${q}” matched ${plural(rows.length, "row")} on this page — the API has no text filter.`
        }
      >
        <TextFilter
          name="q"
          label="Search this page"
          defaultValue={q}
          placeholder="SKU, title, brand, GTIN"
          hint="Loaded page only"
        />
        <SelectFilter
          name="status"
          label="Status"
          defaultValue={status}
          options={[
            { value: "", label: "All statuses" },
            { value: "active", label: "Active" },
            { value: "draft", label: "Draft" },
            { value: "suppressed", label: "Suppressed" },
            { value: "archived", label: "Archived" },
          ]}
        />
        <SelectFilter
          name="page_size"
          label="Rows"
          defaultValue={String(limit)}
          options={PAGE_SIZES.map((size) => ({ value: String(size), label: `${size} per page` }))}
        />
        <CarryFilters filters={filters} except={["q", "status", "page_size"]} />
      </FilterBar>

      {rows.length === 0 ? (
        <EmptyState
          title="Nothing on this page matches"
          body={
            q === "" && status === ""
              ? "This workspace has no products on the loaded page."
              : "The filters removed every row on this page. Clear them, or move to another page."
          }
        />
      ) : (
        <section className="card">
          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">Canonical products with identifiers, status and version</caption>
              <thead>
                <tr>
                  <th>SKU</th>
                  <th>Title</th>
                  <th>Brand</th>
                  <th>Category</th>
                  <th>Status</th>
                  <th>Version</th>
                  <th>Attributes</th>
                  <th>Reconciled</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((product) => (
                  <tr key={product.id}>
                    <td className="mono nowrap">{product.sku}</td>
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
                      <span className={`badge badge-${STATUS_TONE[product.status] ?? "neutral"}`}>{product.status}</span>
                    </td>
                    <td className="num">v{product.version}</td>
                    <td className="num">{Object.keys(product.attributes).length}</td>
                    <td style={{ color: "rgb(var(--text-subtle))", whiteSpace: "nowrap" }}>
                      {product.last_reconciled_at ? formatRelative(product.last_reconciled_at) : "never"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <Pagination path="/catalog" filters={filters} window={window} noun="products" shown={rows.length} />
      <p className="page-footnote">
        Columns mirror what <code>GET /products</code> returns. That route carries no conflict count, price or
        channel count, so this table does not invent them — the conflict view lives on the{" "}
        <a href="/reconciliation">reconciliation page</a>. Both filters here apply to the loaded page only,
        because the route accepts <code>limit</code> and <code>offset</code> and nothing else: a matching row on
        another page will not be found until that page is loaded.
      </p>
    </Shell>
  );
}

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}