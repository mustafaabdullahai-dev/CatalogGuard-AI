import { AgentPanel } from "@/components/AgentPanel";
import { HealthMeters } from "@/components/HealthMeters";
import { NeutralBadge, RiskBadge } from "@/components/RiskBadge";
import { Shell } from "@/components/Shell";
import { DataSourceNotice, Unavailable } from "@/components/StateBlocks";
import { StatTile } from "@/components/StatTile";
import { dashboardCounts, getApprovals, pageContext } from "@/lib/api";
import { agentRuns, health, workspaceName } from "@/lib/fixtures";
import { formatConfidence, formatRelative, humanizeField } from "@/lib/format";
import { plural } from "@/lib/pagination";
import type { RiskLevel } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * Dashboard.
 *
 * Reads as an operations console, not an analytics page: open conflicts and
 * pending approvals are the two numbers that mean "someone is waiting on you",
 * so they lead. Every number here is a `total` the backend computed — a count
 * taken from one page of rows would be wrong the moment a workspace has more,
 * and there is no `/dashboard` route to do it properly, so the tile either
 * shows a real total or says that no route reports it.
 */
export default async function DashboardPage() {
  const context = await pageContext();
  const [counts, approvals] = await Promise.all([
    dashboardCounts(context),
    getApprovals(context, { limit: 6 }),
  ]);

  const live = approvals.source === "live";
  const items = approvals.data.items;
  const byLevel: Record<string, number> = { LOW: 0, MEDIUM: 0, HIGH: 0, CRITICAL: 0 };
  for (const item of items) byLevel[item.proposal.risk_level] = (byLevel[item.proposal.risk_level] ?? 0) + 1;
  const pending = approvals.data.total;
  const label = context.workspace?.name ?? workspaceName;

  return (
    <Shell
      title="Dashboard"
      subtitle={label}
      badges={{ "/approvals": pending }}
    >
      <DataSourceNotice source={approvals.source} reason={approvals.reason} />

      <div className="tile-grid">
        {counts.openConflicts === null ? (
          <Unavailable what="Open conflicts" />
        ) : (
          <StatTile
            label="Open conflicts"
            value={counts.openConflicts.toLocaleString()}
            hint="Conflicts with no resolution recorded"
          />
        )}
        {counts.pendingApprovals === null ? (
          <Unavailable what="Awaiting approval" />
        ) : (
          <StatTile
            label="Awaiting approval"
            value={counts.pendingApprovals.toLocaleString()}
            hint="Nothing is applied without a human decision"
          />
        )}
        {counts.productsTotal === null ? (
          <Unavailable what="Products" />
        ) : (
          <StatTile
            label="Products"
            value={counts.productsTotal.toLocaleString()}
            hint="Canonical products in the workspace"
          />
        )}
        {counts.criticalProposals === null && counts.highProposals === null ? (
          <Unavailable what="Risky changes" />
        ) : (
          <StatTile
            label="Risky changes"
            value={`${(counts.criticalProposals ?? 0).toLocaleString()} / ${(counts.highProposals ?? 0).toLocaleString()}`}
            delta="CRITICAL / HIGH"
            tone={counts.criticalProposals ? "down" : "neutral"}
          />
        )}
      </div>

      <div className="split">
        <section className="card">
          <header className="card-header">
            <span className="card-title">Pending approvals</span>
            <span className="card-hint">first {items.length} of {pending}</span>
          </header>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Risk</th>
                  <th>Change</th>
                  <th>Field</th>
                  <th className="num">Conf.</th>
                  <th>Validation</th>
                  <th>Raised</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="empty">
                      Nothing waiting. Changes appear here only after scoring.
                    </td>
                  </tr>
                ) : (
                  items.map((item) => (
                    <tr key={item.proposal.id}>
                      <td>
                        <RiskBadge level={item.proposal.risk_level as RiskLevel} />
                      </td>
                      <td>
                        <div className="change-line compact">
                          <span className="change-before">{String(item.proposal.current_value ?? "—")}</span>
                          <span className="change-arrow">→</span>
                          <strong className="change-after">{String(item.proposal.proposed_value ?? "—")}</strong>
                        </div>
                        <div className="mono small">{item.proposal.change_ref}</div>
                      </td>
                      <td>{humanizeField(item.proposal.field_name)}</td>
                      <td className="num">{formatConfidence(item.proposal.confidence)}</td>
                      <td>
                        <span
                          className={`badge badge-${item.proposal.validation_status === "validated" ? "LOW" : "MEDIUM"}`}
                        >
                          {item.proposal.validation_status}
                        </span>
                      </td>
                      <td style={{ color: "rgb(var(--text-subtle))", whiteSpace: "nowrap" }}>
                        {formatRelative(item.proposal.created_at)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>

        <div className="stack">
          <section className="card">
            <header className="card-header">
              <span className="card-title">Risk distribution</span>
              <span className="card-hint">first {plural(items.length, "queued item")}</span>
            </header>
            <div className="card-body stack">
              {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map((level) => (
                <div key={level} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <RiskBadge level={level} />
                  <div className="meter" style={{ flex: 1 }}>
                    <span
                      style={{
                        width: `${items.length === 0 ? 0 : ((byLevel[level] ?? 0) / items.length) * 100}%`,
                      }}
                    />
                  </div>
                  <span style={{ fontVariantNumeric: "tabular-nums", minWidth: 16, textAlign: "right" }}>
                    {byLevel[level] ?? 0}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className="card">
            <header className="card-header">
              <span className="card-title">Last reconciliation run</span>
              <span className="card-hint">from the run history</span>
            </header>
            <div className="card-body">
              {counts.lastRun ? (
                <dl className="kv">
                  <dt>Status</dt>
                  <dd>
                    <span
                      className={`badge badge-${counts.lastRun.status === "completed" ? "LOW" : "MEDIUM"}`}
                    >
                      {counts.lastRun.status}
                    </span>
                  </dd>
                  <dt>Products analyzed</dt>
                  <dd>{counts.lastRun.productsAnalyzed.toLocaleString()}</dd>
                  <dt>Conflicts found</dt>
                  <dd>{counts.lastRun.conflictsFound.toLocaleString()}</dd>
                  <dt>Started</dt>
                  <dd>{formatRelative(counts.lastRun.startedAt)}</dd>
                </dl>
              ) : (
                <p className="empty">
                  No reconciliation run is recorded
                  {live ? " in this workspace" : ""}, so nothing has been analyzed yet.
                </p>
              )}
            </div>
          </section>

          <HealthMeters health={health} />
          <AgentPanel runs={agentRuns} />
          <section className="card">
            <header className="card-header">
              <span className="card-title">Marketplace status</span>
              <span className="card-hint">no route</span>
            </header>
            <div className="card-body">
              <p className="field-hint">
                Connections are not exposed by any API route yet (Phase 19 built the seam, not the route), so
                this console cannot show whether a marketplace is reachable. The execution engine is wired to
                the same seam and reports a connection failure on the job row instead.
              </p>
              <div className="badge-row">
                <NeutralBadge>mock adapter only</NeutralBadge>
              </div>
            </div>
          </section>
        </div>
      </div>
    </Shell>
  );
}