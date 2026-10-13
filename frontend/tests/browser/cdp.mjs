/**
 * A minimal Chrome DevTools Protocol driver, with no dependencies.
 *
 * Phase 25 is the first time this project has driven its own console in a
 * browser against its own API. Playwright would do this well and is one
 * `npm install` away; it was declined because it brings ~40 MB of browser
 * downloads and an API surface larger than the six screens under test, and
 * because §41 asks for the DevTools signal specifically — console messages,
 * network events, cookies, storage and performance — which is exactly what
 * CDP exposes directly.
 *
 * Everything here is a thin wrapper over one WebSocket. Node 22 has a global
 * `WebSocket`; no package is imported anywhere in this file.
 *
 * The alternative, once the console grows more screens, is Playwright with
 * this file as the thing it replaced.
 */

import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

/** Every console call, exception and failed request seen since attach. */
export class Recorder {
  constructor() {
    this.console = [];
    this.errors = [];
    this.failedRequests = [];
    this.responses = [];
    this.security = [];
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export class Browser {
  constructor(proc, wsUrl, userDataDir) {
    this.proc = proc;
    this.wsUrl = wsUrl;
    this.userDataDir = userDataDir;
    this.ws = null;
    this.nextId = 1;
    this.pending = new Map();
    this.sessionId = null;
    this.recorder = new Recorder();
    this.listeners = new Set();
  }

  /**
   * Launch headless Chrome and attach to a fresh page.
   *
   * `--remote-allow-origins=*` is required: without it Chrome rejects the
   * WebSocket upgrade from a non-browser client since Chrome 111.
   */
  static async launch({ port = 9222, headless = true } = {}) {
    const binary = CHROME_CANDIDATES.find(Boolean);
    if (!binary) throw new Error("no Chrome binary found; set CHROME_PATH");

    const userDataDir = await mkdtemp(join(tmpdir(), "catalogguard-cdp-"));
    const args = [
      `--remote-debugging-port=${port}`,
      "--remote-allow-origins=*",
      `--user-data-dir=${userDataDir}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-gpu",
      "--disable-dev-shm-usage",
      "--hide-scrollbars",
      // A fixed window size makes the responsive checks reproducible.
      "--window-size=1440,900",
      "about:blank",
    ];
    if (headless) args.unshift("--headless=new");

    const proc = spawn(binary, args, { stdio: ["ignore", "ignore", "pipe"] });
    const browser = new Browser(proc, null, userDataDir);

    // Wait for the debugging endpoint rather than sleeping a guessed interval:
    // a slow machine would otherwise fail the whole run on a race.
    let wsUrl = null;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      try {
        const response = await fetch(`http://127.0.0.1:${port}/json/version`);
        const info = await response.json();
        wsUrl = info.webSocketDebuggerUrl;
        if (wsUrl) break;
      } catch {
        // not listening yet
      }
      await sleep(100);
    }
    if (!wsUrl) {
      proc.kill("SIGKILL");
      throw new Error("Chrome did not expose a DevTools endpoint");
    }

    browser.wsUrl = wsUrl;
    await browser.#connect();
    return browser;
  }

  async #connect() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", reject, { once: true });
    });
    this.ws.addEventListener("message", (event) => this.#onMessage(event.data));

    const { targetId } = await this.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await this.send("Target.attachToTarget", {
      targetId,
      flatten: true,
    });
    this.sessionId = sessionId;

    await this.send("Page.enable");
    await this.send("Runtime.enable");
    await this.send("Network.enable");
    await this.send("Log.enable");
    await this.send("Performance.enable", { timeDomain: "timeTicks" });

    this.on("Runtime.consoleAPICalled", (params) => {
      this.recorder.console.push({
        type: params.type,
        text: (params.args ?? [])
          .map((a) => a.value ?? a.description ?? a.unserializableValue ?? a.type)
          .join(" "),
      });
    });
    this.on("Runtime.exceptionThrown", (params) => {
      const details = params.exceptionDetails ?? {};
      this.recorder.errors.push({
        text: details.exception?.description ?? details.text ?? "unknown",
        url: details.url,
        line: details.lineNumber,
      });
    });
    this.on("Log.entryAdded", (params) => {
      const entry = params.entry ?? {};
      // Network errors also arrive as Log entries; Network.loadingFailed is
      // the authoritative record, so these are recorded separately to avoid
      // counting one failure twice.
      if (entry.source === "network" && entry.level === "error") {
        this.recorder.security.push({ text: entry.text, url: entry.url });
      } else if (entry.level === "error" || entry.level === "warning") {
        this.recorder.console.push({
          type: `log:${entry.source}`,
          text: entry.text,
        });
      }
    });
    this.on("Network.loadingFailed", (params) => {
      const request = this.#requests.get(params.requestId) ?? {};
      this.recorder.failedRequests.push({
        type: params.type,
        error: params.errorText,
        method: request.method,
        url: request.url,
      });
    });
    this.on("Network.requestWillBeSent", (params) => {
      this.#requests.set(params.requestId, {
        url: params.request.url,
        method: params.request.method,
      });
    });
    this.on("Network.responseReceived", (params) => {
      const request = this.#requests.get(params.requestId) ?? {};
      this.recorder.responses.push({
        url: params.response.url,
        status: params.response.status,
        method: request.method,
        fromCache: params.response.fromDiskCache,
        type: params.type,
        requestId: params.requestId,
      });
    });
    this.on("Network.loadingFinished", () => {
      // Keep the request map bounded: a long run would otherwise grow it for
      // the life of the browser.
      if (this.#requests.size > 500) {
        this.#requests.delete(this.#requests.keys().next().value);
      }
    });
  }

  #requests = new Map();

  on(event, handler) {
    this.listeners.add({ event, handler });
  }

  #onMessage(raw) {
    const message = JSON.parse(raw);
    if (message.id && this.pending.has(message.id)) {
      const { resolve, reject, method } = this.pending.get(message.id);
      this.pending.delete(message.id);
      // The method name matters: a protocol error without it ("Object
      // reference chain is too long") is unactionable.
      if (message.error) {
        reject(new Error(`${method} failed: ${message.error.message} (${message.error.code})`));
      } else {
        resolve(message.result);
      }
      return;
    }
    if (message.method) {
      for (const listener of this.listeners) {
        if (listener.event === message.method) listener.handler(message.params, message.sessionId);
      }
    }
  }

  send(method, params = {}) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (this.sessionId) payload.sessionId = this.sessionId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method });
      this.ws.send(JSON.stringify(payload));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP timeout: ${method}`));
        }
      }, 30000);
    });
  }

  async navigate(url) {
    const loaded = new Promise((resolve) => {
      const handler = () => resolve();
      this.on("Page.loadEventFired", handler);
    });
    await this.send("Page.navigate", { url });
    await Promise.race([loaded, sleep(15000)]);
    // Next.js streams and hydrates after `load`; without this the first
    // assertion can run against server HTML with no React attached.
    await this.waitForHydration();
  }

  /**
   * Wait until React has taken over the document.
   *
   * Looks for any `__react*` key on any element within a few levels of
   * `<body>`, rather than assuming a root id. Next.js App Router mounts into
   * a `nextjs-portal` element, so checking `#__next` reported "not hydrated"
   * on a fully interactive page — and the first click was then dispatched
   * into server-rendered HTML that no handler was attached to, which looks
   * exactly like a dead button.
   *
   * This waits rather than sleeping, because a genuine hydration error is
   * what this phase is looking for and must not be waited away.
   */
  async waitForHydration(timeout = 20000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const { result } = await this.send("Runtime.evaluate", {
        expression: `(() => {
          const found = (el, depth) => {
            if (depth > 4) return false;
            for (const key of Object.keys(el)) if (key.startsWith('__react')) return true;
            for (const child of el.children) if (found(child, depth + 1)) return true;
            return false;
          };
          return found(document.body, 0);
        })()`,
        returnByValue: true,
      });
      if (result.value) return true;
      await sleep(100);
    }
    return false;
  }

  async evaluate(expression) {
    const { result, exceptionDetails } = await this.send("Runtime.evaluate", {
      expression: `(() => { ${expression} })()`,
      returnByValue: true,
      awaitPromise: true,
    });
    if (exceptionDetails) {
      throw new Error(
        `evaluate failed: ${exceptionDetails.exception?.description ?? exceptionDetails.text}`,
      );
    }
    return result.value;
  }

  /**
   * Wait for a condition to become true in the page.
   *
   * The expression is coerced with `Boolean(...)` rather than returned as-is.
   * Callers naturally pass selectors like `document.querySelector('main')`,
   * and returning a DOM node with `returnByValue` fails the protocol call with
   * "Object reference chain is too long" — an error that says nothing about
   * what was being waited for.
   */
  async waitFor(expression, { timeout = 15000, label = expression } = {}) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const satisfied = await this.evaluate(`return Boolean(${expression});`);
      if (satisfied) return true;
      await sleep(120);
    }
    throw new Error(`timed out waiting for: ${label}`);
  }

  async cookies() {
    const { cookies } = await this.send("Network.getAllCookies");
    return cookies;
  }

  async storage() {
    return this.evaluate(`
      const out = { local: {}, session: {} };
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        out.local[key] = localStorage.getItem(key);
      }
      for (let i = 0; i < sessionStorage.length; i += 1) {
        const key = sessionStorage.key(i);
        out.session[key] = sessionStorage.getItem(key);
      }
      return out;
    `);
  }

  async setViewport(width, height, deviceScaleFactor = 1, mobile = false) {
    await this.send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor,
      mobile,
      screenWidth: width,
      screenHeight: height,
    });
  }

  /**
   * Paint-level metrics for §41's performance check.
   *
   * LCP and CLS come from a PerformanceObserver installed *now*, which is a
   * limitation worth naming: it reports the largest paint since this call, not
   * since navigation. The validator installs it before navigating, so in
   * practice it covers the load it is about to measure.
   */
  async webVitals() {
    return this.evaluate(`
      return new Promise((resolve) => {
        const observer = new PerformanceObserver((list) => {
          const entries = list.getEntries();
          const lcp = entries.filter((e) => e.entryType === 'largest-contentful-paint');
          const cls = entries.filter((e) => e.entryType === 'layout-shift');
          let total = 0;
          for (const entry of cls) if (!entry.hadRecentInput) total += entry.value;
          resolve({
            lcp: lcp.length ? Math.round(lcp[lcp.length - 1].startTime) : null,
            cls: Number(total.toFixed(4)),
            samples: { lcp: lcp.length, cls: cls.length },
          });
        });
        try {
          observer.observe({ type: 'largest-contentful-paint', buffered: true });
          observer.observe({ type: 'layout-shift', buffered: true });
        } catch (error) {
          resolve({ lcp: null, cls: null, error: String(error) });
        }
        setTimeout(() => resolve({ lcp: null, cls: null, error: 'no entries' }), 1500);
      });
    `);
  }

  async metrics() {
    const { metrics } = await this.send("Performance.getMetrics");
    const wanted = new Set([
      "TaskDuration",
      "ScriptDuration",
      "LayoutDuration",
      "RecalcStyleDuration",
      "DomContentLoaded",
      "JSHeapUsedSize",
      "Nodes",
      "LayoutCount",
    ]);
    return Object.fromEntries(metrics.filter((m) => wanted.has(m.name)).map((m) => [m.name, m.value]));
  }

  async screenshot(path, { fullPage = false } = {}) {
    const params = { format: "png", captureBeyondViewport: fullPage };
    if (fullPage) {
      const metrics = await this.send("Page.getLayoutMetrics");
      const { contentSize } = metrics;
      params.clip = {
        x: 0,
        y: 0,
        width: contentSize.width,
        height: Math.min(contentSize.height, 12000),
        scale: 1,
      };
    }
    const { data } = await this.send("Page.captureScreenshot", params);
    const { writeFile } = await import("node:fs/promises");
    await writeFile(path, Buffer.from(data, "base64"));
    return path;
  }

  /**
   * Type into a field the way a person would.
   *
   * Focuses the element and sends the text through `Input.insertText`, so the
   * browser generates a genuine `input` event. Setting `.value` from script
   * and dispatching a synthetic event looks equivalent and is not: React's
   * value tracker ignores a change it believes it already knows about, so the
   * component state stayed empty, the form's `required` fields blocked
   * submission, and the "Sign in" button did nothing at all — a silent no-op
   * with no console error to explain it.
   */
  async fill(selector, value) {
    const focused = await this.evaluate(`
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return false;
      el.focus();
      el.select?.();
      return document.activeElement === el;
    `);
    if (!focused) return false;
    await this.send("Input.insertText", { text: value });
    // A trailing key event closes the sequence React uses to commit on blur.
    await this.send("Input.dispatchKeyEvent", {
      type: "keyDown",
      key: "Tab",
      code: "Tab",
      windowsVirtualKeyCode: 9,
    });
    await this.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab" });
    return true;
  }

  /** Focus and press Enter, for forms that submit on the keyboard. */
  async pressEnter() {
    await this.send("Input.dispatchKeyEvent", {
      type: "rawKeyDown",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
      nativeVirtualKeyCode: 13,
    });
    await this.send("Input.dispatchKeyEvent", {
      type: "char",
      text: "\r",
      key: "Enter",
    });
    await this.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter" });
  }

  async click(selector) {
    return this.evaluate(`
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return false;
      el.click();
      return true;
    `);
  }

  async text(selector) {
    return this.evaluate(`
      const el = document.querySelector(${JSON.stringify(selector)});
      return el ? el.innerText : null;
    `);
  }

  async close() {
    try {
      this.ws?.close();
    } catch {
      // already closed
    }
    this.proc.kill("SIGTERM");
    await sleep(300);
    if (!this.proc.killed) this.proc.kill("SIGKILL");
    await rm(this.userDataDir, { recursive: true, force: true }).catch(() => {});
  }
}