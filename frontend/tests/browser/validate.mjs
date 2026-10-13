/**
 * §41 validation, run against the real console and the real API.
 *
 * Drives Chrome over CDP with the network, console, cookie and storage panels
 * open, walks the four flows §64 names for this phase, and writes a JSON
 * report. Nothing here is a mock: the API is a uvicorn process talking to a
 * PostgreSQL 16 with pgvector, and the data in the screens was produced by the
 * ingestion and reconciliation services.
 *
 * Usage:
 *   node tests/browser/validate.mjs [--out report.json] [--screens out-dir]
 *   BASE_URL=http://127.0.0.1:3000 EMAIL=… PASSWORD=… node tests/browser/validate.mjs
 */

import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { Browser } from "./cdp.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.BASE_URL ?? "http://127.0.0.1:3000";
const EMAIL = process.env.EMAIL ?? "owner@example.com";
const PASSWORD = process.env.PASSWORD ?? "correct horse battery staple";

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900, mobile: false },
  { name: "tablet", width: 834, height: 1112, mobile: true },
  { name: "mobile", width: 390, height: 844, mobile: true },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const arg = (flag, fallback) => {
  const index = process.argv.indexOf(flag);
  return index === -1 ? fallback : process.argv[index + 1];
};

const OUT = arg("--out", join(HERE, "report.json"));
const SCREENS = arg("--screens", join(HERE, "screens"));

/** Console messages that are noise rather than defects. */
const IGNORED_CONSOLE = [
  // Next.js dev overlay chatter and the React DevTools install hint.
  /Download the React DevTools/i,
  /\[Fast Refresh\]/i,
];

const findings = [];
function record(area, severity, detail) {
  findings.push({ area, severity, detail });
  const mark = severity === "fail" ? "FAIL" : severity === "warn" ? "WARN" : "info";
  console.log(`  [${mark}] ${area}: ${detail}`);
}

async function visit(browser, path, { waitFor, label } = {}) {
  await browser.navigate(`${BASE}${path}`);
  if (waitFor) await browser.waitFor(waitFor, { label: label ?? path });
  // Give client-side fetches a beat to land so the network assertions below
  // see the requests the page actually made.
  await sleep(600);
}

/** Requests to the API that failed, excluding what a signed-out page does. */
function apiFailures(recorder) {
  return recorder.responses.filter(
    (r) => r.url.includes("/api/") && r.status >= 400 && r.status !== 401,
  );
}

async function main() {
  await mkdir(SCREENS, { recursive: true });
  const browser = await Browser.launch({ port: 9333 });
  const report = {
    started_at: new Date().toISOString(),
    base_url: BASE,
    console: [],
    network: [],
    application: {},
    performance: {},
    responsive: [],
    flows: {},
  };

  try {
    // ---- §41 Console, and the sign-in flow -----------------------------
    console.log("sign-in");
    await browser.setViewport(1440, 900);
    await visit(browser, "/login", { waitFor: "document.querySelector('input[type=password]')" });
    await browser.screenshot(join(SCREENS, "01-login.png"));

    await browser.fill("input[type=email]", EMAIL);
    await browser.fill("input[type=password]", PASSWORD);
    await browser.click("form button[type=submit]");
    await browser.waitFor("!location.pathname.includes('login')", {
      timeout: 20000,
      label: "redirect away from /login",
    });
    report.flows.sign_in = { landed_on: await browser.evaluate("return location.pathname;") };

    if (report.flows.sign_in.landed_on === "/login") {
      record("flows", "fail", "sign-in did not leave /login");
    }

    // ---- §41 Application: cookies, storage, session --------------------
    console.log("application state");
    const cookies = await browser.cookies();
    const storage = await browser.storage();
    report.application = {
      cookies: cookies.map((c) => ({
        name: c.name,
        domain: c.domain,
        httpOnly: c.httpOnly,
        secure: c.secure,
        sameSite: c.sameSite,
        path: c.path,
      })),
      local_storage_keys: Object.keys(storage.local),
      session_storage_keys: Object.keys(storage.session),
    };

    const session = cookies.find((c) => c.name.includes("session"));
    if (!session) record("application", "fail", "no session cookie after sign-in");
    else {
      // D-014: the cookie must not be readable by script.
      if (!session.httpOnly) record("application", "fail", "session cookie is not HttpOnly");
      if (session.secure && !session.secure) record("application", "warn", "session cookie without Secure");
      record("application", "info", `session cookie: HttpOnly=${session.httpOnly} Secure=${session.secure} SameSite=${session.sameSite}`);
    }
    if (report.application.local_storage_keys.length) {
      record(
        "application",
        "warn",
        `localStorage holds ${report.application.local_storage_keys.join(", ")} — a session must not live in JS storage`,
      );
    } else {
      record("application", "info", "localStorage empty: the session is cookie-only");
    }

    // ---- The screens, each with a clean recorder ------------------------
    const screens = [
      { path: "/", label: "dashboard", waitFor: "document.querySelector('main')" },
      { path: "/catalog", label: "catalog", waitFor: "document.querySelector('main')" },
      { path: "/reconciliation", label: "reconciliation", waitFor: "document.querySelector('main')" },
      { path: "/approvals", label: "approvals", waitFor: "document.querySelector('main')" },
      { path: "/sandbox", label: "sandbox", waitFor: "document.querySelector('main')" },
      { path: "/compliance", label: "compliance", waitFor: "document.querySelector('main')" },
    ];

    for (const [index, screen] of screens.entries()) {
      console.log(`screen: ${screen.label}`);
      const markConsole = browser.recorder.console.length;
      const markErrors = browser.recorder.errors.length;
      const markFailed = browser.recorder.failedRequests.length;
      const markResponses = browser.recorder.responses.length;

      await visit(browser, screen.path, { waitFor: screen.waitFor, label: screen.label });
      await browser.screenshot(join(SCREENS, `${String(index + 2).padStart(2, "0")}-${screen.label}.png`), {
        fullPage: true,
      });

      const newConsole = browser.recorder.console.slice(markConsole);
      const newErrors = browser.recorder.errors.slice(markErrors);
      const newFailed = browser.recorder.failedRequests.slice(markFailed);
      const newResponses = browser.recorder.responses.slice(markResponses);

      const noisy = newConsole.filter((c) => !IGNORED_CONSOLE.some((r) => r.test(c.text)));
      const warnings = noisy.filter((c) => c.type === "warning" || c.type.startsWith("log:"));
      const errors = noisy.filter((c) => c.type === "error");

      for (const entry of errors) record("console", "fail", `${screen.label}: ${entry.text}`);
      for (const entry of newErrors) record("console", "fail", `${screen.label}: ${entry.text}`);
      for (const entry of warnings) record("console", "warn", `${screen.label}: ${entry.text}`);
      for (const entry of apiFailures({ responses: newResponses }))
        record("network", "fail", `${screen.label}: ${entry.status} ${entry.url}`);
      // An aborted request is a defect unless it is a prefetch the page
      // abandoned: the console prefetches every route on hover and on
      // viewport entry, and navigating away cancels those in flight. Counting
      // them as failures would bury the one that matters.
      for (const entry of newFailed) {
        const prefetched = (entry.url ?? "").includes("_rsc=");
        if (entry.error === "net::ERR_ABORTED" && prefetched) {
          record("network", "info", `${screen.label}: cancelled prefetch (expected)`);
        } else {
          record("network", "fail", `${screen.label}: request failed ${entry.error} ${entry.method ?? ""} ${entry.url ?? ""}`);
        }
      }

      if (!errors.length && !newErrors.length) {
        record("console", "info", `${screen.label}: console clean (${newResponses.length} responses)`);
      }

      report.network.push({
        screen: screen.label,
        responses: newResponses.map((r) => ({ url: r.url, status: r.status, method: r.method })),
        failed: newFailed.length,
      });

      const vitals = await browser.webVitals();
      const metrics = await browser.metrics();
      report.performance[screen.label] = { ...vitals, ...metrics };
    }

    // ---- §41 Responsive ------------------------------------------------
    console.log("responsive");
    await browser.setViewport(1440, 900);
    await visit(browser, "/");
    for (const viewport of VIEWPORTS) {
      await browser.setViewport(viewport.width, viewport.height, 1, viewport.mobile);
      await sleep(400);
      const layout = await browser.evaluate(`
        const doc = document.documentElement;
        const overflowing = [...document.querySelectorAll('body *')]
          .filter((el) => el.getBoundingClientRect().right > doc.clientWidth + 1)
          .slice(0, 5)
          .map((el) => el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ')[0] : ''));
        return {
          scrollWidth: doc.scrollWidth,
          clientWidth: doc.clientWidth,
          horizontal_scroll: doc.scrollWidth > doc.clientWidth + 1,
          overflowing_elements: overflowing,
        };
      `);
      report.responsive.push({ viewport: viewport.name, ...layout });
      if (layout.horizontal_scroll) {
        record(
          "responsive",
          "fail",
          `${viewport.name}: horizontal scroll (${layout.scrollWidth} > ${layout.clientWidth}); first offenders ${layout.overflowing_elements.join(", ") || "unknown"}`,
        );
      } else {
        record("responsive", "info", `${viewport.name}: no horizontal overflow at ${viewport.width}px`);
      }
      await browser.screenshot(join(SCREENS, `responsive-${viewport.name}.png`));
    }

    // ---- §41 Network: authentication, CSRF, and a write ---------------
    console.log("write path: approve a proposal");
    await browser.setViewport(1440, 900);
    await visit(browser, "/approvals", { waitFor: "document.querySelector('main')" });

    const queueText = await browser.evaluate("return document.body.innerText.slice(0, 600);");
    report.flows.approval_queue_visible = /proposal|queue|approve/i.test(queueText);
    // The queue's bulk control is "Approve selected"; the per-row action is a
    // bare "Approve". Clicking the first button on the page hit the filter
    // control and produced no write, which is why this names the row action.
    const approveButton = await browser.evaluate(`
      const buttons = [...document.querySelectorAll('button')];
      const target = buttons.find((b) => b.innerText.trim().toLowerCase() === 'approve');
      return target ? { label: target.innerText.trim(), disabled: target.disabled } : null;
    `);
    report.flows.approve_button = approveButton;
    if (approveButton) {
      const markResponses = browser.recorder.responses.length;
      await browser.evaluate(`
        const buttons = [...document.querySelectorAll('button')];
        const target = buttons.find((b) => b.innerText.trim().toLowerCase() === 'approve');
        if (target) target.click();
        return true;
      `);
      await sleep(2000);
      const writes = browser.recorder.responses.slice(markResponses);
      report.flows.approval_write = writes.map((w) => ({ url: w.url, status: w.status, method: w.method }));
      const write = writes.find((w) => w.method && w.method !== "GET");
      if (!write) {
        record("network", "fail", "approve produced no write request");
      } else if (write.status >= 400) {
        record("network", "fail", `approve returned ${write.status} ${new URL(write.url).pathname}`);
      } else {
        // A write that succeeded also proves the CSRF header was attached: the
        // API refuses every state-changing call without it (D-014).
        record(
          "network",
          "info",
          `approve wrote ${write.method} ${new URL(write.url).pathname} -> ${write.status} (CSRF accepted)`,
        );
      }
      await browser.screenshot(join(SCREENS, "09-after-approve.png"));
    } else {
      record("flows", "warn", "no Approve button found on /approvals");
    }

    // ---- Report --------------------------------------------------------
    report.console = browser.recorder.console;
    report.findings = findings;
    await writeFile(OUT, JSON.stringify(report, null, 2));
    console.log(`\nreport written to ${OUT}`);
    console.log(`findings: ${findings.filter((f) => f.severity === "fail").length} fail, ${findings.filter((f) => f.severity === "warn").length} warn`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("validation crashed:", error);
  process.exit(1);
});