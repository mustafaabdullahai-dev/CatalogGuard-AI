/** Formatting helpers shared by every screen. Pure, so they are unit tested. */

export const RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

export function isRiskLevel(value: string): value is RiskLevel {
  return (RISK_LEVELS as readonly string[]).includes(value);
}

/** Whether the risk engine has marked this band as requiring a human decision. */
export function requiresApproval(level: RiskLevel): boolean {
  return level === "HIGH" || level === "CRITICAL";
}

export function formatMoney(value: number, currency = "USD"): string {
  // Tabular alignment matters more than maximum precision here: a column of
  // prices where the cents jitter is unreadable at a glance.
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatCompact(value: number): string {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function formatPercent(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatConfidence(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function formatRelative(iso: string, now = Date.now()): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "—";
  const seconds = Math.round((now - then) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

const ACRONYMS = new Set([
  "gtin",
  "sku",
  "upc",
  "ean",
  "asin",
  "isbn",
  "mpn",
  "seo",
  "url",
  "erp",
  "pim",
  "wms",
  "api",
]);

/**
 * Turn `category_path` into `Category path` for inline display.
 *
 * Acronyms are preserved: `gtin` must never render as "Gtin". A column header
 * that misspells the identifier it refers to is the kind of small wrongness that
 * makes an operator distrust the whole screen.
 */
export function humanizeField(field: string): string {
  const spaced = field.replace(/[_-]+/g, " ").trim();
  if (spaced === "") return field;
  // Sentence case, with acronyms kept uppercase: "Category path", "SEO readiness".
  return spaced
    .split(" ")
    .map((word, index) => {
      const lower = word.toLowerCase();
      if (ACRONYMS.has(lower)) return lower.toUpperCase();
      if (index > 0) return lower;
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(" ");
}
