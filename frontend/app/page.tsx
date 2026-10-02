import { AgentPanel } from "@/components/AgentPanel";
import { HealthMeters } from "@/components/HealthMeters";
import { RiskBadge, NeutralBadge } from "@/components/RiskBadge";
import { Shell } from "@/components/Shell";
import { StatTile } from "@/components/StatTile";
import {
  getAgentRuns,
  getApprovals,
  getDashboard,
  getHealthScore,
  usingFixtures,
} from "@/lib/api";
import { formatConfidence, formatRelative, humanizeField } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * Dashboard.
 *
 * Reads as an operations console, not an analytics page: open conflicts and
 * pending approvals are the two numbers that mean "someone is waiting on you",
 * so they lead. Risk distribution sits beside them because a critical item
 * buried in a table is a critical item nobody reads.
 */
export default async function DashboardPage() {
  const [summary, health, approvals, runs] = await Promise.all([
    getDashboard(),
    getHealthScore(),
    getApprovals(),
    getAgentRuns(),
  ]);

  const byLevel: Record<string, number> = { LOW: 0, MEDIUM: 0, HIGH: 0, CRITICAL: 0 };
  for (const request of approvals) byLevel[request.risk_level] += 1;
  const needingReview = approvals.filter((a) => a.status === "pending");

  return (
    <Shell
      title="Dashboard"
      subtitle={summary.workspace_name}
      badges={{ "/approvals": approvals.filter((a) => a.status === "pending").length }}
    >
      {usingFixtures() ? (
        <div className="notice">
          Showing fixture data. Set <code>CATALOGGUARD_WORKSPACE_ID</code> and run the
          backend to read a real workspace.
        </div>
      ) : null}

      <div className="tile-grid">
        <StatTile
          label="Open conflicts"
          value={summary.conflicts_open}
          hint={`${summary.conflicts_tied} are ties with no authority winner`}
        />
        <StatTile
          label="Awaiting approval"
          value={summary.proposals_pending}
          hint="Nothing is applied without a human decision"
        />
        <StatTile
          label="Products changed (24h)"
          value={summary.products_changed_24h.toLocaleString()}
          delta={`of ${summary.products_total.toLocaleString()} total`}
        />
        <StatTile
          label="Executions today"
          value={summary.executions_today}
          delta={summary.executions_failed > 0 ? `${summary.executions_failed} failed` : "none failed"}
          tone={summary.executions_failed > 0 ? "down" : "up"}
        />
      </div>

      <div className="split">
        <section className="card">
          <header className="card-header">
            <span className="card-title">Pending approvals</span>
            <span className="card-hint">highest risk first</span>
          </header>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Risk</th>
                  <th>Product</th>
                  <th>Field</th>
                  <th>Change</th>
                  <th className="num">Conf.</th>
                  <th>Raised</th>
                </tr>
              </thead>
              <tbody>
                {needingReview.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="empty">
                      Nothing waiting. Changes appear here only after scoring.
                    </td>
                  </tr>
                ) : (
                  needingReview.map((request) => (
                    <tr key={request.id}>
                      <td>
                        <RiskBadge level={request.risk_level} score={request.risk_score} />
                      </td>
                      <td>
                        <div style={{ fontWeight: 550 }}>{request.product_title ?? request.product_id}</div>
                        <div className="mono" style={{ color: "rgb(var(--text-subtle))", fontSize: 11 }}>
                          {request.product_id?.slice(0, 8)}
                        </div>
                      </td>
                      <td>{humanizeField(request.field_name)}</td>
                      <td>
                        <div style={{ maxWidth: 260 }}>
                          <span style={{ color: "rgb(var(--text-subtle))" }}>
                            {request.current_value ?? "—"}
                          </span>
                          {" → "}
                          <strong>{request.proposed_value ?? "—"}</strong>
                        </div>
                        {request.channels.length > 0 ? (
                          <div style={{ marginTop: 4 }}>
                            {request.channels.map((channel) => (
                              <NeutralBadge key={channel}>{channel}</NeutralBadge>
                            ))}
                          </div>
                        ) : null}
                      </td>
                      <td className="num">{formatConfidence(request.confidence)}</td>
                      <td style={{ color: "rgb(var(--text-subtle))", whiteSpace: "nowrap" }}>
                        {formatRelative(request.requested_at)}
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
              <span className="card-hint">pending proposals</span>
            </header>
            <div className="card-body stack">
              {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map((level) => (
                <div key={level} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <RiskBadge level={level} />
                  <div className="meter" style={{ flex: 1 }}>
                    <span
                      style={{
                        width: `${needingReview.length === 0 ? 0 : (byLevel[level] / needingReview.length) * 100}%`,
                      }}
                    />
                  </div>
                  <span style={{ fontVariantNumeric: "tabular-nums", minWidth: 16, textAlign: "right" }}>
                    {byLevel[level]}
                  </span>
                </div>
              ))}
            </div>
          </section>
          <HealthMeters health={health} />
          <AgentPanel runs={runs} />
        </div>
      </div>
    </Shell>
  );
}
