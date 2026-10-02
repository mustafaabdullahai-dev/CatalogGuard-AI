import Link from "next/link";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/catalog", label: "Catalog explorer" },
  { href: "/approvals", label: "Approval center" },
];

/**
 * The application frame: sidebar, top bar, content.
 *
 * The sidebar is a tenant switcher and navigation, not a marketing menu. Every
 * entry is a place the operator has to work, because the product's whole claim
 * is that changes are reviewed rather than trusted.
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
      <aside className="sidebar">
        <div className="brand">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.png" alt="" />
          <div>
            <div className="brand-name">CatalogGuard</div>
            <div className="brand-sub">Change safety</div>
          </div>
        </div>
        <nav>
          <div className="nav-label">Operate</div>
          {NAV.map((item) => (
            <Link className="nav-item" href={item.href} key={item.href}>
              {item.label}
              {badges?.[item.href] ? <span className="nav-count">{badges[item.href]}</span> : null}
            </Link>
          ))}
        </nav>
        <div style={{ marginTop: "auto", padding: "12px 8px 0", borderTop: "1px solid rgb(var(--border))" }}>
          <div style={{ color: "rgb(var(--text-subtle))", fontSize: 11, lineHeight: 1.5 }}>
            Every change is simulated, scored and approved before it touches a
            live listing.
          </div>
        </div>
      </aside>
      <main className="main">
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
