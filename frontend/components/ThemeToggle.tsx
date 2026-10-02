"use client";

import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "catalogguard.theme";

/**
 * Theme lives in a DOM attribute, not React state.
 *
 * An inline script in the document head has already applied the stored theme
 * before first paint, so the DOM is the source of truth and this component only
 * reads it. `useSyncExternalStore` is what makes that safe: it supplies
 * `"light"` during server rendering and the real value on the client, so React
 * hydrates without a mismatch instead of flashing the wrong theme.
 */

function readTheme(): Theme {
  const current = document.documentElement.dataset.theme;
  return current === "dark" ? "dark" : "light";
}

function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  return () => observer.disconnect();
}

function writeTheme(next: Theme): void {
  document.documentElement.dataset.theme = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Private browsing with storage disabled is not worth failing over.
  }
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, readTheme, () => "light");

  const toggle = useCallback(() => {
    writeTheme(theme === "dark" ? "light" : "dark");
  }, [theme]);

  const target = theme === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      className="button button-ghost"
      onClick={toggle}
      aria-label={`Switch to ${target} theme`}
      title={`Switch to ${target} theme`}
    >
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}
