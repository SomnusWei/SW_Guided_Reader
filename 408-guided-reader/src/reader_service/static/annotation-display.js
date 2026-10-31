// 批注的展示判定（纯逻辑，无 DOM 依赖）：
// AI 笔记有两种来源——整段解释（AI 解答）与只存选区片段（摘录），
// 面板徽标、正文、导出都需要按 payload.excerpt 区分，不能混为一条。

export const ANNOTATION_KIND_LABELS = Object.freeze({
  HIGHLIGHT: "高亮",
  UNDERLINE: "下划线",
  TEXTBOX: "文本框",
  REGION: "区域",
  NOTE: "便签",
  AI_NOTE: "AI 解答",
});

export const EXCERPT_BADGE_LABEL = "摘录";

export function annotationKind(annotation) {
  if (annotation?.kind) return annotation.kind;
  if (annotation?.source_kind === "AI_SAVED") return "AI_NOTE";
  return Array.isArray(annotation?.rect) ? "REGION" : "HIGHLIGHT";
}

// AI 笔记里只有 payload.excerpt 的才是摘录；老数据没有 payload 时按整段解释处理。
export function isExcerptNote(annotation) {
  return annotationKind(annotation) === "AI_NOTE" && Boolean(annotation?.payload?.excerpt);
}

export function annotationBadgeLabel(annotation) {
  if (isExcerptNote(annotation)) return EXCERPT_BADGE_LABEL;
  const kind = annotationKind(annotation);
  return ANNOTATION_KIND_LABELS[kind] || kind;
}

// 摘录现在按 markdown 入库（保留原回答排版）；面板列表是纯文本预览，
// 直接显示会露出 **、#、| 这些标记。这里做与后端 PDF 写回同思路的轻量剥除，
// 只保证预览可读，不追求逐字还原。
export function excerptPreviewText(markdown) {
  const source = String(markdown ?? "");
  if (!source) return "";
  const lines = [];
  for (const rawLine of source.split("\n")) {
    let line = rawLine.trimEnd();
    if (/^\s*(```|~~~)/.test(line)) continue; // 围栏行剥掉，围栏内代码保留
    if (/^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{0,}:?\s*\|?\s*$/.test(line)) continue;
    if (/^\s*\|.*\|\s*$/.test(line)) {
      // 表格行：取各单元格文字，竖线换成空格
      line = line.trim().replace(/^\|/, "").replace(/\|$/, "")
        .split("|").map((cell) => cell.trim()).filter(Boolean).join("  ");
    } else {
      line = line.replace(/^\s{0,3}#{1,6}\s+/, ""); // 标题标记
      line = line.replace(/^\s*>+\s?/, ""); // 引用标记
      line = line.replace(/^(\s*)(?:[-*+]|\d+[.)])\s+/, "$1· "); // 列表项
    }
    line = line
      .replace(/\$\$?([^$]*)\$\$?/g, "$1")
      .replace(/\*\*\*(.*?)\*\*\*/g, "$1")
      .replace(/\*\*(.*?)\*\*/g, "$1")
      .replace(/__(.*?)__/g, "$1")
      .replace(/\*(.*?)\*/g, "$1")
      .replace(/_(.*?)_/g, "$1")
      .replace(/~~(.*?)~~/g, "$1")
      .replace(/`([^`]*)`/g, "$1")
      .trim();
    if (line) lines.push(line);
  }
  return lines.join("\n");
}

// 列表正文：摘录显示所选片段（剥掉 markdown 标记做纯文本预览），
// 整段解释显示该轮提问，其余显示批注正文或原文引用。
export function annotationBodyText(annotation) {
  if (isExcerptNote(annotation)) return excerptPreviewText(annotation.payload.excerpt);
  if (annotationKind(annotation) === "AI_NOTE") {
    return annotation?.payload?.question || annotation?.quote || annotation?.body || "";
  }
  return annotation?.body || annotation?.quote || "";
}

// 批注所在页码：优先 pdf_page_index，老数据回落 page_index。
export function annotationPageIndex(annotation) {
  return Number.isInteger(annotation?.pdf_page_index) ? annotation.pdf_page_index
    : Number.isInteger(annotation?.page_index) ? annotation.page_index : 0;
}

// 全书批注面板点击跳转用的定位：返回 {page, y}，y 为归一化纵坐标，直接喂给
// goToPage 的 offset。rect 优先；只有 quads 时取最上沿；两者都没有（如部分 AI
// 笔记）就落回页首，避免跳到页面中部让人找不到。
export function annotationJumpTarget(annotation) {
  const page = annotationPageIndex(annotation);
  if (Array.isArray(annotation?.rect) && annotation.rect.length >= 4) {
    return {page, y: Number(annotation.rect[1]) || 0};
  }
  const quads = Array.isArray(annotation?.quads) ? annotation.quads : [];
  let y = Infinity;
  for (const quad of quads) {
    if (!Array.isArray(quad)) continue;
    for (const point of quad) {
      if (Array.isArray(point) && Number.isFinite(Number(point[1]))) {
        y = Math.min(y, Number(point[1]));
      }
    }
  }
  return {page, y: Number.isFinite(y) ? y : 0};
}
