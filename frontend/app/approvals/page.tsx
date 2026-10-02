import { RiskBadge, NeutralBadge } from "@/components/RiskBadge";
import { Shell } from "@/components/Shell";
import { getApprovals, usingFixtures } from "@/lib/api";
import { formatConfidence, formatRelative, humanizeField } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * Approval center.
 *
 * One change per card, and the card leads with the risk band, the change itself,
 * and the factors that produced the score. The buttons are rendered disabled on
 * purpose: approval mutates a live marketplace listing, and the backend route
 * that does it is not built yet. A live-looking approve button with no
 * implementation behind it is the most damaging thing this screen could ship.
 */
export default async function ApprovalsPage() {
  const requests = await getApprovals();
  const pending = requests.filter((r) => r.status === "pending");
  const ordered = [...pending].sort(
    (a, b) => b.risk_score - a.risk_score || Date.parse(a.requested_at) - Date.parse(b.requested_at),
  );
  const multi = pending.filter((r) => r.required_approvals > 1).length;

  return (
    <Shell
      title="Approval center"
      subtitle={`${pending.length} pending${multi > 0 ? ` · ${multi} need more than one approval` : ""}`}
      badges={{ "/approvals": pending.length }}
    >
      {usingFixtures() ? (
        <div className="notice">
          Showing fixture data. Approve and reject are not wired to a backend route yet —
          the controls below stay disabled until one exists.
        </div>
      ) : null}

      {ordered.length === 0 ? (
        <section className="card">
          <p className="empty">Nothing is waiting for a decision.</p>
        </section>
      ) : (
        <div className="stack">
          {ordered.map((request, index) => (
            <section className="card rise" key={request.id} style={{ animationDelay: `${index * 40}ms` }}>
              <header className="card-header">
                <RiskBadge level={request.risk_level} score={request.risk_score} />
                <span className="card-title">{humanizeField(request.field_name)}</span>
                <span className="card-hint mono">
                  {request.change_ref} · {request.product_id?.slice(0, 8)}
                </span>
              </header>
              <div className="card-body stack" style={{ gap: 14 }}>
                <div>
                  <div style={{ color: "rgb(var(--text-subtle))", fontSize: 12, marginBottom: 2 }}>
                    {request.product_title}
                  </div>
                  <div style={{ fontSize: 16 }}>
                    <span style={{ color: "rgb(var(--text-subtle))" }}>
                      {request.current_value ?? "—"}
                    </span>
                    <span style={{ margin: "0 8px", color: "rgb(var(--text-subtle))" }}>→</span>
                    <strong>{request.proposed_value ?? "—"}</strong>
                  </div>
                </div>

                <dl className="kv">
                  <dt>Why this change</dt>
                  <dd>{request.reason}</dd>
                  <dt>Confidence</dt>
                  <dd>{formatConfidence(request.confidence)}</dd>
                  <dt>Raised</dt>
                  <dd>{formatRelative(request.requested_at)}</dd>
                  <dt>Approvals</dt>
                  <dd>
                    {request.approvals_received} of {request.required_approvals}
                    {request.approvals_received < request.required_approvals ? (
                      <span style={{ color: "rgb(var(--text-subtle))" }}> — a second approver is required</span>
                    ) : null}
                  </dd>
                </dl>

                <div>
                  <div
                    style={{
                      color: "rgb(var(--text-muted))",
                      fontSize: 12,
                      marginBottom: 6,
                      fontWeight: 600,
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                    }}
                  >
                    Why it scores {request.risk_score}
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Factor</th>
                          <th className="num">Weight</th>
                          <th>Detail</th>
                        </tr>
                      </thead>
                      <tbody>
                        {request.risk_factors.map((factor) => (
                          <tr key={factor.code}>
                            <td className="mono">{factor.code}</td>
                            <td className="num">{factor.weight}</td>
                            <td style={{ color: "rgb(var(--text-muted))" }}>{factor.detail}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {request.competing_values.length > 1 ? (
                  <div>
                    <div
                      style={{
                        color: "rgb(var(--text-muted))",
                        fontSize: 12,
                        marginBottom: 6,
                        fontWeight: 600,
                        textTransform: "uppercase",
                        letterSpacing: "0.06em",
                      }}
                    >
                      Competing values
                    </div>
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Source</th>
                            <th>Value</th>
                            <th className="num">Authority rank</th>
                          </tr>
                        </thead>
                        <tbody>
                          {request.competing_values.map((entry) => (
                            <tr key={entry.source}>
                              <td>
                                <NeutralBadge>{entry.source}</NeutralBadge>
                              </td>
                              <td>{entry.value}</td>
                              <td className="num">{entry.rank}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : null}

                {request.channels.length > 0 ? (
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <span style={{ color: "rgb(var(--text-subtle))", fontSize: 12 }}>Affected channels:</span>
                    {request.channels.map((channel) => (
                      <NeutralBadge key={channel}>{channel}</NeutralBadge>
                    ))}
                  </div>
                ) : (
                  <div style={{ color: "rgb(var(--text-subtle))", fontSize: 12 }}>
                    No live channel is affected by this change.
                  </div>
                )}

                <div className="toolbar" style={{ borderTop: "1px solid rgb(var(--border))", paddingTop: 12 }}>
                  <button type="button" className="button button-primary" disabled>
                    Approve
                  </button>
                  <button type="button" className="button" disabled>
                    Approve with edits
                  </button>
                  <button type="button" className="button" disabled>
                    Reject
                  </button>
                  <button type="button" className="button button-ghost" disabled>
                    Request re-analysis
                  </button>
                  <span className="spacer" />
                  <span style={{ color: "rgb(var(--text-subtle))", fontSize: 12 }}>
                    Disabled: no execution route exists yet
                  </span>
                </div>
              </div>
            </section>
          ))}
        </div>
      )}
    </Shell>
  );
}
