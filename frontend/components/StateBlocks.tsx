import Link from "next/link";

import type { FixtureReason } from "@/lib/api";
import { reasonNotice } from "@/lib/notices";

/**
 * The banner every screen shows when it is rendering fixtures.
 *
 * It names the cause (not signed in, backend unreachable, workspace refused)
 * and offers the action that fixes it, because "no data" and "no connection"
 * look identical in a table and mean opposite things to an operator.
 */
export function DataSourceNotice({
  source,
  reason,
}: {
  source: "live" | "fixture";
  reason: FixtureReason | null;
}) {
  const notice = reasonNotice(source, reason);
  if (!notice) return null;
  const showSignIn = reason === "offline";
  return (
    <div className="notice" role="status">
      <span>{notice}</span>
      {showSignIn ? (
        <>
          {" "}
          <Link href="/login">Sign in</Link>
        </>
      ) : null}
    </div>
  );
}

/** §60's empty state: says what is missing and what would put something here. */
export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <section className="card">
      <div className="card-body empty-state">
        <div className="empty-state-title">{title}</div>
        <p className="empty">{body}</p>
        {action}
      </div>
    </section>
  );
}

/**
 * A number nobody can report yet.
 *
 * Distinct from `EmptyState` on purpose: "no route computes this" is a gap in
 * the product, not an absence in the workspace, and rendering it as `0` or `—`
 * is the error this component exists to prevent (D-006, and the gaps listed in
 * `docs/phases/phase-21-frontend.md`).
 */
export function Unavailable({ what }: { what: string }) {
  return (
    <div className="tile" title="No API route reports this yet.">
      <span className="tile-label">{what}</span>
      <span className="tile-value tile-value-mute">not reported</span>
      <span className="tile-delta">no route</span>
    </div>
  );
}

export function ErrorState({ what, detail }: { what: string; detail?: string | null }) {
  return (
    <section className="card" role="alert">
      <div className="card-body">
        <div style={{ fontWeight: 600 }}>{what}</div>
        {detail ? (
          <p style={{ color: "rgb(var(--text-muted))", margin: "6px 0 0" }}>{detail}</p>
        ) : null}
      </div>
    </section>
  );
}