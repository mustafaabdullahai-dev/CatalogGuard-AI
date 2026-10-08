import { ApprovalQueue } from "@/components/ApprovalQueue";
import { Shell } from "@/components/Shell";
import { CarryFilters, FilterBar, SelectFilter } from "@/components/FilterBar";
import { Pagination } from "@/components/Pagination";
import { DataSourceNotice, EmptyState } from "@/components/StateBlocks";
import { getApprovals, pageContext } from "@/lib/api";
import { clampPageSize, pageWindow } from "@/lib/pagination";

export const dynamic = "force-dynamic";

/**
 * Approval center.
 *
 * The buttons are live when a workspace is connected: they call the Phase 18
 * routes, which write an audit row per decision and refuse anything the
 * backend will not accept — a CRITICAL item that still needs its second
 * approver, or a proposal the sandbox has not validated. In fixture mode they
 * render disabled and say why, because a working-looking button with nothing
 * behind it is the most damaging thing this screen could ship.
 */
export default async function ApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const context = await pageContext();
  const limit = clampPageSize(first(params.page_size));
  const risk = first(params.risk) ?? "";

  const queue = await getApprovals(context, { limit, offset: 0, riskLevel: risk === "" ? undefined : risk });
  let window = pageWindow({ page: first(params.page), limit, total: queue.data.total });
  if (window.offset !== queue.data.offset) {
    const shifted = await getApprovals(context, {
      limit,
      offset: window.offset,
      riskLevel: risk === "" ? undefined : risk,
    });
    window = pageWindow({ page: first(params.page), limit, total: shifted.data.total });
    queue.data = shifted.data;
  }

  const items = queue.data.items;
  const needsTwo = items.filter((item) => (item.required_approvals ?? 1) > 1).length;
  const filters: Record<string, string | undefined> = {
    risk: risk === "" ? undefined : risk,
    page_size: String(limit),
  };

  return (
    <Shell
      title="Approval center"
      subtitle={[
        `${window.total.toLocaleString()} awaiting a decision`,
        needsTwo > 0
          ? `${needsTwo} on this page ${needsTwo === 1 ? "needs" : "need"} more than one approver`
          : null,
      ]
        .filter(Boolean)
        .join(" · ")}
      badges={{ "/approvals": window.total }}
    >
      <DataSourceNotice source={queue.source} reason={queue.reason} />

      <FilterBar action="/approvals">
        <SelectFilter
          name="risk"
          label="Risk band"
          defaultValue={risk}
          options={[
            { value: "", label: "All bands" },
            { value: "CRITICAL", label: "CRITICAL" },
            { value: "HIGH", label: "HIGH" },
            { value: "MEDIUM", label: "MEDIUM" },
            { value: "LOW", label: "LOW" },
          ]}
        />
        <SelectFilter
          name="page_size"
          label="Rows"
          defaultValue={String(limit)}
          options={[25, 50, 100, 200].map((size) => ({ value: String(size), label: `${size} per page` }))}
        />
        <CarryFilters filters={filters} except={["risk", "page_size"]} />
      </FilterBar>

      {items.length === 0 ? (
        <EmptyState title="Nothing is waiting" body="Every proposal in this workspace has been decided." />
      ) : (
        <ApprovalQueue items={items} live={queue.source === "live"} />
      )}

      <Pagination path="/approvals" filters={filters} window={window} noun="proposals" />
    </Shell>
  );
}

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}