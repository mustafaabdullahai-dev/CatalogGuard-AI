/** The sidebar's contents, in the order an operator works through them. */
export interface NavEntry {
  href: string;
  label: string;
  /** Grouped in the sidebar so the console reads as three workflows. */
  group: "operate" | "verify";
}

export const NAV: NavEntry[] = [
  { href: "/", label: "Dashboard", group: "operate" },
  { href: "/approvals", label: "Approval center", group: "operate" },
  { href: "/catalog", label: "Catalog explorer", group: "operate" },
  { href: "/reconciliation", label: "Reconciliation", group: "verify" },
  { href: "/sandbox", label: "Sandbox", group: "verify" },
  { href: "/compliance", label: "Compliance", group: "verify" },
];

export const NAV_GROUPS: { id: NavEntry["group"]; label: string }[] = [
  { id: "operate", label: "Operate" },
  { id: "verify", label: "Verify" },
];