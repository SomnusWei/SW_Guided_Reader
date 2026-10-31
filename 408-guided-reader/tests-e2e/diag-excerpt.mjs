// 诊断：复现「所选内容存为笔记」全链路 + 问AI默认技能（Task：用户反馈 2026-10-31）
// 用法：先起 frozen sidecar（--data-dir D:\LearningTools\Reader），READER_URL 指向它。
// ASK=0 时跳过提问（复用已有会话验证摘录保存）。
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
const artifacts = path.resolve("test-results/diag-excerpt");
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
  await page.waitForTimeout(1200);

  // 0) 问AI面板草稿里的默认技能（不发送，不耗额度）——仅 ASK 模式；ASK=0 时稍后从追问表单读
  let pick = null;
  if (process.env.ASK !== "0") {
    // 上次阅读位置可能是扫描件页（OCR 选字不可用），先跳回第 1 页再选字提问
    await page.locator("#page-number").fill("1");
    await page.locator("#page-number").press("Enter");
    await page.waitForTimeout(2500);
    pick = await page.locator(".page .text-overlay").evaluateAll((nodes) => {
      for (const node of nodes) {
        if (node.textContent.trim().length < 40) continue;
        const box = node.getBoundingClientRect();
        if (box.top < 400 && box.bottom > 600) {
          return { x: box.left + box.width * 0.25, y: Math.max(box.top + 200, 150), w: box.width };
        }
      }
      return null;
    });
    if (!pick) throw new Error("没有与视口相交的 text-overlay");
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
  }
  const skillValue = await page.locator("#assistant-skill").inputValue().catch(() => "(无此控件)");
  const skillText = skillValue === "(无此控件)" ? skillValue
    : await page.locator(`#assistant-skill option[value="${skillValue}"]`).textContent().catch(() => "?");
  console.log(`INFO 默认技能 value=${JSON.stringify(skillValue)} 文本=${JSON.stringify(skillText)}`);
  await page.screenshot({ path: path.join(artifacts, "draft.png") });

  if (process.env.ASK !== "0") {
    // 1) 提问并等回答（真实 provider）
    await page.locator("#assistant-start").click();
    console.log("INFO 已发送问题，等待回答…");
    await page.waitForFunction(() => {
      const bubble = document.querySelector('.assistant-answer-bubble[data-current-answer="true"]');
      const pending = document.querySelector(".assistant-pending");
      return bubble && bubble.textContent.trim().length > 80 && !pending;
    }, { timeout: 180000 });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(artifacts, "answer.png") });
    console.log("INFO 回答已生成");
  } else {
    // 复用最近一次会话：优先等面板自动恢复；否则用「切换解释主题」下拉选回
    const hasBubble = await page.locator('.assistant-answer-bubble[data-current-answer="true"]')
      .waitFor({ timeout: 6000 }).then(() => true).catch(() => false);
    if (!hasBubble) {
      await page.locator("#assistant-toggle").click().catch(() => {});
      await page.waitForTimeout(800);
      const options = await page.locator("#assistant-root-switcher option").count();
      console.log("INFO root-switcher 选项数：", options);
      if (options > 0) {
        await page.locator("#assistant-root-switcher").selectOption({ index: 0 });
        await page.waitForTimeout(2000);
      }
    }
    await page.waitForSelector('.assistant-answer-bubble[data-current-answer="true"]', { timeout: 20000 });
    console.log("INFO 已载入历史会话");
    const skillValue0 = await page.locator("#assistant-skill").inputValue().catch(() => "(无此控件)");
    console.log("INFO 会话内技能选择：", JSON.stringify(skillValue0));
  }

  // 2) 程序化设置选区（跨多个文本段），再派发 contextmenu——面板重渲染会清掉
  //    鼠标拖选的选区（e2e 抖动），程序化选区不受影响；产品链路完整覆盖，
  //    仅「物理右键塌陷容错」不在此覆盖（上一轮已验证）。
  await page.waitForTimeout(300);
  const picked = await page.evaluate(() => {
    const bubble = document.querySelector('.assistant-answer-bubble[data-current-answer="true"]');
    if (!bubble) return null;
    const nodes = Array.from(bubble.querySelectorAll(".assistant-source-text"));
    if (nodes.length < 2) return null;
    const startEl = nodes[0];
    const endEl = nodes[Math.min(nodes.length - 1, 7)];
    const range = document.createRange();
    range.setStartBefore(startEl);
    range.setEndAfter(endEl);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
    if (!rects.length) return null;
    const mid = rects[Math.floor(rects.length / 2)];
    return {
      text: selection.toString().slice(0, 80),
      x: mid.left + mid.width / 2,
      y: mid.top + mid.height / 2,
    };
  });
  if (!picked) throw new Error("程序化选区设置失败");
  console.log("INFO 选区结果：", JSON.stringify(picked.text));
  check(Boolean(picked.text.trim()), "回答选区设置成功");
  await page.evaluate(({ x, y }) => {
    // 监听器绑在 #assistant-turns 上；选区可能整体在视口外（elementFromPoint
    // 会返回 null），所以直接在容器上派发，坐标仅作锚定参考
    const container = document.getElementById("assistant-turns");
    (container || document.body).dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true, cancelable: true, clientX: x, clientY: y,
    }));
  }, { x: picked.x, y: picked.y });
  const diag = await page.evaluate(async () => {
    const mod = await import("/assistant-render.js");
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount !== 1) return { error: "selection collapsed/multi" };
    const range = sel.getRangeAt(0);
    const bubble = document.querySelector('.assistant-answer-bubble[data-current-answer="true"]');
    const mapped = mod.renderedSelectionToRaw(range, bubble);
    return {
      mapped: mapped ? {
        blocked: mapped.blocked, reason: mapped.reason || "",
        text: (mapped.selectedText || "").slice(0, 60),
        spans: (mapped.sourceSpans || []).length,
      } : null,
      rangeText: range.toString().slice(0, 60),
      parts: bubble ? bubble.querySelectorAll(".assistant-source-text").length : -1,
    };
  });
  console.log("INFO 映射诊断：", JSON.stringify(diag));
  // 序列化器确定性检查：对整个回答气泡做反向序列化（不动真实选区），
  // 只要回答本身带结构（标题/粗体/表格/列表），还原结果就必须有 markdown 标记
  const mdDiag = await page.evaluate(async () => {
    const mod = await import("/assistant-render.js");
    const bubble = document.querySelector('.assistant-answer-bubble[data-current-answer="true"]');
    if (!bubble) return { error: "no bubble" };
    const range = document.createRange();
    range.selectNodeContents(bubble);
    const md = mod.selectionToMarkdown(range, bubble) || "";
    return {
      chars: md.length,
      hasHeading: /^#{1,6} \S/m.test(md),
      hasBold: md.includes("**"),
      hasTable: /^\|/m.test(md),
      hasList: /^[-*] \S/m.test(md),
      sample: md.slice(0, 160),
    };
  });
  console.log("INFO 序列化诊断：", JSON.stringify(mdDiag));
  check(!mdDiag.error && (mdDiag.hasHeading || mdDiag.hasBold || mdDiag.hasTable || mdDiag.hasList),
    "selectionToMarkdown 还原出 markdown 结构",
    JSON.stringify({ chars: mdDiag.chars, sample: (mdDiag.sample || "").slice(0, 60) }));
  // 派发 contextmenu 时选区仍在（未经历物理右键塌陷），工具栏应直接弹出
  await page.waitForTimeout(600);
  const afterRight = await page.evaluate(() => {
    const sel = window.getSelection();
    const bubble = document.querySelector('.assistant-answer-bubble[data-current-answer="true"]');
    const rects = sel && sel.rangeCount ? Array.from(sel.getRangeAt(0).getClientRects()) : [];
    return {
      collapsed: sel ? sel.isCollapsed : null,
      rangeCount: sel ? sel.rangeCount : null,
      text: sel ? sel.toString().slice(0, 40) : null,
      bubbleCurrent: bubble?.dataset.currentAnswer || null,
      rectSample: rects.slice(0, 2).map((r) => ({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width) })),
    };
  });
  console.log("INFO 右键后选区状态：", JSON.stringify(afterRight));
  const toast = await page.evaluate(() => Array.from(document.querySelectorAll("[role='status'], .assistant-readiness"))
    .map((n) => n.textContent.trim()).filter(Boolean).slice(0, 4));
  console.log("INFO 右键后提示：", JSON.stringify(toast));
  const toolbarVisible = await page.locator("#assistant-answer-actions").isVisible();
  check(toolbarVisible, "回答选区工具栏弹出");
  if (!toolbarVisible) {
    await page.screenshot({ path: path.join(artifacts, "no-toolbar.png") });
    throw new Error("工具栏未弹出，后续无法保存摘录");
  }
  await page.screenshot({ path: path.join(artifacts, "toolbar.png") });
  await page.locator("#assistant-save-excerpt").click();
  // 等保存完成：按钮进入 saving → announce 或状态变化
  await page.waitForTimeout(2500);
  const saveToast = await page.evaluate(() => {
    const nodes = document.querySelectorAll("[role='status'], .sr-live, .announce");
    return Array.from(nodes).map((n) => n.textContent.trim()).filter(Boolean).slice(0, 3);
  });
  console.log("INFO 保存后提示：", JSON.stringify(saveToast));
  await page.screenshot({ path: path.join(artifacts, "after-save.png") });

  // 3) 点 PDF 页面上的「AI」徽章打开笔记卡片（面板条目点击=跳转，不开卡片），
  //    验证摘录按 markdown 渲染
  await page.waitForTimeout(800);
  const marker = page.locator(".annotation-note-marker").last();
  await marker.waitFor({ timeout: 8000 });
  await marker.click();
  await page.waitForTimeout(1200);
  const cardInfo = await page.evaluate(() => {
    const card = document.querySelector(".annotation-note-card");
    if (!card) return { card: false };
    const answer = card.querySelector(".annotation-note-answer");
    return {
      card: true,
      hasTable: Boolean(answer?.querySelector("table")),
      hasStrong: Boolean(answer?.querySelector("strong")),
      hasList: Boolean(answer?.querySelector("ul, ol")),
      text: (answer?.textContent || "").trim().slice(0, 120),
    };
  });
  console.log("INFO 笔记卡片：", JSON.stringify(cardInfo));
  await page.screenshot({ path: path.join(artifacts, "note-card.png") });
  check(cardInfo.card, "AI 笔记卡片打开");
  if (cardInfo.card) {
    check(cardInfo.hasTable || cardInfo.hasStrong || cardInfo.hasList,
      "卡片内容按 markdown 渲染（出现表格/加粗/列表元素）",
      `table=${cardInfo.hasTable} strong=${cardInfo.hasStrong} list=${cardInfo.hasList}`);
  }
} finally {
  console.log("console errors:", consoleErrors.length ? consoleErrors.slice(0, 8) : "(none)");
  await browser.close();
}
if (failures.length) { console.log("\nFAILURES:\n" + failures.join("\n")); process.exit(1); }
console.log("\nALL PASS");
