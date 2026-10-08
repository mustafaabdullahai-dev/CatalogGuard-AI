"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { NeutralBadge, RiskBadge } from "@/components/RiskBadge";
import { DiffTable } from "@/components/DiffTable";
import { displayValue, parseImpact, stageLabel, stageTone, verdictLabel } from "@/lib/diff";
import { formatConfidence, humanizeField } from "@/lib/format";
import type { ChangeProposalRef, RiskLevel, SimulationResponse } from "@/lib/types";
import { WriteApiError, simulate } from "@/lib/writeClient";

/**
 * The sandbox: run §18's chain against a copy and read the result.
 *
 * Simulating is a POST because it writes a simulation run — a human pressing
 * "Simulate" is the trigger, and the row records who asked. Nothing here
 * changes a product: the chain runs on a snapshot copy, which is the whole
 * reason the sandbox exists (D-020).
 */
export function SandboxPanel({
  proposals,
  live,
}: {
  proposals: ChangeProposalRef[];
  live: boolean;
}) {
  const router = useRouter();
  const [selected, setSelected] = useState<string>(proposals[0]?.id ?? "");
  const [result, setResult] = useState<SimulationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const proposal = proposals.find((candidate) => candidate.id === selected) ?? null;

  const run = async () => {
    if (!proposal) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await simulate(proposal.id));
      router.refresh();
    } catch (cause) {
      setResult(null);
      setError(
        cause instanceof WriteApiError ? cause.message : "The simulation request could not be sent.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <form
        className="toolbar filter-bar"
        action="/sandbox"
        method="get"
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
      >
        <span className="field-inline">
          <label className="field-label" htmlFor="sandbox-proposal">
            Proposal
          </label>
          <select
            className="input"
            id="sandbox-proposal"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            {proposals.length === 0 ? <option value="">No proposals loaded</option> : null}
            {proposals.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.change_ref} · {humanizeField(candidate.field_name)} · {candidate.risk_level}
              </option>
            ))}
          </select>
        </span>
        <button type="submit" className="button button-primary" disabled={!live || !proposal || busy}>
          {busy ? "Simulating…" : "Simulate this change"}
        </button>
        <span className="spacer" />
        {!live ? (
          <span style={{ color: "rgb(var(--text-subtle))", fontSize: 12 }}>
            The sandbox calls the backend, which is not connected on this screen.
          </span>
        ) : null}
      </form>

      {error ? (
        <div className="notice notice-bad" role="alert">
          {error}
        </div>
      ) : null}

      {proposal ? (
        <section className="card">
          <header className="card-header">
            <RiskBadge level={proposal.risk_level as RiskLevel} />
            <span className="card-title">{humanizeField(proposal.field_name)}</span>
            <span className="card-hint mono">{proposal.change_ref}</span>
          </header>
          <div className="card-body">
            <div className="change-line">
              <span className="change-before">{displayValue(proposal.current_value)}</span>
              <span className="change-arrow">→</span>
              <strong className="change-after">{displayValue(proposal.proposed_value)}</strong>
            </div>
            <p className="reason-line">{proposal.reason}</p>
            <dl className="kv">
              <dt>Confidence</dt>
              <dd>{formatConfidence(proposal.confidence)}</dd>
              <dt>Validation status</dt>
              <dd>{proposal.validation_status}</dd>
              <dt>SEO</dt>
              <dd>{proposal.seo_impact}</dd>
              <dt>Compliance</dt>
              <dd>{proposal.compliance_impact}</dd>
            </dl>
          </div>
        </section>
      ) : null}

      {result ? <SimulationResult result={result} /> : null}
    </div>
  );
}

/**
 * The chain's own report.
 *
 * A stage that did not run is rendered as `not run`, never as a pass (§18): the
 * compliance stage cannot answer on a workspace with no corpus, and a green
 * tick there would be the most expensive lie on the screen.
 */
export function SimulationResult({ result }: { result: SimulationResponse }) {
  return (
    <div className="stack">
      <section className="card">
        <header className="card-header">
          <span className="card-title">Chain result</span>
          <span className="card-hint">
            {result.change_ref} · validation {result.validation_status} · risk {result.overall_risk}
          </span>
        </header>
        <div className="card-body stack">
          <ul className="verdict-list">
            {(
              [
                ["schema", result.schema_valid],
                ["relationships", result.relationship_valid],
                ["compliance", result.compliance_valid],
              ] as const
            ).map(([label, value]) => {
              const verdict = verdictLabel(value);
              return (
                <li key={label}>
                  <span
                    className={`badge badge-${verdict.tone === "ok" ? "LOW" : verdict.tone === "bad" ? "CRITICAL" : "neutral"}`}
                  >
                    {verdict.text}
                  </span>
                  {label}
                </li>
              );
            })}
            <li>
              <span className={`badge badge-${result.seo_sensitive ? "MEDIUM" : "neutral"}`}>
                {result.seo_sensitive ? "sensitive" : "not sensitive"}
              </span>
              SEO fields
            </li>
          </ul>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Stage</th>
                  <th>Status</th>
                  <th>Findings</th>
                </tr>
              </thead>
              <tbody>
                {result.stages.map((stage) => (
                  <tr key={stage.name}>
                    <td className="mono">{stage.name}</td>
                    <td>
                      <span
                        className={`badge badge-${stageTone(stage.status) === "ok" ? "LOW" : stageTone(stage.status) === "bad" ? "CRITICAL" : "MEDIUM"}`}
                      >
                        {stageLabel(stage.status)}
                      </span>
                    </td>
                    <td style={{ color: "rgb(var(--text-muted))" }}>
                      {stage.detail === "" ? "—" : stage.detail}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {result.validation_errors.length > 0 ? (
            <ul className="evidence-list" role="list">
              {result.validation_errors.map((message) => (
                <li key={message} className="text-bad">
                  {message}
                </li>
              ))}
            </ul>
          ) : null}

          <p className="reason-line">{result.explanation}</p>
        </div>
      </section>

      <DiffTable diff={result.diff} />

      <section className="card">
        <header className="card-header">
          <span className="card-title">Impact</span>
          <span className="card-hint">§18 report</span>
        </header>
        <div className="card-body">
          <ImpactReport impact={result.impact_report} />
        </div>
      </section>
    </div>
  );
}

function ImpactReport({ impact }: { impact: Record<string, unknown> }) {
  const view = parseImpact(impact);
  return (
    <dl className="kv">
      <dt>Recommendation</dt>
      <dd>{view.recommendation ?? "not reported"}</dd>
      <dt>Affected channels</dt>
      <dd className="badge-row">
        {view.affectedChannels.length === 0 ? (
          <span className="field-hint">none</span>
        ) : (
          view.affectedChannels.map((channel) => <NeutralBadge key={channel}>{channel}</NeutralBadge>)
        )}
      </dd>
      <dt>Affected products</dt>
      <dd>{view.affectedProducts ?? "not reported"}</dd>
      <dt>Conflicts remaining</dt>
      <dd>{view.conflictsRemaining ?? "not reported"}</dd>
      <dt>Sources still disagreeing</dt>
      <dd>
        {view.disagreeing.length === 0 ? (
          "none"
        ) : (
          view.disagreeing.map((source) => (
            <NeutralBadge key={source}>{source}</NeutralBadge>
          ))
        )}
      </dd>
      <dt>Relationships</dt>
      <dd>
        {view.relationshipValid === null
          ? "not run"
          : view.relationshipValid
            ? "valid after the change"
            : "invalid after the change"}
      </dd>
    </dl>
  );
}