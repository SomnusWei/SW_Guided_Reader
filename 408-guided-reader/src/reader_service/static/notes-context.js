// Reader ↔ 笔记页之间的阅读上下文协议（纯逻辑，无 DOM 依赖）。
// Reader 打开笔记抽屉时把当前教材与页码带进 URL；之后翻页再用 postMessage 增量推送。
// 笔记页只把这些值当作「新建笔记的预填」和列表里的来源标注，不改动已保存的旧笔记。

export const CONTEXT_MESSAGE_TYPE = "reader-context";

function cleanPage(value) {
  if (value === null || value === undefined || value === "") return null;
  const page = typeof value === "number" ? value : Number(value);
  if (!Number.isInteger(page) || page < 0) return null;
  return page;
}

function cleanText(value) {
  if (typeof value !== "string") return "";
  return value.trim();
}

/** 归一到 {revisionId, bookTitle, page}；无有效信息时返回 null。 */
export function normalizeContext(raw) {
  if (!raw || typeof raw !== "object") return null;
  const revisionId = cleanText(raw.revisionId ?? raw.revision_id ?? raw.book);
  const bookTitle = cleanText(raw.bookTitle ?? raw.book_title ?? raw.bookTitleSnapshot);
  const page = cleanPage(raw.page ?? raw.page_index ?? raw.pageIndex);
  if (!revisionId && page === null) return null;
  return { revisionId: revisionId || null, bookTitle, page };
}

/** 从笔记页 URL 查询串里取出 Reader 带进来的上下文。 */
export function readContextFromQuery(search) {
  const query = new URLSearchParams(search || "");
  return normalizeContext({
    revisionId: query.get("book"),
    bookTitle: query.get("bookTitle"),
    page: query.get("page"),
  });
}

/** 新草稿要预填的字段（对应 notes 表列名）。 */
export function contextSeed(context) {
  return {
    book_source_revision_id: context?.revisionId || null,
    page_index: Number.isInteger(context?.page) ? context.page : null,
  };
}

/** 「书名 · 第 N 页」；页码按 1 起显示。 */
export function contextLabel(context) {
  if (!context) return "";
  const parts = [];
  if (context.bookTitle) parts.push(context.bookTitle);
  if (Number.isInteger(context.page)) parts.push(`第 ${context.page + 1} 页`);
  return parts.join(" · ");
}

export function sameContext(a, b) {
  if (!a || !b) return a === b;
  return a.revisionId === b.revisionId && a.page === b.page && a.bookTitle === b.bookTitle;
}

/** 笔记页是否被嵌在 Reader 抽屉里（紧凑顶栏、按钮文案随之调整）。 */
export function isEmbedded(search) {
  const value = new URLSearchParams(search || "").get("embed");
  return value === "1" || value === "true";
}
