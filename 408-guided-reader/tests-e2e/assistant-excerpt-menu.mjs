// 问 AI 回答选区菜单的冒烟测试：
// 页面能正常启动、右键工具条里「所选内容存为笔记」与「再问一层」都已就位，
// 且带 AI 摘录的数据能让「全书批注」面板显示「摘录」徽标与所选片段。
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
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
const exists = (file) => {
  try { accessSync(file, fsConstants.X_OK); return true; } catch { return false; }
};
const executablePath = process.env.READER_CHROMIUM || chromeCandidates.find(exists);
if (!executablePath) throw new Error("未找到 Chrome/Edge，请设置 READER_CHROMIUM");
const python = process.env.READER_TEST_PYTHON || path.join(".venv", "Scripts", "python.exe");

const dataDir = await mkdtemp(path.join(os.tmpdir(), "assistant-excerpt-e2e-"));
const env = { ...process.env, PYTHONPATH: path.join(process.cwd(), "src") };
const service = spawn(python, ["-m", "reader_service", "--no-open", "--port", "0", "--data-dir", dataDir], {
  cwd: process.cwd(), stdio: ["ignore", "pipe", "pipe"], windowsHide: true, env,
});
let errors = "";
service.stderr.on("data", (chunk) => { errors += chunk.toString(); });

const ready = await new Promise((resolve, reject) => {
  let output = "";
  const timer = setTimeout(() => reject(new Error(`服务未启动 ${errors}`)), 20_000);
  service.once("exit", (code) => reject(new Error(`服务退出 ${code} ${errors}`)));
  service.stdout.on("data", (chunk) => {
    output += chunk.toString();
    const match = output.match(/READY (http:\/\/[^\s]+)/);
    if (match) { clearTimeout(timer); resolve(match[1]); }
  });
});
const origin = new URL(ready).origin;
const token = new URL(ready).searchParams.get("token");

// 通过接口导入一本 1 页的 PDF，再直接落一条"AI 摘录"批注（HTTP 不接受 AI_NOTE）
const pdfPath = path.join(dataDir, "book.pdf");
spawnSync(python, ["-c", `
import sys
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

writer = PdfWriter()
page = writer.add_blank_page(width=612, height=792)
font = DictionaryObject({
    NameObject("/Type"): NameObject("/Font"),
    NameObject("/Subtype"): NameObject("/Type1"),
    NameObject("/BaseFont"): NameObject("/Helvetica"),
})
stream = DecodedStreamObject()
stream.set_data(b"BT /F1 24 Tf 72 700 Td (Page 0 bus transaction) Tj ET")
page[NameObject("/Contents")] = writer._add_object(stream)
page[NameObject("/Resources")] = DictionaryObject({
    NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
})
writer.write(sys.argv[1])
`, pdfPath], { env });

const book = await fetch(`${origin}/api/books`, {
  method: "POST",
  headers: { "X-Reader-Token": token, "Content-Type": "application/pdf", "X-File-Name": "book.pdf" },
  body: (await import("node:fs")).readFileSync(pdfPath),
}).then((response) => response.json());
const revisionId = book.book.active_revision.id;

const seeded = spawnSync(python, ["-c", `
import sys
from pathlib import Path
from reader_service.library import LibraryService
from reader_service.annotation import AnnotationRepository, AnnotationService
from reader_service.foundation import FoundationService, FoundationRepository
from reader_service.storage import ManagedPaths

data_root, revision_id = sys.argv[1], sys.argv[2]
library = LibraryService(ManagedPaths(Path(data_root)))
foundation = FoundationService(library, FoundationRepository(library.database), lambda *a, **k: None)
annotations = AnnotationService(foundation, AnnotationRepository(library.database))
projection = {
    "revision_id": revision_id,
    "pdf_page_index": 0,
    "source": {
        "quads": [[[0.2, 0.3], [0.6, 0.3], [0.6, 0.34], [0.2, 0.34]]],
        "quote": "总线事务",
        "context_before": "",
        "context_after": "",
        "foundation_version": 1,
    },
    "provenance": {
        "root_focus": "总线事务",
        "child_focus": None,
        "concept_path": ["总线事务"],
        "answer_question": "什么是总线事务？",
    },
    "source_grounding": {
        "pdf_page_number": 1,
        "printed_page_label": None,
        "chapter_title": None,
        "section_title": None,
        "bounded_same_page_ocr": "",
    },
    "ai_content": "总线事务由地址、数据与控制信号组成。",
}
annotation, created = annotations.create_ai_saved(
    revision_id,
    save_intent_id="e2e-excerpt-intent",
    projection=projection,
    excerpt="总线事务由地址、数据与控制信号组成。",
)
print(annotation["id"], created)
`, dataDir, revisionId], { env, encoding: "utf8" });
assert.equal(seeded.status, 0, seeded.stderr || "摘录批注写入失败");

let browser;
try {
  browser = await chromium.launch({ executablePath, headless: process.env.READER_HEADLESS !== "0" });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") console.log("[console.error]", message.text());
  });
  await page.goto(`${origin}/?token=${encodeURIComponent(token)}`);
  await page.waitForLoadState("networkidle");

  // 右键工具条结构：再问一层 → 所选内容存为笔记 → 关闭
  const labels = await page.locator("#assistant-answer-actions button").allInnerTexts();
  assert.deepEqual(labels.map((text) => text.trim()), ["再问一层", "所选内容存为笔记", "×"]);
  assert.equal(await page.locator("#assistant-answer-actions").isHidden(), true);

  // 打开这本书（先到总览，再进 PDF 阅读器）
  await page.locator(".book-open").first().click();
  await page.getByRole("button", { name: /继续 PDF|打开 PDF/ }).first().click();
  await page.locator("#reader").waitFor({ state: "visible" });
  await page.locator(".page").first().waitFor({ timeout: 30_000 });
  // 全书批注面板：徽标是「摘录」，正文是所选片段，且能显示跳转页码
  await page.locator("#annotations-toggle").click();
  const item = page.locator('.annotation-item[data-excerpt="true"]');
  await item.waitFor({ timeout: 10_000 });
  assert.equal((await item.locator(".annotation-badge").first().innerText()).trim(), "摘录");
  assert.equal((await item.locator(".annotation-body").innerText()).trim(), "总线事务由地址、数据与控制信号组成。");
  assert.match(await item.locator(".annotation-jump").innerText(), /PDF 第 1 页/);

  assert.deepEqual(pageErrors, [], `页面启动报错：${pageErrors.join(" | ")}`);
  console.log("assistant-excerpt: 全部断言通过");
} finally {
  if (browser) await browser.close();
  service.kill();
  await rm(dataDir, { recursive: true, force: true }).catch(() => {});
}
