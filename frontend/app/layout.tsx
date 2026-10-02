import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CatalogGuard AI",
  description: "E-commerce catalog reconciliation and change safety",
};

/**
 * Applies the stored theme before first paint.
 *
 * Deliberately inline and blocking: a script that runs after hydration would
 * show a light flash to every dark-mode user on every navigation.
 */
const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem('catalogguard.theme');if(t==='dark'||t==='light'){document.documentElement.dataset.theme=t}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
