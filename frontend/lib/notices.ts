/**
 * Why a screen is showing fixtures instead of workspace rows.
 *
 * Pure, so the mapping is unit tested and the wording cannot drift per page.
 * The rule the mapping follows: never say "no data" when the truth is "no
 * connection". A screen that renders an empty state for an unreachable backend
 * tells an operator their catalog is clean.
 */

export const FIXTURE_NOTICES: Record<string, string> = {
  disabled:
    "Fixture mode is forced on (CATALOGGUARD_FIXTURES=1). Every number below is invented.",
  offline:
    "Not signed in to the backend, so this screen is showing invented fixture rows. Sign in to read a workspace.",
  unreachable:
    "The backend did not answer, so this screen is showing invented fixture rows. Start it and reload.",
  forbidden: "The backend refused this workspace, so this screen is showing invented fixture rows.",
  "server-error": "The backend returned an error, so this screen is showing invented fixture rows.",
};

export function reasonNotice(source: "live" | "fixture", reason: string | null): string | null {
  if (source === "live") return null;
  if (reason && reason in FIXTURE_NOTICES) return FIXTURE_NOTICES[reason] ?? null;
  return FIXTURE_NOTICES.offline ?? null;
}