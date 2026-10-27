#!/usr/bin/env node
/**
 * Hybrid scrape: curl TLS1.2 login for reading_id, then headed Chrome
 * waits past report-page WAF and saves HTML.
 */
import { chromium } from "playwright";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const INBOX = path.join(ROOT, "scrape-inbox");
const JAR = "/tmp/il-hybrid-cookies.jar";
const UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

function log(m) {
  console.error(`[hybrid_scrape] ${m}`);
}
function die(m, c = 1) {
  console.error(`[hybrid_scrape] ERROR: ${m}`);
  process.exit(c);
}

function reportUrls() {
  const r = spawnSync("python3", ["report_urls.py"], { cwd: ROOT, encoding: "utf8" });
  if (r.status !== 0) die(`report_urls.py failed: ${r.stderr || r.stdout}`);
  const urls = (r.stdout || "").split(/\r?\n/).filter((l) => l.startsWith("http"));
  if (urls.length < 2) die(`need 2 urls, got ${urls.length}`);
  return { prev: urls[0], current: urls[1] };
}

function looksLikeReport(html) {
  if (!html || html.length < 2000) return false;
  if (/id=["']login_form["']/.test(html)) return false;
  if (/请稍候|One moment, please|Just a moment/i.test(html)) return false;
  if (!/已完成練習/.test(html)) return false;
  return [/班別/, /學號/, /姓名/, /平台班別/].some((re) => re.test(html));
}

function curlLogin() {
  const user = process.env.ILEARNER_USERNAME || "";
  const pass = process.env.ILEARNER_PASSWORD || "";
  if (!user || !pass) die("missing ILEARNER_USERNAME/PASSWORD");
  fs.rmSync(JAR, { force: true });
  let r = spawnSync(
    "curl",
    ["-sk", "--http1.1", "--tlsv1.2", "--max-time", "60", "-c", JAR, "-b", JAR, "-A", UA, "-o", "/dev/null", "https://chinese.i-learner.com.hk/"],
    { encoding: "utf8" }
  );
  if (r.status !== 0) die(`seed curl failed status=${r.status}`);
  r = spawnSync(
    "curl",
    [
      "-sk",
      "--http1.1",
      "--tlsv1.2",
      "--max-time",
      "60",
      "-c",
      JAR,
      "-b",
      JAR,
      "-A",
      UA,
      "-X",
      "POST",
      "https://chinese.i-learner.com.hk/login.php",
      "--data-urlencode",
      "loginsys=reading",
      "--data-urlencode",
      `username=${user}`,
      "--data-urlencode",
      `password=${pass}`,
      "-o",
      "/dev/null",
      "-w",
      "%{http_code}",
    ],
    { encoding: "utf8" }
  );
  const code = (r.stdout || "").trim();
  log(`curl login http=${code} status=${r.status}`);
  if (r.status !== 0) die(`login curl failed status=${r.status}`);
  const jar = fs.readFileSync(JAR, "utf8");
  if (!/\treading_id\t/.test(jar) && !jar.split("\n").some((l) => l.includes("\treading_id\t"))) {
    // also accept space-separated edge cases
    const names = jar
      .split("\n")
      .filter((l) => l && !l.startsWith("#"))
      .map((l) => l.split("\t")[5])
      .filter(Boolean);
    if (!names.includes("reading_id")) die(`no reading_id after curl login; cookies=${names.join(",")}`);
  }
  log("curl login OK (reading_id present)");
}

function parseNetscapeCookies(jarText) {
  const out = [];
  for (const line of jarText.split("\n")) {
    if (!line || line.startsWith("#")) continue;
    const parts = line.split("\t");
    if (parts.length < 7) continue;
    const [domain, , pathPart, secure, expires, name, value] = parts;
    out.push({
      name,
      value,
      domain: domain.startsWith(".") ? domain : domain,
      path: pathPart || "/",
      secure: String(secure).toUpperCase() === "TRUE",
      httpOnly: false,
      expires: Number(expires) > 0 ? Number(expires) : undefined,
    });
  }
  return out;
}

async function readContent(page) {
  for (let i = 0; i < 8; i++) {
    try {
      return await page.content();
    } catch (e) {
      const msg = e && e.message ? e.message : String(e);
      if (/navigating|changing the content|Execution context was destroyed/i.test(msg)) {
        await page.waitForTimeout(1500);
        await page.waitForLoadState("domcontentloaded", { timeout: 30_000 }).catch(() => null);
        continue;
      }
      throw e;
    }
  }
  return await page.content();
}

async function saveViaBrowser(page, url, outPath, label) {
  log(`browser ${label}: ${url}`);
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 120_000 }).catch(() => null);
  const deadline = Date.now() + 240_000;
  let html = "";
  let reloads = 0;
  while (Date.now() < deadline) {
    await page.waitForTimeout(3000);
    html = await readContent(page);
    if (looksLikeReport(html)) break;
    const title = await page.title().catch(() => "");
    if (/one moment|稍候|just a moment/i.test(title) || /One moment, please|请稍候/i.test(html.slice(0, 3000))) {
      log(`${label}: WAF waiting (reloads=${reloads})`);
      await page.waitForTimeout(5000);
      // let auto-reload finish; only force reload if stuck long
      if (reloads < 4 && Date.now() + 90_000 < deadline) {
        // no forced reload early — WAF script reloads itself
      } else if (reloads < 6) {
        reloads += 1;
        log(`${label}: soft reload #${reloads}`);
        await page.reload({ waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => null);
      }
      continue;
    }
    await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => null);
  }
  html = await readContent(page);
  fs.writeFileSync(outPath, html, "utf8");
  if (!looksLikeReport(html)) {
    die(`${label} not report-like (${html.length} bytes, url=${page.url()}, title=${await page.title().catch(() => "")})`);
  }
  log(`saved ${label} (${html.length} bytes)`);
}

async function main() {
  fs.mkdirSync(INBOX, { recursive: true });
  curlLogin();
  const cookies = parseNetscapeCookies(fs.readFileSync(JAR, "utf8"));
  log(`parsed ${cookies.length} cookies for browser`);

  const browser = await chromium.launch({
    channel: "chrome",
    headless: false,
    args: [
      "--disable-blink-features=AutomationControlled",
      "--disable-dev-shm-usage",
    ],
    ignoreDefaultArgs: ["--enable-automation"],
    env: { ...process.env, DISPLAY: process.env.DISPLAY || ":12" },
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    locale: "zh-HK",
    ignoreHTTPSErrors: true,
    userAgent: UA,
  });
  await context.addCookies(
    cookies.map((c) => ({
      name: c.name,
      value: c.value,
      domain: c.domain.replace(/^\./, "") === "chinese.i-learner.com.hk" || c.domain.includes("i-learner")
        ? c.domain.startsWith(".")
          ? c.domain
          : `.${c.domain.replace(/^\./, "")}`
        : ".i-learner.com.hk",
      path: c.path || "/",
      secure: true,
    }))
  );
  // normalize domains — Playwright wants host without invalid shapes
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  try {
    // verify session
    await page.goto("https://chinese.i-learner.com.hk/", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await page.waitForTimeout(2000);
    const jarCookies = await context.cookies("https://chinese.i-learner.com.hk");
    const names = jarCookies.map((c) => c.name);
    log(`browser cookies after inject: ${names.join(",")}`);
    if (!names.includes("reading_id")) {
      // re-add with exact domain from jar
      await context.clearCookies();
      const raw = [];
      for (const line of fs.readFileSync(JAR, "utf8").split("\n")) {
        if (!line || line.startsWith("#")) continue;
        const p = line.split("\t");
        if (p.length < 7) continue;
        let domain = p[0];
        if (!domain.startsWith(".")) domain = "." + domain.replace(/^\./, "");
        raw.push({
          name: p[5],
          value: p[6],
          domain,
          path: p[2] || "/",
          secure: true,
          url: "https://chinese.i-learner.com.hk/",
        });
      }
      // Prefer url-based addCookies
      await context.addCookies(
        raw.map(({ name, value, path: pt }) => ({
          name,
          value,
          url: "https://chinese.i-learner.com.hk/",
          path: pt || "/",
        }))
      );
      log(`re-injected ${raw.length} cookies via url`);
    }

    const urls = reportUrls();
    await saveViaBrowser(page, urls.prev, path.join(INBOX, "prev.html"), "prev");
    await saveViaBrowser(page, urls.current, path.join(INBOX, "current.html"), "current");
  } finally {
    await browser.close().catch(() => null);
  }
  log("done");
}

main().catch((e) => die(e && e.message ? e.message : String(e)));
