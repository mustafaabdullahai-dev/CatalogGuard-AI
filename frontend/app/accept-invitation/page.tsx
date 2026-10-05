import { Suspense } from "react";
import { AuthScreen, AuthScreenFallback } from "@/components/AuthScreen";

export const metadata = { title: "Accept invitation · CatalogGuard AI" };

/**
 * Public and unauthenticated: a person reaching this page has no session, which
 * is why it renders the auth screen rather than the application shell.
 *
 * The Suspense boundary is required, not decorative. The screen reads the reset
 * or invitation token from the query string, and Next cannot prerender a page
 * that depends on search params without one.
 */
export default function Page() {
  return (
    <Suspense fallback={<AuthScreenFallback />}>
      <AuthScreen />
    </Suspense>
  );
}
