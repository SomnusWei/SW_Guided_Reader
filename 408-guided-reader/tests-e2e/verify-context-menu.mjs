// 验证 WebView2 原生右键菜单已在页面层禁用：
// 1) 普通区域派发 contextmenu → defaultPrevented === true（原生黑色菜单不再弹出）
// 2) 输入控件（textarea）→ defaultPrevented === false（保留系统复制/粘贴菜单）
// 3) 书页内右键仍能弹应用的摘录工具栏（由 diag-excerpt 全链路覆盖，这里不重复）
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

let ok = true;
const check = (pass, label) => {
  ok = ok && pass;
  console.log(`${pass ? "PASS" : "FAIL"}  ${label}`);
};

const browser = await chromium.launch({ executablePath, headless: true });
try {
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("body", { timeout: 15000 });

  const results = await page.evaluate(() => {
    const fire = (target) => {
      const event = new MouseEvent("contextmenu", {
        bubbles: true, cancelable: true,
      });
      target.dispatchEvent(event);
      return event.defaultPrevented;
    };
    const area = document.createElement("textarea");
    document.body.append(area);
    const editable = fire(area);
    area.remove();
    return { plain: fire(document.body), editable };
  });

  check(results.plain === true, "空白/普通区域 contextmenu 被拦截（禁用原生菜单）");
  check(results.editable === false, "textarea 上 contextmenu 不拦截（保留系统菜单）");
} finally {
  await browser.close();
}

if (!ok) process.exit(1);
console.log("ALL PASS");
