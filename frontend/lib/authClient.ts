/**
 * Browser-side client for the account flows: sign-in, password reset,
 * invitation acceptance.
 *
 * Split from `lib/api.ts` on purpose. That module runs in server components,
 * which call the backend directly. These pages are public and unauthenticated,
 * so they call it from the browser — but through the same-origin rewrite in
 * `next.config.ts`, which proxies `/api/*` to the backend.
 *
 * Relative, not absolute, and that is the whole point. A direct cross-origin
 * call would need the frontend origin in the backend's CORS allowlist and would
 * store the session cookie on a second origin, which means third-party cookie
 * policy, `SameSite=None`, and a second origin to keep in sync with deployments.
 * Through the proxy this is same-origin: the cookie belongs to this host, the
 * session survives a page reload, and nothing about CORS is load-bearing.
 *
 * The CSRF token is fetched from `/auth/csrf` and copied into the header rather
 * than read from the cookie: the endpoint exists so a client does not have to
 * parse `Set-Cookie`, and hardcoding the header name in two places is how that
 * ends up wrong.
 */

const BASE = "";

/** Mirrors `password_min_length` on the backend. */
export const PASSWORD_MIN_LENGTH = 12;

export class AuthApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "AuthApiError";
  }
}

async function csrfHeader(): Promise<Record<string, string>> {
  const response = await fetch(`${BASE}/api/v1/auth/csrf`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) throw new AuthApiError(response.status, "Could not start a session.");
  const { csrf_token: token, header_name: header } = (await response.json()) as {
    csrf_token: string;
    header_name: string;
  };
  return { [header]: token };
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const header = await csrfHeader();
  const response = await fetch(`${BASE}/api/v1${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json", ...header },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) throw new AuthApiError(response.status, describe(response.status));
  return (await response.json()) as T;
}

/**
 * Backend error text is intentionally uniform, so there is nothing to surface
 * from the body. What the person can be told is what they can do next.
 */
function describe(status: number): string {
  if (status === 422) return "That password does not meet the policy. Use at least 12 characters.";
  if (status === 429) return "Too many attempts. Wait a few minutes and try again.";
  if (status === 401) return "That email and password combination was not recognised.";
  if (status === 403) return "That link is no longer valid. Ask for a new one.";
  if (status >= 500) return "Something went wrong on our side. Try again shortly.";
  return "The request could not be completed.";
}

export interface LoginResult {
  email: string;
  workspaces: { id: string; name: string; slug: string; role: string }[];
}

export async function signIn(email: string, password: string): Promise<LoginResult> {
  const data = await post<{
    user: { email: string };
    workspaces: { id: string; name: string; slug: string; role: string }[];
  }>("/auth/login", { email, password });
  return { email: data.user.email, workspaces: data.workspaces };
}

/** Always resolves. The server cannot tell us whether the account exists. */
export async function requestPasswordReset(email: string): Promise<string> {
  const data = await post<{ accepted: boolean; message: string }>("/auth/password-reset", {
    email,
  });
  return data.message;
}

export async function confirmPasswordReset(
  token: string,
  newPassword: string,
): Promise<{ sessions_revoked: number }> {
  return post<{ password_reset: boolean; sessions_revoked: number }>(
    "/auth/password-reset/confirm",
    { token, new_password: newPassword },
  );
}

export async function acceptInvitation(
  token: string,
  values: { password?: string; full_name?: string },
): Promise<{ workspace_id: string; role: string; account_created: boolean }> {
  return post<{ workspace_id: string; role: string; account_created: boolean }>(
    "/auth/invitations/accept",
    { token, ...values },
  );
}

/**
 * Client-side policy check, mirroring the server's.
 *
 * The server is the authority and will refuse a weak password either way; this
 * exists so the person finds out before the round trip. It is not a security
 * control and is not treated as one.
 */
export function validatePassword(value: string, confirmation?: string): string | null {
  if (value.length < PASSWORD_MIN_LENGTH) {
    return `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
  }
  if (confirmation !== undefined && value !== confirmation) {
    return "The two passwords do not match.";
  }
  return null;
}

export function validateEmail(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed || !trimmed.includes("@") || trimmed.startsWith("@")) {
    return "Enter an email address.";
  }
  return null;
}
