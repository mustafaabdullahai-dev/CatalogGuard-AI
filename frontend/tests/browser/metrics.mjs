/**
 * §58 frontend performance, measured in a browser.
 *
 * §58 lists SSR, pagination, virtualization, caching, optimistic UI, skeleton
 * loading and error boundaries. Two of those are properties of the source that
 * a reviewer can check; the rest are claims about what a browser does, and this
 * is where they are measured — with the built console against the real API,
 * holding 405 products and 405 proposals, which is a full page of both tables.
 *
 * What is recorded per route:
 *
 * - **first-load bytes**, split by kind, from the network events rather than
 *   from the build directory: a chunk that exists is not a chunk that loads;
 * - **DOM nodes and rendered rows**, which is where an un-virtualized table
 *   becomes visible;
 * - **LCP and CLS**, the two numbers a person actually feels;
 * - **request count**, so a screen that fetches the same thing twice is
 *   visible rather than felt.
 *
 * No assertion on bytes here: a budget belongs to a deployment (Phase 28), and
 * a number asserted against a laptop measures the laptop. What is asserted is
 * that a page renders at all, and that nothing blocks it from rendering.
 */

import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { Browser } from "./cdp.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.BASE_URL ?? "http://127.0.0.1:3001";
const EMAIL = process.env.EMAIL ?? "owner@example.com";
const PASSWORD = process.env.PASSWORD ?? "correct horse battery staple";

const ROUTES = [
  { path: "/", label: "dashboard", rows: "main tr" },
  { path: "/catalog", label: "catalog", rows: "main tbody tr" },
  { path: "/reconciliation", label: "reconciliation", rows: "main tbody tr" },
  // The queue renders cards rather than a table, so its rows are counted the
  // way they appear. Measuring "0 rows" because the selector assumed `<tbody>`
  // would have been a false reading of a full queue.
  { path: "/approvals", label: "approvals", rows: "main input[type=checkbox]" },
  { path: "/sandbox", label: "sandbox", rows: "main tbody tr" },
  { path: "/compliance", label: "compliance", rows: "main tbody tr" },
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const kb = (bytes) => Math.round(bytes / 1024);

/** Sum transferred bytes per resource kind from the recorder's responses. */
function bytesByKind(responses) {
  const totals = { script: 0, stylesheet: 0, document: 0, other: 0, count: 0 };
  for (const response of responses) {
    const kind =
      response.type === "Script"
        ? "script"
        : response.type === "Stylesheet"
          ? "stylesheet"
          : response.type === "Document"
            ? "document"
            : "other";
    // `encodedDataLength` from the loading-finished event: the bytes that
    // crossed the wire after compression. Measured rather than estimated,
    // because a build-directory size is not a page weight.
    const size = response.encodedBytes ?? 0;
    totals[kind] += size;
    totals.count += 1;
  }
  return totals;
}

async function signIn(browser) {
  await browser.navigate(`${BASE}/login`);
  await browser.waitForHydration();
  await browser.fill("input[type=email]", EMAIL);
  await browser.fill("input[type=password]", PASSWORD);
  await browser.click("form button[type=submit]");
  await browser.waitFor("!location.pathname.includes('login')", { timeout: 20000 });
}

async function main() {
  await mkdir(HERE, { recursive: true });
  const browser = await Browser.launch({ port: 9350 });
  const report = { base_url: BASE, routes: [], totals: {} };

  try {
    await browser.setViewport(1440, 900);
    await signIn(browser);

    for (const route of ROUTES) {
      const markResponses = browser.recorder.responses.length;
      await browser.navigate(`${BASE}${route.path}`);
      await browser.waitFor("document.querySelector('main')", { label: route.path });
      await sleep(700);

      const responses = browser.recorder.responses.slice(markResponses);
      const apiCalls = responses.filter((r) => r.url.includes("/api/"));
      const vitals = await browser.webVitals();
      const metrics = await browser.metrics();

      const rendered = await browser.evaluate(`
        const rows = document.querySelectorAll(${JSON.stringify(route.rows)}).length;
        return {
          rows,
          nodes: document.querySelectorAll('*').length,
          text_length: document.body.innerText.length,
          images: [...document.images].filter((img) => !img.complete || img.naturalWidth === 0).length,
        };
      `);

      const entry = {
        path: route.path,
        requests: responses.length,
        api_requests: apiCalls.length,
        api_failures: apiCalls.filter((r) => r.status >= 400).length,
        bytes: bytesByKind(responses),
        rows: rendered.rows,
        dom_nodes: rendered.nodes,
        broken_images: rendered.images,
        lcp_ms: vitals.lcp,
        cls: vitals.cls,
        layout_ms: metrics.LayoutDuration ?? null,
        script_ms: metrics.ScriptDuration ?? null,
        heap_mb: metrics.JSHeapUsedSize
          ? Math.round((metrics.JSHeapUsedSize / 1048576) * 10) / 10
          : null,
      };
      report.routes.push(entry);

      console.log(
        `${route.label.padEnd(14)} rows=${String(rendered.rows).padStart(4)} ` +
          `nodes=${String(rendered.nodes).padStart(5)} api=${entry.api_requests} ` +
          `lcp=${entry.lcp_ms ?? "?"}ms cls=${entry.cls ?? "?"} ` +
          `scripts=${kb(entry.bytes.script)}KB`,
      );
    }

    report.totals = {
      max_dom_nodes: Math.max(...report.routes.map((r) => r.dom_nodes)),
      max_rows: Math.max(...report.routes.map((r) => r.rows)),
      max_lcp_ms: Math.max(...report.routes.map((r) => r.lcp_ms ?? 0)),
      max_cls: Math.max(...report.routes.map((r) => r.cls ?? 0)),
      api_failures: report.routes.reduce((sum, r) => sum + r.api_failures, 0),
    };

    await writeFile(join(HERE, "metrics.json"), JSON.stringify(report, null, 2));
    console.log(`\n${JSON.stringify(report.totals, null, 2)}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("metrics crashed:", error);
  process.exit(1);
});