// 诊断辞典弹窗布局
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const chromeCandidates = [
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
];
const executablePath = process.env.READER_CHROMIUM || chromeCandidates.find((item) => existsSync(item));
const url = process.env.READER_URL || "http://127.0.0.1:8810/?token=devfix";

const browser = await chromium.launch({ executablePath, headless: process.env.READER_HEADLESS !== "0" });
for (const vw of [700, 820, 900, 1100, 1440]) {
  const context = await browser.newContext({ viewport: { width: vw, height: 760 } });
  const page = await context.newPage();
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(900);
  await page.locator(".book-open").first().click();
  await page.waitForSelector("#book-overview:not([hidden])");
  await page.locator("#book-overview .overview-book-heading .primary-action").first().click();
  await page.waitForSelector("#reader:not([hidden])");
  await page.waitForSelector(".page .text-overlay", { timeout: 30000 });
  await page.waitForTimeout(900);

// 拖选弹出菜单 → 点词典
let pick = null;
const n = await page.locator(".page .text-overlay").count();
for (let i = 0; i < n; i++) {
  const b = await page.locator(".page .text-overlay").nth(i).boundingBox();
  if (b && b.y < 400 && b.y + b.height > 600) { pick = { i, ...b }; break; }
}
const y = Math.max(pick.y + 220, 120);
const x0 = pick.x + pick.width * 0.2, x1 = pick.x + Math.min(pick.width * 0.8, 1400);
await page.mouse.move(x0, y);
await page.mouse.down();
for (let i = 1; i <= 10; i++) { await page.mouse.move(x0 + (x1 - x0) * (i / 10), y); await page.waitForTimeout(30); }
await page.mouse.up();
await page.waitForTimeout(500);
await page.locator("#selection-actions button", { hasText: "词典" }).first().click();
await page.waitForSelector("dialog.plugin-dialog[open]", { timeout: 15000 });
await page.waitForTimeout(2500);

// 等词典 iframe 就绪后输入真实搜索词，再量宽度
const frame = page.frameLocator("#plugin-frame");
await frame.locator("#q").fill("痹");
await page.waitForTimeout(2500);

const info = await page.evaluate(() => {
  const dlg = document.querySelector(".plugin-dialog");
  const frame = document.querySelector("#plugin-frame");
  const doc = frame.contentDocument;
  const side = doc?.querySelector("#side");
  const list = doc?.querySelector("#hitList");
  const out = { dialog: Math.round(dlg?.getBoundingClientRect().width ?? 0) };
  if (side) {
    out.side = Math.round(side.getBoundingClientRect().width);
    out.sidePct = Math.round(100 * out.side / out.dialog);
    out.iframeViewport = doc.defaultView.innerWidth;
    out.sideMinContent = (() => {
      // 逐行找最宽的 min-content
      let max = 0, who = "";
      for (const row of list?.querySelectorAll(".hit") || []) {
        const w = row.scrollWidth;
        if (w > max) { max = w; who = row.textContent.trim().slice(0, 20); }
      }
      return { max, who };
    })();
  }
  return out;
});
console.log(`window=${vw}`, JSON.stringify(info));
await context.close();
}
await browser.close();
