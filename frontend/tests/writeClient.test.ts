import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WriteApiError, approve, forgetWorkspace, resolveWorkspaceId } from "@/lib/writeClient";

/**
 * The write client's two obligations that are not obvious from reading it: every
 * mutation carries the CSRF token and the workspace header, and a refusal is
 * reported with the backend's own words rather than a status code.
 *
 * Both matter because a request that omits them is refused by the backend —
 * silently, from the operator's point of view, since the button does nothing.
 */
interface Route {
  status?: number;
  body: unknown;
}

describe("writeClient", () => {
  const calls: { url: string; init: RequestInit }[] = [];
  let routes: Record<string, Route> = {};

  function answer(url: string): Route {
    if (url.includes("/auth/me")) return routes["/auth/me"] ?? { body: { workspaces: [{ id: "ws-1" }] } };
    if (url.includes("/auth/csrf")) {
      return routes["/auth/csrf"] ?? { body: { csrf_token: "token-abc", header_name: "X-CSRF-Token" } };
    }
    for (const [path, route] of Object.entries(routes)) {
      if (url.includes(path)) return route;
    }
    return { body: {} };
  }

  beforeEach(() => {
    calls.length = 0;
    routes = {};
    forgetWorkspace();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init: init ?? {} });
        const route = answer(url);
        return new Response(JSON.stringify(route.body), {
          status: route.status ?? 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    forgetWorkspace();
  });

  function headersOf(call: { init: RequestInit }): Record<string, string> {
    return (call.init.headers ?? {}) as Record<string, string>;
  }

  it("resolves the workspace from the session once and caches it", async () => {
    expect(await resolveWorkspaceId()).toBe("ws-1");
    expect(await resolveWorkspaceId()).toBe("ws-1");
    expect(calls.filter((call) => call.url.includes("/auth/me"))).toHaveLength(1);
  });

  it("refuses to send anything before the workspace is known", async () => {
    routes["/auth/me"] = { status: 403, body: { detail: "no membership" } };
    await expect(approve("p-1")).rejects.toBeInstanceOf(WriteApiError);
    expect(calls.some((call) => call.url.includes("/approve"))).toBe(false);
  });

  it("sends the workspace header, the CSRF token and the body on a write", async () => {
    routes["/approve"] = {
      body: {
        proposal_id: "p-1",
        change_ref: "CHG-0001",
        decision: "approved",
        proposal_status: "approved",
        validation_status: "validated",
        request_status: "approved",
        approvals_received: 1,
        required_approvals: 1,
        complete: true,
        audit_id: "a-1",
        detail: "Approved by one approver.",
      },
    };

    const result = await approve("p-1", "because");

    expect(result.audit_id).toBe("a-1");
    const write = calls.at(-1)!;
    expect(write.url).toContain("/api/v1/approvals/p-1/approve");
    expect(write.init.method).toBe("POST");
    expect(write.init.body).toBe(JSON.stringify({ notes: "because" }));
    const headers = headersOf(write);
    expect(headers["x-workspace-id"]).toBe("ws-1");
    // The header name comes from the CSRF endpoint, never hardcoded here.
    expect(headers["X-CSRF-Token"]).toBe("token-abc");
    expect(headers["content-type"]).toBe("application/json");
  });

  it("sends a null note rather than an empty string when there is none", async () => {
    routes["/approve"] = { body: { audit_id: "a-1" } };
    await approve("p-1");
    expect(calls.at(-1)!.init.body).toBe(JSON.stringify({ notes: null }));
  });

  it("surfaces a refusal with the backend's detail", async () => {
    routes["/approve"] = {
      status: 409,
      body: { detail: "A CRITICAL proposal needs a second approver." },
    };
    await expect(approve("p-1")).rejects.toThrow(/second approver/);
  });

  it("explains an expired session in words", async () => {
    routes["/auth/me"] = { status: 401, body: {} };
    await expect(resolveWorkspaceId()).rejects.toThrow(/Not signed in/);
  });

  it("re-caches the workspace after a sign-out", async () => {
    await resolveWorkspaceId();
    forgetWorkspace();
    await resolveWorkspaceId();
    expect(calls.filter((call) => call.url.includes("/auth/me"))).toHaveLength(2);
  });
});
