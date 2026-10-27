#!/usr/bin/env node
/**
 * Playwright scrape for 智愛中文平台 teacher per-student reports.
 *
 * Credentials (never logged / never written to disk):
 *   - Default: ILEARNER_USERNAME + ILEARNER_PASSWORD from the environment.
 *     Inject via KRU MCP terminal_run secretEnv (item=ilearner), e.g.
 *       secretEnv: { ILEARNER_USERNAME: "username", ILEARNER_PASSWORD: "password" }
 *   - KRU_FILL=1: load KRU browser extension, focus fields in order, wait for
 *     MCP credential_fill (item=ilearner, target=browser). Requires bridge paired.
 *
 * Env:
 *   HEADED=1          show browser (DISPLAY=:12). Default ON when USE_SYSTEM_CHROME
 *                     (WAF「One moment」常擋 headless／bundled Chromium)。
 *   USE_SYSTEM_CHROME=1  用系統 google-chrome channel（預設 1）；並 ignoreHTTPSErrors
 *                     （Playwright bundled CA 可能不認新 LE Root YE → ERR_CERT_AUTHORITY_INVALID）。
 *   KRU_FILL=1        wait for extension fill instead of env credentials
 *   KRU_EXTENSION=1   also load extension when using env login (optional)
 *   SCRAPE_TIMEOUT_MS overall budget (default 660000 ≈ 11 min; wrapper adds margin)
 *   DRY_RUN=1         skip live login; write tiny placeholder HTML for structure test
 */
import { chromium } from "playwright";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = __dirname;
const INBOX = path.join(ROOT, "scrape-inbox");
const LOGIN_URL = "https://chinese.i-learner.com.hk/";
const KRU_EXT =
  process.env.KRU_EXTENSION_PATH ||
  "/home/box/.local/opt/KRU_current/browser-extension";
const OVERALL_MS = Number(process.env.SCRAPE_TIMEOUT_MS || 660_000);
const FILL_WAIT_MS = Number(process.env.KRU_FILL_WAIT_MS || 180_000);

function die(msg, code = 1) {
  console.error(`[scrape_reports] ERROR: ${msg}`);
  process.exit(code);
}

function log(msg) {
  console.error(`[scrape_reports] ${msg}`);
}

function redactEnvPresent() {
  const u = Boolean(process.env.ILEARNER_USERNAME);
  const p = Boolean(process.env.ILEARNER_PASSWORD);
  return `ILEARNER_USERNAME=${u ? "set" : "missing"} ILEARNER_PASSWORD=${p ? "set" : "missing"}`;
}

function reportUrls() {
  const r = spawnSync("python3", ["report_urls.py"], {
    cwd: ROOT,
    encoding: "utf8",
  });
  if (r.status !== 0) {
    die(`report_urls.py failed: ${r.stderr || r.stdout}`);
  }
  const lines = (r.stdout || "").split(/\r?\n/);
  const out = {};
  let cur = null;
  for (const line of lines) {
    const m = line.match(/^(prev|current):\s/);
    if (m) {
      cur = m[1];
      continue;
    }
    if (cur && line.startsWith("http")) {
      out[cur] = line.trim();
      cur = null;
    }
  }
  if (!out.prev || !out.current) {
    die(`could not parse report URLs from report_urls.py:\n${r.stdout}`);
  }
  return out;
}

function looksLikeReport(html) {
  if (!html || html.length < 2000) return false;
  if (/id=["']login_form["']/.test(html)) return false;
  // Per-student report markers (tolerant)
  const markers = [
    /已完成練習/,
    /r_teachers_report_student/,
    /班別/,
    /學號/,
    /姓名/,
  ];
  const hits = markers.filter((re) => re.test(html)).length;
  return hits >= 2;
}

async function waitForKruFill(page, selector, label) {
  const el = page.locator(selector).first();
  await el.waitFor({ state: "visible", timeout: 30_000 });
  await el.click({ clickCount: 3 });
  await el.focus();
  log(`KRU_FILL: focused ${label} (${selector}) — call credential_fill item=ilearner module=${label} target=browser`);
  const start = Date.now();
  let last = await el.inputValue().catch(() => "");
  while (Date.now() - start < FILL_WAIT_MS) {
    await page.waitForTimeout(500);
    const v = await el.inputValue().catch(() => "");
    if (v && v !== last && v.length > 0) {
      log(`KRU_FILL: ${label} field received value (len=${v.length}, not logged)`);
      return;
    }
    last = v;
  }
  die(`timed out waiting for KRU fill on ${label}`);
}


async function selectChinesePlatform(page) {
  // First dropdown = 選擇平台; must pick 第2項「智愛中文平台」(value=reading)
  const sel = page.locator('select#platform, select[name="loginsys"]').first();
  await sel.waitFor({ state: "visible", timeout: 30_000 });
  await sel.selectOption({ label: "智愛中文平台" });
  const value = await sel.inputValue();
  if (value !== "reading") {
    // fallback by value then by index 1 (0 is placeholder)
    await sel.selectOption("reading").catch(async () => {
      await sel.selectOption({ index: 1 });
    });
  }
  const final = await sel.inputValue();
  const label = await sel.locator("option:checked").textContent();
  if (final !== "reading" && !(label || "").includes("智愛中文平台")) {
    die(`platform select failed: value=${final} label=${(label || "").trim()}`);
  }
  log(`platform selected: ${(label || "").trim()} (value=${final})`);
}

async function waitPastWaf(page) {
  // WAF「One moment」會自動 reload；等到 login_form 可見。
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    const has = await page.locator('input[name="username"]').isVisible().catch(() => false);
    if (has) return;
    const title = await page.title().catch(() => "");
    if (/one moment|稍候|just a moment/i.test(title)) {
      log("WAF interstitial detected; waiting for reload…");
      await page.waitForTimeout(3000);
      continue;
    }
    await page.waitForTimeout(1000);
  }
  die("timed out waiting past WAF / for login form");
}

async function loginWithEnv(page, username, password) {
  await page.goto(LOGIN_URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await waitPastWaf(page);
  await page.locator('input[name="username"]').waitFor({ state: "visible", timeout: 30_000 });

  // Order from skill: (1) username (2) password (3) LAST select platform (4) submit
  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(password);
  await selectChinesePlatform(page);
  await Promise.all([
    page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => null),
    page.locator('#login_form input[type="submit"]').click(),
  ]);
  await page.waitForTimeout(1500);
}

async function loginWithKruFill(page) {
  await page.goto(LOGIN_URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await waitPastWaf(page);
  await page.locator('input[name="username"]').waitFor({ state: "visible", timeout: 30_000 });

  await waitForKruFill(page, 'input[name="username"]', "username");
  await waitForKruFill(page, 'input[name="password"]', "password");
  // Platform AFTER credentials — must be 第2項「智愛中文平台」
  await selectChinesePlatform(page);
  log("KRU_FILL: submitting after platform 智愛中文平台");
  await Promise.all([
    page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => null),
    page.locator('#login_form input[type="submit"]').click(),
  ]);
  await page.waitForTimeout(1500);
}

async function assertLoggedIn(page) {
  const url = page.url();
  const userVisible = await page.locator('input[name="username"]').isVisible().catch(() => false);
  const passVisible = await page.locator('input[name="password"]').isVisible().catch(() => false);
  if (userVisible && passVisible) {
    die(`login appears to have failed (login fields still visible). url=${url}`);
  }
  log(`login OK (url=${url})`);
}

async function saveReport(page, url, outPath, label) {
  log(`navigating ${label}: ${url}`);
  await page.goto(url, { waitUntil: "networkidle", timeout: 120_000 }).catch(async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 30_000 });
  });
  // Let any late tables settle
  await page.waitForTimeout(2000);
  const html = await page.content();
  if (!looksLikeReport(html)) {
    // Save anyway for debugging, then fail
    fs.writeFileSync(outPath, html, "utf8");
    die(
      `${label} HTML does not look like a per-student report (saved ${outPath}, ${html.length} bytes, url=${page.url()}). Check login/session or URL.`
    );
  }
  fs.writeFileSync(outPath, html, "utf8");
  log(`saved ${label} → ${outPath} (${html.length} bytes)`);
}

async function dryRun() {
  fs.mkdirSync(INBOX, { recursive: true });
  const stub = `<!DOCTYPE html><html><head><title>DRY_RUN</title></head><body>
<table><tr><th>班別</th><th>學號</th><th>姓名</th><th>已完成練習數目</th></tr>
<tr><td>1A</td><td>1</td><td>測試</td><td>0</td></tr></table>
<!-- DRY_RUN placeholder; not a live r_teachers_report_student.php dump -->
</body></html>`;
  fs.writeFileSync(path.join(INBOX, "prev.html"), stub);
  fs.writeFileSync(path.join(INBOX, "current.html"), stub);
  const urls = reportUrls();
  log(`DRY_RUN: wrote stub HTML; would fetch:\n  prev=${urls.prev}\n  current=${urls.current}`);
  log(`credential env check: ${redactEnvPresent()}`);
}

async function main() {
  const t0 = Date.now();
  const watchdog = setTimeout(() => die(`overall scrape timeout (${OVERALL_MS}ms)`), OVERALL_MS);
  try {
    fs.mkdirSync(INBOX, { recursive: true });

    if (process.env.DRY_RUN === "1") {
      await dryRun();
      return;
    }

    const kruFill = process.env.KRU_FILL === "1";
    const username = process.env.ILEARNER_USERNAME || "";
    const password = process.env.ILEARNER_PASSWORD || "";

    if (!kruFill && (!username || !password)) {
      die(
        `missing credentials. Launch via KRU terminal_run with secretEnv ` +
          `{ILEARNER_USERNAME:"username", ILEARNER_PASSWORD:"password"} item=ilearner, ` +
          `or set KRU_FILL=1 with a paired browser bridge. (${redactEnvPresent()})`
      );
    }

    // 系統 Chrome＋headed：過 WAF JS challenge；ignoreHTTPSErrors：過新 LE 憑證鏈。
    const useSystemChrome = process.env.USE_SYSTEM_CHROME !== "0";
    const headed =
      process.env.HEADED === "1" ||
      (process.env.HEADED !== "0" && useSystemChrome);
    const loadExt = kruFill || process.env.KRU_EXTENSION === "1";
    if (loadExt && !fs.existsSync(path.join(KRU_EXT, "manifest.json"))) {
      die(`KRU extension not found at ${KRU_EXT}`);
    }

    const launchOpts = {
      headless: !headed,
      args: headed ? [] : ["--disable-dev-shm-usage"],
    };
    if (useSystemChrome) {
      launchOpts.channel = "chrome";
    }
    if (headed) {
      launchOpts.env = { ...process.env, DISPLAY: process.env.DISPLAY || ":12" };
    }
    log(
      `browser: channel=${useSystemChrome ? "chrome" : "bundled"} headed=${headed} ignoreHTTPSErrors=1`
    );

    const contextOpts = {
      viewport: { width: 1280, height: 900 },
      locale: "zh-HK",
      ignoreHTTPSErrors: true,
    };

    let context;
    let browser;
    if (loadExt) {
      // Persistent context required for --load-extension
      const userData = path.join(ROOT, ".pw-kru-profile");
      fs.mkdirSync(userData, { recursive: true });
      context = await chromium.launchPersistentContext(userData, {
        ...launchOpts,
        ...contextOpts,
        args: [
          ...(launchOpts.args || []),
          `--disable-extensions-except=${KRU_EXT}`,
          `--load-extension=${KRU_EXT}`,
        ],
      });
    } else {
      browser = await chromium.launch(launchOpts);
      context = await browser.newContext(contextOpts);
    }

    const page = context.pages()[0] || (await context.newPage());
    page.setDefaultTimeout(60_000);

    try {
      if (kruFill) {
        log("login mode: KRU_FILL (extension + credential_fill)");
        await loginWithKruFill(page);
      } else {
        log(`login mode: env secretEnv (${redactEnvPresent()})`);
        await loginWithEnv(page, username, password);
      }
      await assertLoggedIn(page);

      const urls = reportUrls();
      await saveReport(page, urls.prev, path.join(INBOX, "prev.html"), "prev");
      await saveReport(page, urls.current, path.join(INBOX, "current.html"), "current");
      log(`done in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
    } finally {
      await context.close().catch(() => {});
      if (browser) await browser.close().catch(() => {});
    }
  } finally {
    clearTimeout(watchdog);
  }
}

main().catch((err) => {
  die(err && err.stack ? err.stack : String(err));
});
