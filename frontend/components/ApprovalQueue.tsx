"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { NeutralBadge, RiskBadge } from "@/components/RiskBadge";
import { formatConfidence, humanizeField } from "@/lib/format";
import { displayValue, verdictLabel } from "@/lib/diff";
import type { ApprovalDetail, ApprovalQueueItem, BulkItemResult, DecisionResponse, RiskLevel } from "@/lib/types";
import {
  WriteApiError,
  approve,
  approveAllLowRisk,
  decideBulk,
  fetchApprovalDetail,
  modify,
  reject,
  requestReanalysis,
} from "@/lib/writeClient";

type Outcome =
  | { kind: "idle" }
  | { kind: "busy"; what: string }
  | { kind: "done"; what: string; detail: string; auditId?: string }
  | { kind: "failed"; what: string; detail: string };

/**
 * The approval center's controls.
 *
 * Four decisions, one at a time or in bulk, and every one of them calls the
 * Phase 18 route that writes an audit row. The rules the UI repeats from the
 * backend rather than recomputing: a proposal that has not been validated by
 * the sandbox says so, a CRITICAL item still needs its second approver, and a
 * refused call reports the refusal instead of retrying — D-020 declined
 * auto-approval, so nothing here fires without a click.
 *
 * Accessibility: dialogs are native `<dialog>` elements, which bring the focus
 * trap, Escape-to-close and `aria-modal` behaviour with them; results are
 * announced through a polite live region rather than by colour.
 */
export function ApprovalQueue({
  items,
  live,
}: {
  items: ApprovalQueueItem[];
  live: boolean;
}) {
  const router = useRouter();
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });
  const [selected, setSelected] = useState<string[]>([]);
  const [detailFor, setDetailFor] = useState<string | null>(null);

  const run = useCallback(
    async (what: string, call: () => Promise<DecisionResponse | { results: BulkItemResult[]; done: number; refused: number }>) => {
      setOutcome({ kind: "busy", what });
      try {
        const result = await call();
        if ("detail" in result) {
          setOutcome({ kind: "done", what, detail: result.detail, auditId: result.audit_id });
        } else {
          setOutcome({
            kind: result.refused > 0 ? "failed" : "done",
            what,
            detail: `${result.done} done, ${result.refused} refused of ${result.results.length} considered.`,
          });
        }
        router.refresh();
      } catch (error) {
        setOutcome({
          kind: "failed",
          what,
          detail: error instanceof WriteApiError ? error.message : "The request could not be sent.",
        });
      }
    },
    [router],
  );

  const toggle = (id: string) => {
    setSelected((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  };

  return (
    <div className="stack">
      <div className="toolbar" style={{ borderTop: "none", paddingTop: 0 }}>
        <span className="selection-count" aria-live="polite">
          {selected.length} selected
        </span>
        <button
          type="button"
          className="button"
          disabled={!live || selected.length === 0}
          onClick={() =>
            run("Approve selected", () => decideBulk("approve", selected, "Bulk approval from the console"))
          }
        >
          Approve selected
        </button>
        <button
          type="button"
          className="button"
          disabled={!live || selected.length === 0}
          onClick={() =>
            run("Reject selected", () => decideBulk("reject", selected, "Bulk rejection from the console"))
          }
        >
          Reject selected
        </button>
        <button
          type="button"
          className="button button-ghost"
          disabled={!live}
          onClick={() => run("Approve all low risk", () => approveAllLowRisk("Bulk low-risk approval from the console"))}
        >
          Approve all LOW risk
        </button>
        <span className="spacer" />
        {!live ? (
          <span style={{ color: "rgb(var(--text-subtle))", fontSize: 12 }}>
            Read-only: fixtures are on screen, so there is nothing to decide against.
          </span>
        ) : null}
      </div>

      <OutcomeBanner outcome={outcome} />

      {items.length === 0 ? (
        <section className="card">
          <p className="empty">Nothing is waiting for a decision.</p>
        </section>
      ) : (
        items.map((item) => (
          <ApprovalCard
            key={item.proposal.id}
            item={item}
            live={live}
            selected={selected.includes(item.proposal.id)}
            onToggle={() => toggle(item.proposal.id)}
            onInspect={() => setDetailFor(item.proposal.id)}
            run={run}
          />
        ))
      )}

      {detailFor ? (
        <ApprovalDetailDialog
          proposalId={detailFor}
          onClose={() => setDetailFor(null)}
          run={run}
          live={live}
        />
      ) : null}
    </div>
  );
}

function OutcomeBanner({ outcome }: { outcome: Outcome }) {
  if (outcome.kind === "idle") return null;
  const tone = outcome.kind === "failed" ? "notice notice-bad" : outcome.kind === "busy" ? "notice" : "notice notice-ok";
  return (
    <div className={tone} role="status" aria-live="polite">
      <strong>{outcome.what}:</strong>{" "}
      {outcome.kind === "busy" ? "working…" : outcome.detail}
      {outcome.kind === "done" && outcome.auditId ? (
        <span className="mono" style={{ marginLeft: 8, fontSize: 11 }}>
          audit {outcome.auditId.slice(0, 8)}
        </span>
      ) : null}
    </div>
  );
}

function ApprovalCard({
  item,
  live,
  selected,
  onToggle,
  onInspect,
  run,
}: {
  item: ApprovalQueueItem;
  live: boolean;
  selected: boolean;
  onToggle: () => void;
  onInspect: () => void;
  run: (what: string, call: () => Promise<DecisionResponse | { results: BulkItemResult[]; done: number; refused: number }>) => Promise<void>;
}) {
  const { proposal } = item;
  const risk = proposal.risk_level as RiskLevel;
  const [rejecting, setRejecting] = useState(false);
  const [modifying, setModifying] = useState(false);
  const [reanalyzing, setReanalyzing] = useState(false);

  return (
    <section className="card">
      <header className="card-header">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={`Select ${proposal.change_ref} for bulk decision`}
        />
        <RiskBadge level={risk} />
        <span className="card-title">{humanizeField(proposal.field_name)}</span>
        <span className="card-hint mono">
          {proposal.change_ref} · {proposal.product_id?.slice(0, 8) ?? "no product"}
        </span>
      </header>
      <div className="card-body stack" style={{ gap: 12 }}>
        <div>
          <div className="change-line">
            <span className="change-before">{displayValue(proposal.current_value)}</span>
            <span className="change-arrow" aria-label="changes to">
              →
            </span>
            <strong className="change-after">{displayValue(proposal.proposed_value)}</strong>
          </div>
          <p className="reason-line">{proposal.reason}</p>
        </div>

        <dl className="kv">
          <dt>Confidence</dt>
          <dd>
            {formatConfidence(proposal.confidence)}
            <span className="field-hint"> — evidence, never permission (§37)</span>
          </dd>
          <dt>Validation</dt>
          <dd>
            <span className={`badge ${proposal.validation_status === "validated" ? "badge-LOW" : "badge-MEDIUM"}`}>
              {proposal.validation_status}
            </span>
            {!item.simulated ? (
              <span className="field-hint"> — the sandbox has not validated this one; it cannot be approved yet</span>
            ) : null}
          </dd>
          <dt>Approvals</dt>
          <dd>
            {item.approvals_received ?? 0} of {item.required_approvals ?? 1}
            {(item.required_approvals ?? 1) > 1 ? (
              <span className="field-hint"> — a second approver is required (D-020)</span>
            ) : null}
          </dd>
          <dt>Channels</dt>
          <dd className="badge-row">
            {proposal.affected_channels.length === 0 ? (
              <span className="field-hint">No live channel is affected</span>
            ) : (
              proposal.affected_channels.map((channel) => (
                <NeutralBadge key={displayValue(channel)}>{displayValue(channel)}</NeutralBadge>
              ))
            )}
          </dd>
        </dl>

        <div className="toolbar" style={{ borderTop: "1px solid rgb(var(--border))", paddingTop: 12 }}>
          <button
            type="button"
            className="button button-primary"
            disabled={!live || !item.simulated}
            onClick={() => run(`Approve ${proposal.change_ref}`, () => approve(proposal.id))}
          >
            Approve
          </button>
          <button type="button" className="button" disabled={!live} onClick={() => setModifying(true)}>
            Approve with edits
          </button>
          <button type="button" className="button" disabled={!live} onClick={() => setRejecting(true)}>
            Reject
          </button>
          <button type="button" className="button button-ghost" disabled={!live} onClick={() => setReanalyzing(true)}>
            Request re-analysis
          </button>
          <span className="spacer" />
          <button type="button" className="button button-ghost" onClick={onInspect}>
            Evidence
          </button>
        </div>
      </div>

      {rejecting ? (
        <DecisionDialog
          title={`Reject ${proposal.change_ref}`}
          description="A rejection is final for this proposal version and is written to the audit log with your note."
          confirmLabel="Reject"
          confirmClass="button-danger"
          minLength={1}
          onCancel={() => setRejecting(false)}
          onConfirm={(notes) => {
            setRejecting(false);
            return run(`Reject ${proposal.change_ref}`, () => reject(proposal.id, notes));
          }}
        />
      ) : null}

      {modifying ? (
        <DecisionDialog
          title={`Approve ${proposal.change_ref} with edits`}
          description="The edited value becomes the proposal's value; a fresh approval request is opened for it."
          confirmLabel="Approve the edited value"
          minLength={1}
          valueLabel="Proposed value"
          defaultValue={displayValue(proposal.proposed_value)}
          onCancel={() => setModifying(false)}
          onConfirm={(notes, value) => {
            setModifying(false);
            return run(`Approve ${proposal.change_ref} with edits`, () =>
              modify(proposal.id, value ?? null, notes),
            );
          }}
        />
      ) : null}

      {reanalyzing ? (
        <DecisionDialog
          title={`Request re-analysis of ${proposal.change_ref}`}
          description="Validation is reset and a new approval request is created, so this version cannot be approved on the old evidence."
          confirmLabel="Request re-analysis"
          minLength={1}
          onCancel={() => setReanalyzing(false)}
          onConfirm={(notes) => {
            setReanalyzing(false);
            return run(`Re-analysis ${proposal.change_ref}`, () => requestReanalysis(proposal.id, notes));
          }}
        />
      ) : null}
    </section>
  );
}

/**
 * A decision dialog.
 *
 * Notes are required for reject, modify and re-analysis because the backend
 * requires them (`min_length=1`), so the dialog refuses to send an empty note
 * rather than letting the API answer with a validation error the operator has
 * to decode.
 */
function DecisionDialog({
  title,
  description,
  confirmLabel,
  confirmClass,
  minLength,
  valueLabel,
  defaultValue,
  onConfirm,
  onCancel,
}: {
  title: string;
  description: string;
  confirmLabel: string;
  confirmClass?: string;
  minLength?: number;
  valueLabel?: string;
  defaultValue?: string;
  onConfirm: (notes: string, value?: string) => Promise<void>;
  onCancel: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [notes, setNotes] = useState("");
  const [value, setValue] = useState(defaultValue ?? "");
  const [invalid, setInvalid] = useState(false);

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (minLength && notes.trim().length < minLength) {
      setInvalid(true);
      return;
    }
    dialog.current?.close();
    await onConfirm(notes.trim(), valueLabel ? value.trim() : undefined);
  };

  return (
    <dialog ref={dialog} className="dialog" onClose={onCancel} aria-label={title}>
      <form method="dialog" className="dialog-body" onSubmit={submit}>
        <h2 className="dialog-title">{title}</h2>
        <p className="dialog-text">{description}</p>
        {valueLabel ? (
          <span className="field">
            <label className="field-label" htmlFor="decision-value">
              {valueLabel}
            </label>
            <input
              className="input"
              id="decision-value"
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </span>
        ) : null}
        <span className="field">
          <label className="field-label" htmlFor="decision-notes">
            Note {minLength ? "(required)" : "(optional)"}
          </label>
          <textarea
            className="input"
            id="decision-notes"
            rows={3}
            value={notes}
            aria-invalid={invalid}
            aria-describedby={invalid ? "decision-notes-error" : undefined}
            onChange={(event) => {
              setNotes(event.target.value);
              setInvalid(false);
            }}
          />
          {invalid ? (
            <span className="field-error" id="decision-notes-error" role="alert">
              A note is required for this decision; it is stored on the audit row.
            </span>
          ) : null}
        </span>
        <div className="dialog-actions">
          <button type="button" className="button button-ghost" onClick={() => dialog.current?.close()}>
            Cancel
          </button>
          <button type="submit" className={`button ${confirmClass ?? "button-primary"}`}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}

/** The evidence a reviewer opens before deciding: risk factors, sandbox, conflicts. */
function ApprovalDetailDialog({
  proposalId,
  live,
  run,
  onClose,
}: {
  proposalId: string;
  live: boolean;
  run: (what: string, call: () => Promise<DecisionResponse | { results: BulkItemResult[]; done: number; refused: number }>) => Promise<void>;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [detail, setDetail] = useState<ApprovalDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Derived, not stored: in fixture mode there is nothing to fetch, and a
  // message that depends on props must not be a state the effect has to sync.
  const offlineMessage = live
    ? null
    : "Evidence is fetched from the backend, which is not connected on this screen.";

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);

  useEffect(() => {
    if (!live) return;
    let cancelled = false;
    fetchApprovalDetail(proposalId)
      .then((value) => {
        if (!cancelled) setDetail(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof WriteApiError ? cause.message : "Could not load the evidence.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [proposalId, live]);

  return (
    <dialog ref={dialog} className="dialog dialog-wide" aria-label="Evidence for this proposal" onClose={onClose}>
      <div className="dialog-body">
        <h2 className="dialog-title">Evidence</h2>
        {offlineMessage ? <p className="dialog-text">{offlineMessage}</p> : null}
        {error ? <p className="dialog-text">{error}</p> : null}
        {!detail && !error && !offlineMessage ? <p className="dialog-text">Loading evidence…</p> : null}
        {detail ? (
          <div className="stack">
            <dl className="kv">
              <dt>Approvable</dt>
              <dd>{detail.approvable ? "yes" : `no — ${detail.approvable_reason ?? "reason not reported"}`}</dd>
              <dt>Proposal</dt>
              <dd className="mono">{detail.proposal.change_ref}</dd>
              <dt>Reason</dt>
              <dd>{detail.proposal.reason}</dd>
            </dl>

            {detail.risk ? (
              <section>
                <h3 className="section-title">
                  Risk factors ({detail.risk.numeric_score.toFixed(1)})
                </h3>
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
                      {detail.risk.factors.map((factor) => (
                        <tr key={factor.code}>
                          <td className="mono">{factor.code}</td>
                          <td className="num">{factor.weight}</td>
                          <td>{factor.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {detail.risk.escalated_reason ? (
                  <p className="field-hint">Escalated: {detail.risk.escalated_reason}</p>
                ) : null}
              </section>
            ) : null}

            {detail.simulation ? (
              <section>
                <h3 className="section-title">Sandbox ({detail.simulation.status})</h3>
                <p className="field-hint">{detail.simulation.explanation ?? "No explanation recorded."}</p>
                <ul className="verdict-list">
                  {(
                    [
                      ["schema", detail.simulation.schema_valid],
                      ["relationships", detail.simulation.relationship_valid],
                      ["compliance", detail.simulation.compliance_valid],
                    ] as const
                  ).map(([label, value]) => {
                    const verdict = verdictLabel(value);
                    return (
                      <li key={label}>
                        <span className={`badge badge-${verdict.tone === "ok" ? "LOW" : verdict.tone === "bad" ? "CRITICAL" : "neutral"}`}>
                          {verdict.text}
                        </span>
                        {label}
                        {value === null ? <span className="field-hint"> — did not run, which is not a pass</span> : null}
                      </li>
                    );
                  })}
                </ul>
              </section>
            ) : (
              <p className="field-hint">The sandbox has not run for this proposal.</p>
            )}

            {detail.conflicts.length > 0 ? (
              <section>
                <h3 className="section-title">Disagreements</h3>
                <ul className="evidence-list">
                  {detail.conflicts.map((conflict) => (
                    <li key={conflict.conflict_id}>
                      <span className="mono">{conflict.field_name}</span> · {conflict.conflict_type}
                      <div className="mono small">
                        {JSON.stringify(conflict.competing_values)}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {detail.actions.length > 0 ? (
              <section>
                <h3 className="section-title">Decisions so far</h3>
                <ul className="evidence-list">
                  {detail.actions.map((action) => (
                    <li key={`${action.decided_by}-${action.decided_at}`}>
                      <strong>{action.decision}</strong> by {action.decided_by.slice(0, 8)} · {action.decided_at}
                      {action.notes ? <div className="field-hint">{action.notes}</div> : null}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            <div className="dialog-actions">
              <button
                type="button"
                className="button button-primary"
                disabled={!live || !detail.approvable}
                onClick={() => {
                  dialog.current?.close();
                  return run("Approve", () => approve(proposalId));
                }}
              >
                Approve from here
              </button>
              <button type="button" className="button" onClick={() => dialog.current?.close()}>
                Close
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </dialog>
  );
}