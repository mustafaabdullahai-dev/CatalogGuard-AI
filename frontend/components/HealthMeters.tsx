import { formatPercent, formatRelative } from "@/lib/format";
import type { HealthScore } from "@/lib/types";

const DIMENSIONS: { key: keyof HealthScore; label: string }[] = [
  { key: "data_quality", label: "Data quality" },
  { key: "consistency", label: "Consistency" },
  { key: "compliance", label: "Compliance" },
  { key: "relationship_integrity", label: "Relationships" },
  { key: "seo_readiness", label: "SEO readiness" },
  { key: "change_safety", label: "Change safety" },
];

/**
 * Health dimensions as meters.
 *
 * Relationship integrity is drawn in the danger color below 70: it is the
 * dimension whose failures break storefronts rather than merely skewing reports,
 * and it usually sits lowest because it depends on every other source being
 * correct first.
 */
export function HealthMeters({ health }: { health: HealthScore }) {
  return (
    <section className="card">
      <header className="card-header">
        <span className="card-title">Catalog health</span>
        <span className="card-hint">
          overall {health.overall.toFixed(1)} · {formatRelative(health.computed_at)}
        </span>
      </header>
      <div className="card-body stack">
        {DIMENSIONS.map(({ key, label }) => {
          const value = health[key] as number;
          const low = key === "relationship_integrity" && value < 70;
          return (
            <div key={key}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  fontSize: 12,
                  marginBottom: 4,
                }}
              >
                <span style={{ color: "rgb(var(--text-muted))" }}>{label}</span>
                <span style={{ fontVariantNumeric: "tabular-nums", color: low ? "rgb(var(--danger))" : undefined }}>
                  {formatPercent(value / 100, 1)}
                </span>
              </div>
              <div className="meter">
                <span
                  style={{
                    width: `${Math.min(100, Math.max(0, value))}%`,
                    background: low ? "rgb(var(--danger))" : "rgb(var(--primary))",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
