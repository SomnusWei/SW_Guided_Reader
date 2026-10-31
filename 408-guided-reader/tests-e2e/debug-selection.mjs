// 诊断：谁拦截了 text-overlay 的指针事件
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const chromeCandidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
];
const executablePath = process.env.READER_CHROMIUM || chromeCandidates.find((item) => existsSync(item));
const url = process.env.READER_URL || "http://127.0.0.1:8810/?token=devfix";

const browser = await chromium.launch({ executablePath, headless: process.env.READER_HEADLESS !== "0" });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 960 } })).newPage();
page.on("console", (m) => { if (m.type() === "error") console.log("console-error:", m.text()); });

await page.goto(url, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
await page.locator(".book-open").first().click();
await page.waitForSelector("#book-overview:not([hidden])");
await page.locator("#book-overview .overview-book-heading .primary-action").first().click();
await page.waitForSelector("#reader:not([hidden])");
await page.waitForSelector(".page .text-overlay", { timeout: 30000 });
await page.waitForTimeout(1200);

// 列出所有 overlay 的视口位置，挑一个可见的
const boxes = [];
const n = await page.locator(".page .text-overlay").count();
for (let i = 0; i < n; i++) {
  const b = await page.locator(".page .text-overlay").nth(i).boundingBox();
  boxes.push({ i, ...b });
}
console.log("overlays:", JSON.stringify(boxes.slice(0, 6), null, 1));
let pick = boxes.find((b) => b.y < 480 && b.y + b.height > 480);
if (!pick) { console.log("no overlay intersects viewport center"); process.exit(1); }
console.log("pick:", JSON.stringify(pick));

// 计数可见 overlay 收到的事件 + 元素堆栈
const probe = await page.evaluate(({ idx }) => {
  window.__ev = { down: 0, move: 0, up: 0 };
  const ov = document.querySelectorAll(".page .text-overlay")[idx];
  ov.addEventListener("pointerdown", () => window.__ev.down++);
  ov.addEventListener("pointermove", () => window.__ev.move++);
  ov.addEventListener("pointerup", () => window.__ev.up++);
  const rect = ov.getBoundingClientRect();
  const cy = Math.min(Math.max(rect.top + rect.height * 0.12, 4), innerHeight - 4);
  const cx = rect.left + rect.width * 0.4;
  const stack = document.elementsFromPoint(cx, cy).map((el) => {
    const cs = getComputedStyle(el);
    return `${el.tagName.toLowerCase()}${el.className ? "." + String(el.className).split(" ").join(".") : ""} z=${cs.zIndex} pe=${cs.pointerEvents}`;
  });
  return { stack: stack.slice(0, 8) };
}, { idx: pick.i });
console.log(JSON.stringify(probe, null, 1));

const box = { x: pick.x, y: Math.max(pick.y, 0), width: pick.width, height: pick.height };
await page.mouse.move(box.x + box.width * 0.2, Math.min(box.y + box.height * 0.12, 500));
await page.mouse.down();
for (let i = 1; i <= 8; i++) {
  await page.mouse.move(box.x + box.width * (0.2 + i * 0.06), Math.min(box.y + box.height * 0.12, 500));
  await page.waitForTimeout(30);
}
await page.mouse.up();
await page.waitForTimeout(400);
console.log("events:", JSON.stringify(await page.evaluate(() => window.__ev)));
console.log("quads:", await page.locator(".selection-quad").count());
console.log("state.selection:", await page.evaluate(() => JSON.stringify(window.state?.selection ?? "(state not exposed)")));
await browser.close();
