import { formatRelative } from "@/lib/format";
import type { AgentRun, AgentState } from "@/lib/types";

const STATE_BADGE: Record<AgentState, string> = {
  running: "badge-HIGH",
  waiting: "badge-MEDIUM",
  done: "badge-LOW",
  failed: "badge-CRITICAL",
};

const STATE_DOT: Record<AgentState, string> = {
  running: "dot dot-live",
  waiting: "dot",
  done: "dot",
  failed: "dot",
};

/**
 * Agent activity as a log of what each node did, not a chat window.
 *
 * The node name and its finding are the payload. A stage that reports "no
 * problems" is still shown, because "the cycle checker ran and found nothing" is
 * information a reviewer needs before approving.
 */
export function AgentPanel({ runs }: { runs: AgentRun[] }) {
  return (
    <section className="card">
      <header className="card-header">
        <span className="card-title">Agent activity</span>
        <span className="card-hint">latest run</span>
      </header>
      <div className="card-body">
        {runs.length === 0 ? (
          <p className="empty">No agent runs yet.</p>
        ) : (
          <div>
            {runs.map((run) => (
              <div className="agent-row" key={run.id}>
                <span className={STATE_DOT[run.state]} />
                <div style={{ minWidth: 0 }}>
                  <div className="mono" style={{ fontWeight: 600 }}>
                    {run.node}
                  </div>
                  <div style={{ color: "rgb(var(--text-muted))", fontSize: 12 }}>{run.detail}</div>
                </div>
                <span className={`badge ${STATE_BADGE[run.state]}`} style={{ marginLeft: "auto" }}>
                  {run.state}
                </span>
                <span style={{ color: "rgb(var(--text-subtle))", fontSize: 11, whiteSpace: "nowrap" }}>
                  {run.duration_ms !== null ? `${(run.duration_ms / 1000).toFixed(1)}s` : formatRelative(run.started_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
