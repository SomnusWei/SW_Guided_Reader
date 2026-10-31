// 修复验证：模拟用户环境（暗色主题 + 150% 缩放）的 PDF 拖选 / 高亮 / 下划线
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
const url = process.env.READER_URL || "http://127.0.0.1:8810/?token=devfix";
const artifacts = path.resolve("test-results/fix-selection");
await mkdir(artifacts, { recursive: true });

const failures = [];
const check = (ok, label, detail = "") => {
  if (!ok) failures.push(`${label}${detail ? ` — ${detail}` : ""}`);
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${detail ? `（${detail}）` : ""}`);
};

const browser = await chromium.launch({ executablePath, headless: process.env.READER_HEADLESS !== "0" });
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(String(e)));

await page.goto(url, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);
// 切暗色主题（与用户环境一致）
await page.evaluate(async () => {
  const token = new URLSearchParams(location.search).get("token");
  await fetch("/api/settings/appearance", {
    method: "PUT", headers: { "Content-Type": "application/json", "X-Reader-Token": token },
    body: JSON.stringify({ theme: "dark" }),
  });
});
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
console.log("theme:", await page.evaluate(() => document.documentElement.dataset.theme));

await page.locator(".book-open").first().click();
await page.waitForSelector("#book-overview:not([hidden])");
await page.locator("#book-overview .overview-book-heading .primary-action").first().click();
await page.waitForSelector("#reader:not([hidden])");
await page.waitForSelector(".page .text-overlay", { timeout: 30000 });
await page.waitForTimeout(1500);

// 调到 150% 缩放（与用户一致）
const zoomInput = page.locator("#zoom-value");
if (await zoomInput.count()) {
  await zoomInput.fill("150%");
  await zoomInput.press("Enter");
  await page.waitForTimeout(2500);
  console.log("zoom set:", await zoomInput.inputValue());
}

// 找与视口相交的 overlay
const n = await page.locator(".page .text-overlay").count();
let pick = null;
for (let i = 0; i < n; i++) {
  const b = await page.locator(".page .text-overlay").nth(i).boundingBox();
  if (b && b.y < 400 && b.y + b.height > 600) { pick = { i, ...b }; break; }
}
if (!pick) { console.log("没有与视口相交的页面"); process.exit(1); }
console.log("pick page:", pick.i, "y=", Math.round(pick.y));

// 视口坐标拖选（避开页顶工具栏区域）
const y = Math.max(pick.y + 220, 120);
const x0 = pick.x + pick.width * 0.2, x1 = pick.x + Math.min(pick.width * 0.8, 1400);
await page.mouse.move(x0, y);
await page.mouse.down();
for (let i = 1; i <= 10; i++) {
  await page.mouse.move(x0 + (x1 - x0) * (i / 10), y);
  await page.waitForTimeout(35);
}
await page.mouse.up();
await page.waitForTimeout(600);

const quads = await page.locator(".selection-quad").count();
check(quads > 0, "拖选后出现 .selection-quad", `count=${quads}`);
const quadInfo = quads > 0 ? await page.evaluate(() => {
  const node = document.querySelector(".selection-quad");
  const cs = getComputedStyle(node); const r = node.getBoundingClientRect();
  return { bg: cs.backgroundColor, w: Math.round(r.width), h: Math.round(r.height), z: cs.zIndex, display: cs.display };
}) : null;
if (quadInfo) console.log("quad:", JSON.stringify(quadInfo));
await page.screenshot({ path: path.join(artifacts, "dark-zoom-after-drag.png") });

const menuVisible = await page.evaluate(() => { const m = document.getElementById("selection-actions"); return m ? !m.hidden : "(missing)"; });
check(menuVisible === true, "松手后选区菜单弹出", String(menuVisible));

if (menuVisible === true) {
  const labels = await page.evaluate(() => [...document.querySelectorAll("#selection-actions button")].map((b) => b.textContent.trim()));
  console.log("menu:", labels.join(" | "));
  for (const name of ["高亮", "下划线"]) {
    const btn = page.locator("#selection-actions button", { hasText: name }).first();
    if (await btn.count()) {
      await btn.click();
      await page.waitForTimeout(1500);
      const marks = await page.evaluate(() => document.querySelectorAll("[data-annotation-id]").length);
      check(marks > 0, `点击「${name}」后生成批注`, `count=${marks}`);
      // 批注标记颜色必须非透明（M5 回归正是这里透明不可见）
      const markBg = await page.evaluate(() => {
        for (const node of document.querySelectorAll("[data-annotation-id]")) {
          const bg = getComputedStyle(node).backgroundColor;
          if (bg && bg !== "rgba(0, 0, 0, 0)") return bg;
        }
        return "rgba(0, 0, 0, 0)";
      });
      check(markBg !== "rgba(0, 0, 0, 0)", "批注标记背景非透明", markBg);
      // 重新拖选为下一步做准备
      await page.mouse.move(x0, y + 60);
      await page.mouse.down();
      for (let i = 1; i <= 10; i++) { await page.mouse.move(x0 + (x1 - x0) * (i / 10), y + 60); await page.waitForTimeout(25); }
      await page.mouse.up();
      await page.waitForTimeout(500);
    }
  }
}
await page.screenshot({ path: path.join(artifacts, "dark-zoom-final.png") });
console.log("console errors:", consoleErrors.length ? consoleErrors.slice(0, 8) : "(none)");
await browser.close();
if (failures.length) { console.log("\nFAILURES:\n" + failures.join("\n")); process.exit(1); }
console.log("\nALL PASS");
