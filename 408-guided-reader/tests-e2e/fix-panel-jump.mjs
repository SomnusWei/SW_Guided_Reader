// 修复验证：全书批注面板整条内容点击跳转到所在位置（Task #46）
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
const artifacts = path.resolve("test-results/fix-panel-jump");
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

try {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  await page.locator(".book-open").first().click();
  await page.waitForSelector("#book-overview:not([hidden])");
  await page.locator("#book-overview .overview-book-heading .primary-action").first().click();
  await page.waitForSelector("#reader:not([hidden])");
  await page.waitForSelector(".page .text-overlay", { timeout: 30000 });
  await page.waitForTimeout(1500);

  // 1) 在当前视口内的页拖选并保存一条高亮（打开书会恢复上次阅读位置，不假设是第 1 页）
  const startPage = await page.locator("#page-number").inputValue();
  const overlayCount = await page.locator(".page .text-overlay").count();
  let pick = null;
  for (let i = 0; i < overlayCount; i += 1) {
    const box = await page.locator(".page .text-overlay").nth(i).boundingBox();
    if (box && box.y < 400 && box.y + box.height > 600) { pick = box; break; }
  }
  if (!pick) throw new Error(`第 ${startPage} 页附近没有与视口相交的 text-overlay，无法拖选`);
  const b = pick;
  const y = Math.max(b.y + 200, 150);
  const x0 = b.x + b.width * 0.2, x1 = b.x + b.width * 0.7;
  await page.mouse.move(x0, y);
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) { await page.mouse.move(x0 + (x1 - x0) * (i / 8), y); await page.waitForTimeout(30); }
  await page.mouse.up();
  await page.waitForTimeout(600);
  await page.locator("#selection-actions button", { hasText: "高亮" }).first().click();
  await page.waitForTimeout(1200);

  // 2) 打开全书批注面板，选第一条，从它的按钮标签解析目标页（不硬编码页码）
  await page.locator("#annotations-toggle").click();
  await page.waitForSelector("#annotations-panel:not([hidden])");
  await page.waitForTimeout(1000);
  const items = await page.locator("#annotations-list .annotation-item").count();
  check(items > 0, "批注面板有条目", `count=${items}`);
  const target = page.locator("#annotations-list .annotation-item").first();
  const annotationId = await target.getAttribute("data-annotation-id");
  const labelText = await target.locator("button.annotation-jump").textContent();
  const targetPage = Number((labelText || "").match(/第 (\d+) 页/)?.[1]);
  check(Number.isInteger(targetPage) && targetPage > 0, "条目标签解析出目标页", `${labelText} → 第 ${targetPage} 页（id=${annotationId}）`);

  const jumpAndAssert = async (how) => {
    // 先翻到离目标页较远的一页，再触发跳转
    const away = targetPage >= 10 ? 3 : Math.min(10, targetPage + 5);
    const pageInput = page.locator("#page-number");
    await pageInput.fill(String(away));
    await pageInput.press("Enter");
    await page.waitForTimeout(1000);
    const at = await pageInput.inputValue();
    check(at === String(away), `${how}：先翻到第 ${away} 页`, at);
    if (how === "点击条目正文") await target.locator(".annotation-body").click();
    else if (how === "点击跳转按钮") await target.locator("button.annotation-jump").click();
    else { await target.focus(); await page.keyboard.press("Enter"); }
    await page.waitForTimeout(1200);
    // 验收标准：目标批注标记真实出现在视口内（页码可能因批注在页底而回算到下一页，±1 容差）
    const after = await pageInput.inputValue();
    check(Math.abs(Number(after) - targetPage) <= 1, `${how}：页码落在目标页附近`, `目标=${targetPage} 当前=${after}`);
    let visible = false;
    for (let i = 0; i < 10 && !visible; i += 1) {
      visible = await page.evaluate((id) => {
        const node = document.querySelector(`[data-annotation-id="${id}"]`);
        if (!node) return false;
        const r = node.getBoundingClientRect();
        return r.bottom > 0 && r.top < innerHeight && r.width > 0;
      }, annotationId);
      if (!visible) await page.waitForTimeout(250);
    }
    check(visible, `${how}：目标批注标记进入视口`);
    if (how === "点击条目正文") {
      let flashed = false;
      for (let i = 0; i < 8 && !flashed; i += 1) {
        flashed = await page.evaluate(() => Boolean(document.querySelector(".annotation-flash")));
        if (!flashed) await page.waitForTimeout(250);
      }
      check(flashed, "跳转后对应标记闪烁（annotation-flash）");
    }
  };

  await jumpAndAssert("点击条目正文");
  await jumpAndAssert("点击跳转按钮");
  await jumpAndAssert("键盘 Enter");

  await page.screenshot({ path: path.join(artifacts, "after-jump.png") });
} finally {
  console.log("console errors:", consoleErrors.length ? consoleErrors.slice(0, 8) : "(none)");
  await browser.close();
}
if (failures.length) { console.log("\nFAILURES:\n" + failures.join("\n")); process.exit(1); }
console.log("\nALL PASS");
