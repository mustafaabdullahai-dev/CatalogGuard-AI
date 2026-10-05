"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";
import { ThemeToggle } from "./ThemeToggle";
import {
  acceptInvitation,
  AuthApiError,
  confirmPasswordReset,
  PASSWORD_MIN_LENGTH,
  requestPasswordReset,
  signIn,
  validateEmail,
  validatePassword,
} from "@/lib/authClient";

/**
 * The unauthenticated surfaces: sign-in, password reset, invitation acceptance.
 *
 * One component for all three because they are one flow. A person who cannot
 * sign in lands here, a person who cannot read their inbox lands here, and the
 * only difference between the three is which fields appear and what happens on
 * success. Splitting them would duplicate the CSRF handshake, the error
 * handling and the validation three times, and drift is what makes auth screens
 * disagree with each other about what is allowed.
 *
 * Two rules the backend forced on this design:
 *
 * - The reset request screen never learns whether an account exists. The
 *   response is identical for a known address and an unknown one, so this screen
 *   shows the same message either way. It must not, for instance, offer
 *   "create an account" when the address looks unknown: that would rebuild the
 *   account-existence oracle the identical response exists to prevent.
 * - Nothing here stores a session. A successful reset revokes every session and
 *   signs you out, so the honest next step is the sign-in screen, not the
 *   dashboard.
 */

type Mode = "sign-in" | "request-reset" | "reset" | "accept-invitation";

function Field({
  label,
  type,
  value,
  onChange,
  autoComplete,
  placeholder,
  required,
}: {
  label: string;
  type: string;
  value: string;
  onChange: (next: string) => void;
  autoComplete?: string;
  placeholder?: string;
  required?: boolean;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <input
        className="input"
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        placeholder={placeholder}
        required={required}
      />
    </label>
  );
}

function Banner({ tone, children }: { tone: "info" | "error"; children: React.ReactNode }) {
  return (
    <div className={`auth-banner ${tone === "error" ? "auth-banner-error" : ""}`} role="status">
      {children}
    </div>
  );
}

/**
 * Rendered while the client component reads the query string. A centred card
 * rather than a blank body: a white flash before the auth screen is exactly
 * where people look for a phishing page.
 */
export function AuthScreenFallback() {
  return (
    <div className="auth">
      <div className="auth-bar" />
      <div className="auth-panel">
        <section className="card auth-card auth-skeleton" aria-busy="true" aria-label="Loading">
          <div className="skeleton-line" style={{ width: "40%", height: 20 }} />
          <div className="skeleton-line" style={{ width: "75%" }} />
          <div className="skeleton-line" style={{ width: "100%", height: 34 }} />
          <div className="skeleton-line" style={{ width: "100%", height: 34 }} />
        </section>
      </div>
    </div>
  );
}

export function AuthScreen() {
  const search = useSearchParams();
  const pathname = usePathname();
  const token = search.get("token") ?? "";

  const mode: Mode =
    pathname === "/login"
      ? "sign-in"
      : pathname === "/accept-invitation"
        ? "accept-invitation"
        : token
          ? "reset"
          : "request-reset";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [fullName, setFullName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const run = useCallback(
    async (action: () => Promise<void>) => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        await action();
      } catch (cause) {
        setError(
          cause instanceof AuthApiError ? cause.message : "Something went wrong. Try again.",
        );
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  function submit(event: React.FormEvent) {
    event.preventDefault();

    if (mode === "sign-in") {
      const problem = validateEmail(email);
      if (problem) return setError(problem);
      void run(async () => {
        const result = await signIn(email, password);
        if (result.workspaces.length === 0) {
          // Nothing to open. Saying so is better than redirecting into a
          // dashboard that renders an empty workspace forever.
          setNotice(
            `Signed in as ${result.email}, but no workspace is assigned to this account yet.`,
          );
          return;
        }
        // A full document load rather than a client-side route change. The
        // session cookie was just issued by the API origin; a client transition
        // would reuse the RSC payload cached before sign-in and render a
        // dashboard built from the signed-out request.
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.assign("/");
      });
      return;
    }

    if (mode === "request-reset") {
      const problem = validateEmail(email);
      if (problem) return setError(problem);
      void run(async () => {
        const message = await requestPasswordReset(email);
        setEmail("");
        setNotice(message);
      });
      return;
    }

    if (mode === "reset") {
      const problem = validatePassword(password, confirmation);
      if (problem) return setError(problem);
      void run(async () => {
        const result = await confirmPasswordReset(token, password);
        setPassword("");
        setConfirmation("");
        setNotice(
          `Password changed. ${result.sessions_revoked} ${
            result.sessions_revoked === 1 ? "session was" : "sessions were"
          } signed out. Sign in again with the new password.`,
        );
      });
      return;
    }

    const problem = validatePassword(password, password ? confirmation : undefined);
    if (problem) return setError(problem);
    void run(async () => {
      const result = await acceptInvitation(token, {
        password,
        full_name: fullName.trim() || undefined,
      });
      setPassword("");
      setConfirmation("");
      setNotice(
        result.account_created
          ? `Account created as ${result.role.replace(/_/g, " ")}. Sign in to continue.`
          : `Added to the workspace as ${result.role.replace(/_/g, " ")}. Sign in to continue.`,
      );
    });
  }

  const copy = {
    "sign-in": {
      title: "Sign in",
      subtitle: "CatalogGuard does not let accounts self-register.",
    },
    "request-reset": {
      title: "Reset your password",
      subtitle: "We will queue a reset link if the address has an active account.",
    },
    reset: {
      title: "Choose a new password",
      subtitle: "This link is single-use and expires on its own.",
    },
    "accept-invitation": {
      title: "Accept your invitation",
      subtitle: "An administrator granted you access to a workspace.",
    },
  }[mode];

  const passwordHint =
    mode === "accept-invitation"
      ? "Pick one if the address has no account yet. An account that already has a password keeps it."
      : `At least ${PASSWORD_MIN_LENGTH} characters.`;

  return (
    <div className="auth">
      <div className="auth-bar">
        <div className="brand">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.png" alt="" />
          <div>
            <div className="brand-name">CatalogGuard</div>
            <div className="brand-sub">Change safety</div>
          </div>
        </div>
        <ThemeToggle />
      </div>

      <div className="auth-panel">
        <section className="card auth-card">
          <header className="auth-head">
            <h2>{copy.title}</h2>
            <p>{copy.subtitle}</p>
          </header>

          {notice ? (
            <Banner tone="info">
              {notice}
              {mode !== "sign-in" ? (
                <>
                  {" "}
                  <Link href="/login">Sign in</Link>
                </>
              ) : null}
            </Banner>
          ) : null}
          {error ? <Banner tone="error">{error}</Banner> : null}

          <form onSubmit={submit} className="stack" style={{ gap: 14 }}>
            {mode === "reset" || mode === "accept-invitation" ? (
              // Neither page knows which account the token belongs to, and
              // asking would mean a lookup endpoint whose only purpose is to
              // decorate a form. A hidden username field is what Chrome's
              // password manager needs to bind the new credential to something
              // instead of silently storing it under a blank entry.
              <input
                type="email"
                className="visually-hidden"
                autoComplete="username"
                tabIndex={-1}
                aria-hidden="true"
                readOnly
                value=""
              />
            ) : null}
            {mode === "sign-in" ? (
              <>
                <Field
                  label="Email"
                  type="email"
                  value={email}
                  onChange={setEmail}
                  autoComplete="username"
                  required
                />
                <Field
                  label="Password"
                  type="password"
                  value={password}
                  onChange={setPassword}
                  autoComplete="current-password"
                  required
                />
              </>
            ) : null}

            {mode === "request-reset" ? (
              <Field
                label="Email"
                type="email"
                value={email}
                onChange={setEmail}
                autoComplete="username"
                required
              />
            ) : null}

            {mode === "reset" || mode === "accept-invitation" ? (
              <>
                <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="new-password" />
                <div className="field-hint">{passwordHint}</div>
                <Field
                  label="Confirm password"
                  type="password"
                  value={confirmation}
                  onChange={setConfirmation}
                  autoComplete="new-password"
                  required
                />
              </>
            ) : null}

            {mode === "accept-invitation" ? (
              <Field
                label="Full name (optional)"
                type="text"
                value={fullName}
                onChange={setFullName}
                autoComplete="name"
                placeholder="How you appear to the rest of the workspace"
              />
            ) : null}

            <button className="button button-primary" type="submit" disabled={busy}>
              {busy
                ? "Working…"
                : mode === "sign-in"
                  ? "Sign in"
                  : mode === "request-reset"
                    ? "Send reset link"
                    : mode === "reset"
                      ? "Change password"
                      : "Accept invitation"}
            </button>
          </form>

          <footer className="auth-foot">
            {mode === "sign-in" ? (
              <Link href="/reset-password">Forgot your password?</Link>
            ) : mode === "request-reset" ? (
              <Link href="/login">Back to sign in</Link>
            ) : mode === "reset" ? (
              <span>
                Link not working? <Link href="/reset-password">Request another one</Link>.
              </span>
            ) : null}
          </footer>
        </section>

        <p className="auth-legal">
          A reset signs out every device and issues no session. Accepting an
          invitation grants exactly the role an administrator chose, never more.
        </p>
      </div>
    </div>
  );
}
