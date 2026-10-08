import { NavItems } from "@/components/NavItems";
import { NAV_GROUPS } from "@/components/nav";
import { ThemeToggle } from "./ThemeToggle";

/**
 * The application frame: sidebar, top bar, content.
 *
 * The sidebar is a tenant switcher and navigation, not a marketing menu. Every
 * entry is a place the operator has to work, because the product's whole claim
 * is that changes are reviewed rather than trusted.
 *
 * Accessibility choices that are load-bearing rather than decorative:
 * - a skip link, because the sidebar is on every screen;
 * - `<main id="main" tabIndex={-1}>` as the skip target, so the jump moves
 *   focus as well as the viewport;
 * - the nav labelled as a landmark, so a screen reader can list sections
 *   instead of reading every link in a row.
 */
export function Shell({
  title,
  subtitle,
  actions,
  badges,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  badges?: Record<string, number>;
  children: React.ReactNode;
}) {
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <aside className="sidebar">
        <div className="brand">
          {/* eslint-disable-next-line @next/next/no-img-element -- a 30x20 inline mark: an image loader would add a request pipeline for one 1KB file. Explicit dimensions because the source is 3:2 and Lighthouse reports a distorted aspect ratio without them. */}
          <img src="/logo.png" alt="" width={30} height={20} />
          <div>
            <div className="brand-name">CatalogGuard</div>
            <div className="brand-sub">Change safety</div>
          </div>
        </div>
        <nav aria-label="Sections">
          {NAV_GROUPS.map((group) => (
            <div key={group.id}>
              <div className="nav-label">{group.label}</div>
              <NavItems group={group.id} badges={badges} />
            </div>
          ))}
        </nav>
        <div style={{ marginTop: "auto", padding: "12px 8px 0", borderTop: "1px solid rgb(var(--border))" }}>
          <div style={{ color: "rgb(var(--text-subtle))", fontSize: 11, lineHeight: 1.5 }}>
            Every change is simulated, scored and approved before it touches a
            live listing.
          </div>
        </div>
      </aside>
      <main className="main" id="main" tabIndex={-1}>
        <header className="topbar">
          <div>
            <h1>{title}</h1>
            {subtitle ? <div className="topbar-sub">{subtitle}</div> : null}
          </div>
          <div className="topbar-actions">
            {actions}
            <ThemeToggle />
          </div>
        </header>
        <div className="content">{children}</div>
      </main>
    </div>
  );
}