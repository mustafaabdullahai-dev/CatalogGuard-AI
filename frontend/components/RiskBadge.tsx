import { requiresApproval, type RiskLevel } from "@/lib/format";

/** The four risk bands, colored identically everywhere they appear. */
export function RiskBadge({ level, score }: { level: RiskLevel; score?: number }) {
  return (
    <span className={`badge badge-${level}`} title={requiresApproval(level) ? "Requires explicit approval" : undefined}>
      <span className="dot" />
      {level}
      {score !== undefined ? ` · ${score}` : ""}
    </span>
  );
}

export function NeutralBadge({ children }: { children: React.ReactNode }) {
  return <span className="badge badge-neutral">{children}</span>;
}
