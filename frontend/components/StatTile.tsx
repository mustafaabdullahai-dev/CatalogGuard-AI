export function StatTile({
  label,
  value,
  delta,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: string | number;
  delta?: string;
  tone?: "neutral" | "up" | "down";
  hint?: string;
}) {
  return (
    <div className="card tile" title={hint}>
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {delta ? <span className={`tile-delta ${tone}`}>{delta}</span> : null}
    </div>
  );
}
