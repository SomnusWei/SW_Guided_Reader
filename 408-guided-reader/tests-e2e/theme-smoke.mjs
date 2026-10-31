// M5 验收冒烟：主题令牌化 + 6 标签设置 + 插件/SKILL 面板在真实浏览器里能不能跑。
//
// 只依赖 playwright-core + 本机 Chromium，不需要 AI 服务：
//   node tests-e2e/theme-smoke.mjs
// 环境变量：READER_URL（默认 http://127.0.0.1:8802/?token=m5dev）
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const chromeCandidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
];
const executablePath = process.env.READER_CHROMIUM || chromeCandidates.find((item) => existsSync(item));
if (!executablePath) throw new Error("需要 Chrome 或 Edge（可用 READER_CHROMIUM 指定）");
const url = process.env.READER_URL || "http://127.0.0.1:8802/?token=m5dev";
const artifacts = path.resolve("test-results");
await mkdir(artifacts, { recursive: true });

async function openBook(page) {
  await page.locator(".book-open").first().click();
  await page.waitForSelector("#book-overview:not([hidden])", { timeout: 20000 });
  await page.locator("#book-overview .overview-book-heading .primary-action").first().click();
  await page.waitForSelector("#reader:not([hidden])", { timeout: 30000 });
  await page.waitForTimeout(1200);
}

const failures = [];
const check = (ok, label, detail = "") => {
  if (!ok) failures.push(`${label}${detail ? ` — ${detail}` : ""}`);
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${detail ? `（${detail}）` : ""}`);
};

const browser = await chromium.launch({ executablePath, headless: process.env.READER_HEADLESS !== "0" });
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));

try {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".book-card", { timeout: 20000 }).catch(() => {});

  // ---- 首屏：注入的偏好与 theme.css 都在 ----
  const injected = await page.evaluate(() => ({
    pref: document.documentElement.getAttribute("data-theme-pref"),
    theme: document.documentElement.getAttribute("data-theme"),
    hasThemeCss: [...document.styleSheets].some((sheet) => (sheet.href || "").endsWith("/theme.css")),
    tokenCount: getComputedStyle(document.documentElement).getPropertyValue("--t-f6f5ee").trim(),
  }));
  check(injected.hasThemeCss, "theme.css 已加载");
  check(Boolean(injected.pref), "html 上注入了 data-theme-pref", String(injected.pref));
  check(injected.tokenCount !== "", "令牌 --t-f6f5ee 有值", injected.tokenCount);

  // ---- 打开一本书 ----
  await openBook(page);

  // ---- 设置：6 个标签 ----
  await page.click("#reader-settings");
  await page.waitForSelector("#settings-dialog[open]", { timeout: 10000 });
  await page.waitForTimeout(400);
  const tabLabels = await page.$$eval(".settings-tab", (nodes) => nodes.map((node) => node.textContent.trim()));
  check(tabLabels.length === 6, "设置里有 6 个标签", tabLabels.join(" / "));

  const tabReports = [];
  for (const label of tabLabels) {
    const button = page.locator(".settings-tab", { hasText: label }).first();
    await button.click();
    await page.waitForTimeout(650);
    const report = await page.evaluate(() => {
      const panel = [...document.querySelectorAll(".settings-panel")].find((node) => !node.hidden);
      if (!panel) return { id: null, text: 0, problems: ["没有可见面板"] };
      const text = (panel.textContent || "").replace(/\s+/g, " ").trim();
      const problems = [];
      if (!text) problems.push("面板是空的");
      if (/undefined|NaN|\[object Object\]/.test(text)) problems.push("出现 undefined/NaN/[object Object]");
      return { id: panel.dataset.tab, text: text.length, problems, head: text.slice(0, 60) };
    });
    tabReports.push({ label, ...report });
    check(report.problems.length === 0, `标签「${label}」渲染正常`, report.head);
  }

  // ---- 关于页要读到真实版本 ----
  await page.locator(".settings-tab", { hasText: "关于" }).first().click();
  await page.waitForTimeout(500);
  const about = await page.evaluate(() => {
    const dl = document.querySelector(".settings-about");
    return dl ? dl.textContent.replace(/\s+/g, " ").trim().slice(0, 160) : "";
  });
  check(/版本/.test(about) && !/—\s*—/.test(about), "关于页读到了版本信息", about);

  // ---- 深色主题 ----
  await page.locator(".settings-tab", { hasText: "通用" }).first().click();
  await page.waitForTimeout(300);
  // 先回到浅色，避免上一轮留下的深色让 before === after
  await page.click('input[name="theme"][value="light"]');
  await page.waitForTimeout(700);
  const before = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return { bg: style.backgroundColor, color: style.color, theme: document.documentElement.getAttribute("data-theme") };
  });
  await page.click('input[name="theme"][value="dark"]');
  await page.waitForTimeout(900);
  const after = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    const reader = document.querySelector("#reader");
    return {
      bg: style.backgroundColor,
      color: style.color,
      theme: document.documentElement.getAttribute("data-theme"),
      pref: document.documentElement.getAttribute("data-theme-pref"),
      stored: localStorage.getItem("lt-theme"),
      readerBg: reader ? getComputedStyle(reader).backgroundColor : "",
    };
  });
  const luminance = (rgb) => {
    const parts = String(rgb).match(/\d+/g);
    if (!parts) return null;
    const [r, g, b] = parts.slice(0, 3).map(Number);
    return 0.299 * r + 0.587 * g + 0.114 * b;
  };
  check(after.theme === "dark" && after.pref === "dark", "点击深色后 html 属性已切换", `${after.theme}/${after.pref}`);
  check(after.stored === "dark", "偏好镜像进了 localStorage", String(after.stored));
  check(luminance(after.bg) < luminance(before.bg), "背景确实变暗", `${before.bg} → ${after.bg}`);
  check(luminance(after.color) > luminance(after.bg), "深色下文字比背景亮（可读性）", `${after.color} on ${after.bg}`);

  await page.screenshot({ path: path.join(artifacts, "m5-settings-dark.png") });

  // 刷新一次：服务端注入应让首屏直接是深色，不该先白再黑
  await page.goto(url, { waitUntil: "domcontentloaded" });
  const firstPaintTheme = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  check(firstPaintTheme === "dark", "刷新后首屏直接是深色（无白闪）", String(firstPaintTheme));
  await page.screenshot({ path: path.join(artifacts, "m5-home-dark.png") });

  // ---- 插件/SKILL 面板内容 ----
  await page.waitForSelector(".book-card", { timeout: 20000 }).catch(() => {});
  await openBook(page);
  await page.click("#reader-settings");
  await page.waitForSelector("#settings-dialog[open]");
  await page.locator(".settings-tab", { hasText: "插件" }).first().click();
  await page.waitForTimeout(900);
  const plugins = await page.evaluate(() => {
    const rows = [...document.querySelectorAll(".plugin-row")];
    return rows.map((row) => ({
      id: row.dataset.pluginId,
      text: row.textContent.replace(/\s+/g, " ").trim().slice(0, 80),
    }));
  });
  check(plugins.length >= 2, "插件管理列出了随包插件", plugins.map((item) => item.id).join(", "));
  const hasOpen = await page.evaluate(() =>
    [...document.querySelectorAll(".plugin-row button")].some((b) => b.textContent === "打开"));
  check(hasOpen, "window 型插件有「打开」入口");

  // 从设置里打开辞典，并确认插件页跟上深色主题
  await page.locator('.plugin-row[data-plugin-id="dict"] button', { hasText: "打开" }).click();
  await page.waitForSelector("#plugin-dialog[open]", { timeout: 10000 });
  await page.waitForTimeout(1500);
  const frameTheme = await page.evaluate(() => {
    const frame = document.querySelector("#plugin-frame");
    return frame?.contentDocument?.documentElement?.getAttribute("data-theme") || "";
  });
  check(frameTheme === "dark", "插件页跟随深色主题", String(frameTheme));
  await page.screenshot({ path: path.join(artifacts, "m5-plugin-dark.png") });
  await page.evaluate(() => window.pluginHost.close());
  await page.screenshot({ path: path.join(artifacts, "m5-settings-plugins.png") });

  await page.locator(".settings-tab", { hasText: "SKILL" }).first().click();
  await page.waitForTimeout(900);
  const skills = await page.evaluate(() => {
    const rows = [...document.querySelectorAll(".skills-list .skill-row")];
    return {
      count: rows.length,
      names: rows.map((row) => row.dataset.skillName),
      hasLibrary: Boolean(document.querySelector(".skill-library")),
      hasSize: /KB|MB|B\b/.test(document.querySelector(".skills-list")?.textContent || ""),
    };
  });
  check(skills.count >= 1, "SKILL 面板列出了技能", skills.names.join(", "));
  check(skills.hasLibrary, "随包技能显示了资料库挂载状态");
  await page.screenshot({ path: path.join(artifacts, "m5-settings-skills.png") });

  // 插件在深色下的选区动作是否还活着
  await page.click("#settings-close");
  const actions = await page.evaluate(() => ({
    count: window.pluginHost ? window.pluginHost.actions.length : -1,
    sessions: window.pluginHost ? window.pluginHost.plugins.length : -1,
  }));
  check(actions.count >= 2, "选区动作已注册到宿主", `${actions.count} 个动作 / ${actions.sessions} 个插件`);

  check(consoleErrors.length === 0, "控制台零报错", consoleErrors.slice(0, 3).join(" | "));
} finally {
  await browser.close();
}

console.log(failures.length ? `\n${failures.length} 项未通过` : "\n全部通过");
process.exit(failures.length ? 1 : 0);
