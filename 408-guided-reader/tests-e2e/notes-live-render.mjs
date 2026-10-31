// 笔记块编辑器"回车即渲染"回归测试：
// 段落按 Enter 立即渲染并开新块；列表/引用/代码围栏内回车照常换行；
// Esc 提交后整篇渲染且正文正确入库。
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { accessSync, constants as fsConstants } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright-core";

const chromeCandidates = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
];
function requireExists(file) {
  try { accessSync(file, fsConstants.X_OK); return true; } catch { return false; }
}
const executablePath = process.env.READER_CHROMIUM || chromeCandidates.find(requireExists);
if (!executablePath) throw new Error("未找到 Chrome/Edge，请设置 READER_CHROMIUM");

const dataDir = await mkdtemp(path.join(os.tmpdir(), "notes-live-e2e-"));
const service = spawn(process.env.READER_TEST_PYTHON || "python", ["-m", "reader_service", "--no-open", "--port", "0", "--data-dir", dataDir], {
  cwd: process.cwd(), stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
});
let serviceErrors = "";
service.stderr.on("data", (chunk) => { serviceErrors += chunk.toString(); });

let browser;
try {
  const base = await new Promise((resolve, reject) => {
    let output = "";
    const timeout = setTimeout(() => reject(new Error(`服务未启动 ${serviceErrors}`)), 15_000);
    service.once("exit", (code) => reject(new Error(`服务退出 ${code} ${serviceErrors}`)));
    service.stdout.on("data", (chunk) => {
      output += chunk.toString();
      const match = output.match(/READY (http:\/\/[^\s]+)/);
      if (match) { clearTimeout(timeout); resolve(match[1]); }
    });
  });
  const token = new URL(base).searchParams.get("token");
  browser = await chromium.launch({ executablePath, headless: process.env.READER_HEADLESS !== "0" });
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  page.on("pageerror", (error) => console.log("[pageerror]", error.message));
  page.on("console", (message) => {
    if (message.type() === "error") console.log("[console.error]", message.text());
  });
  const origin = new URL(base).origin;
  await page.goto(`${origin}/notes.html?token=${encodeURIComponent(token)}`);

  // 新建笔记：直接进入第一个块的源码编辑
  await page.waitForLoadState("networkidle");
  await page.locator("#new-note").click();
  const editor = page.locator("#note-text");
  await editor.waitFor({ state: "visible" });

  // 1) 标题段：输完按 Enter 立即渲染为 h1，且新块进入编辑
  await editor.pressSequentially("# 标题一");
  await editor.press("Enter");
  await page.locator('.block-view[data-block="0"] h1', { hasText: "标题一" }).waitFor({ timeout: 3000 });
  const editorAfterHeading = page.locator("#note-text");
  assert.equal(await editorAfterHeading.count(), 1, "Enter 后应只有一个 textarea（新块）");
  assert.equal(await editorAfterHeading.inputValue(), "", "新块应为空");

  // 2) 普通段落含行内语法：Enter 后渲染出 <strong>
  await editorAfterHeading.pressSequentially("这是**加粗**内容");
  await editorAfterHeading.press("Enter");
  await page.locator('.block-view[data-block="1"] strong', { hasText: "加粗" }).waitFor({ timeout: 3000 });

  // 3) 列表行：Enter 应在块内换行继续，不提交渲染
  await editorAfterHeading.pressSequentially("- 项目一");
  await editorAfterHeading.press("Enter");
  await page.waitForTimeout(300);
  assert.equal(await page.locator("#note-text").inputValue(), "- 项目一\n", "列表行 Enter 应只换行");
  await page.locator("#note-text").pressSequentially("- 项目二");
  await page.locator("#note-text").press("Enter"); // 列表行 → 块内换行
  await page.locator("#note-text").press("Enter"); // 空行 → 提交整块渲染
  await page.locator('.block-view[data-block="2"] li').nth(1).waitFor({ timeout: 3000 });

  // 4) 代码围栏：围栏内回车照常换行
  await page.locator("#note-text").pressSequentially("```js");
  await page.locator("#note-text").press("Enter");
  await page.locator("#note-text").pressSequentially("const a = 1;");
  await page.locator("#note-text").press("Enter");
  await page.waitForTimeout(200);
  assert.equal(await page.locator("#note-text").inputValue(), "```js\nconst a = 1;\n", "围栏内 Enter 应只换行");

  // 5) Esc 提交：整篇渲染，围栏代码块渲染成 pre/code
  await page.locator("#note-text").pressSequentially("const b = 2;");
  await page.locator("#note-text").press("Enter"); // 未闭合围栏内 → 换行
  await page.locator("#note-text").pressSequentially("```");
  await page.locator("#note-text").press("Escape");
  await page.locator('.block-view[data-block="3"] pre code').waitFor({ timeout: 3000 });

  // 6) 点击已渲染块 → 回到源码编辑
  await page.locator('.block-view[data-block="1"]').click();
  assert.equal(await page.locator("#note-text").inputValue(), "这是**加粗**内容");

  // 7) 正文入库校验（点保存触发确定性写入）
  await page.locator("#save-note").click();
  await page.waitForFunction(() => /已保存 \d{2}:\d{2}/.test(document.querySelector("#status")?.textContent || ""));
  const detail = await page.evaluate(async () => {
    const list = await (await fetch("/api/notes")).json();
    const id = list.notes[0].id;
    return (await fetch(`/api/notes/${id}`)).json();
  });
  assert.equal(
    detail.note.body,
    "# 标题一\n\n这是**加粗**内容\n\n- 项目一\n- 项目二\n\n```js\nconst a = 1;\nconst b = 2;\n```",
    "入库正文应与块结构一致",
  );

  console.log("notes-live-render: 全部断言通过");
} finally {
  if (browser) await browser.close();
  service.kill();
  await rm(dataDir, { recursive: true, force: true }).catch(() => {});
}
