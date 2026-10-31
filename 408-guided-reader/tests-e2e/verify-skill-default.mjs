// frozen 真机快速验收：默认技能随设置勾选（不发送，不耗额度）
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const chromeCandidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
];
const executablePath = process.env.READER_CHROMIUM || chromeCandidates.find((item) => existsSync(item));
const url = process.env.READER_URL || "http://127.0.0.1:8902/?token=devfix3";

const browser = await chromium.launch({ executablePath, headless: process.env.READER_HEADLESS !== "0" });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 960 } })).newPage();
try {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  await page.locator(".book-open").first().click();
  await page.waitForSelector("#book-overview:not([hidden])");
  await page.locator("#book-overview .overview-book-heading .primary-action").first().click();
  await page.waitForSelector("#reader:not([hidden])");
  await page.waitForSelector(".page .text-overlay", { timeout: 30000 });
  await page.waitForTimeout(1200);
  const pick = await page.locator(".page .text-overlay").evaluateAll((nodes) => {
    for (const node of nodes) {
      const box = node.getBoundingClientRect();
      if (box.top < 400 && box.bottom > 600) {
        return { x: box.left + box.width * 0.25, y: Math.max(box.top + 200, 150), w: box.width };
      }
    }
    return null;
  });
  if (!pick) throw new Error("没有可拖选的页");
  await page.mouse.move(pick.x, pick.y);
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) {
    await page.mouse.move(pick.x + (pick.w * 0.45) * (i / 8), pick.y);
    await page.waitForTimeout(30);
  }
  await page.mouse.up();
  await page.waitForTimeout(600);
  await page.locator("#selection-actions button#ask-selection").click();
  await page.waitForSelector("#assistant-first-turn:not([hidden])", { timeout: 15000 });
  await page.waitForTimeout(800);
  const value = await page.locator("#assistant-skill").inputValue();
  const options = await page.locator("#assistant-skill option").evaluateAll((nodes) =>
    nodes.map((n) => `${n.getAttribute("value") || "(空)"}=${n.textContent.trim()}`));
  console.log(`SKILL-DEFAULT value=${JSON.stringify(value)}`);
  console.log(`SKILL-OPTIONS ${JSON.stringify(options)}`);
  console.log(value === "yixue-zonghe" ? "PASS 默认技能=随包技能" : "FAIL 默认技能不符");
  process.exitCode = value === "yixue-zonghe" ? 0 : 1;
} finally {
  await browser.close();
}
