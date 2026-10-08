"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV } from "./nav";
import type { NavEntry } from "./nav";

/**
 * Navigation with `aria-current`, which needs the URL the server does not have.
 *
 * Isolated into its own client component so the shell, the sidebar and every
 * page stay server components. `aria-current="page"` is what tells a screen
 * reader where it is; a highlighted link that a keyboard user cannot identify
 * as "here" is a visual-only cue.
 */
export function NavItems({
  group,
  badges,
}: {
  group: NavEntry["group"];
  badges?: Record<string, number>;
}) {
  const pathname = usePathname();
  const items = NAV.filter((item) => item.group === group);
  return (
    <>
      {items.map((item) => {
        const current = pathname === item.href;
        const count = badges?.[item.href];
        return (
          <Link
            className="nav-item"
            href={item.href}
            key={item.href}
            aria-current={current ? "page" : undefined}
          >
            {item.label}
            {count ? (
              <span className="nav-count">
                {count}
                <span className="visually-hidden"> items waiting</span>
              </span>
            ) : null}
          </Link>
        );
      })}
    </>
  );
}