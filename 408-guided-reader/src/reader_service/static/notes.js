// Markdown 笔记独立小窗。与 Reader 同源：优先用 launch cookie（HttpOnly），
// 同时接受 URL 上的 ?token=（Rust 开窗时带上），双保险。
import { renderAssistantAnswer } from "/assistant-render.js";
import { filenameFromDisposition } from "/export-md.js";
import { continuesOnEnter, insideFence, joinBlocks, parseBlocks } from "/notes-blocks.js";
import {
  CONTEXT_MESSAGE_TYPE,
  contextLabel,
  contextSeed,
  isEmbedded,
  normalizeContext,
  readContextFromQuery,
  sameContext,
} from "/notes-context.js";

const TOKEN_KEY = "reader-token";
const AUTOSAVE_DELAY = 1500;
const NARROW = window.matchMedia("(max-width: 719px)");

const query = new URLSearchParams(location.search);
let token = query.get("token") || sessionStorage.getItem(TOKEN_KEY) || "";
if (token) sessionStorage.setItem(TOKEN_KEY, token);
if (query.has("token")) {
  query.delete("token");
  const rest = query.toString();
  history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : ""));
}

// 嵌在 Reader 抽屉里（embed=1）：顶栏只留操作，来源由外层标题栏说明
const EMBED = isEmbedded(location.search);
if (EMBED) document.body.dataset.embed = "1";
// Reader 带进来的阅读位置：新建笔记时预填教材与页码
let readerContext = readContextFromQuery(location.search);

const el = {
  banner: document.getElementById("banner"),
  search: document.getElementById("search"),
  list: document.getElementById("note-list"),
  listEmpty: document.getElementById("list-empty"),
  editorEmpty: document.getElementById("editor-empty"),
  editor: document.getElementById("editor"),
  title: document.getElementById("note-title"),
  live: document.getElementById("note-live"),
  book: document.getElementById("note-book"),
  page: document.getElementById("note-page"),
  pinned: document.getElementById("note-pinned"),
  status: document.getElementById("status"),
  save: document.getElementById("save-note"),
  del: document.getElementById("delete-note"),
  exportOne: document.getElementById("export-note"),
  exportAll: document.getElementById("export-all"),
  newNote: document.getElementById("new-note"),
  back: document.getElementById("back-to-list"),
  headerHint: document.getElementById("header-hint"),
  usePage: document.getElementById("use-reading-page"),
};

const state = {
  notes: [],
  books: [],
  current: null,      // 服务端笔记对象；id 为 null 表示尚未保存的新草稿
  dirty: false,
  saving: null,
  timer: null,
  searchTimer: null,
  seq: 0,
  unauthorized: false,
  autoBook: null,     // 上一次由 Reader 自动带入的教材，用户手动改过就不再覆盖
};

/* ------------------------------ 基础工具 ------------------------------ */

function setBanner(message, kind = "") {
  if (!message) {
    el.banner.hidden = true;
    el.banner.textContent = "";
    return;
  }
  el.banner.hidden = false;
  el.banner.textContent = message;
  el.banner.className = `banner${kind ? ` ${kind}` : ""}`;
}

function setStatus(text, kind = "") {
  el.status.textContent = text;
  el.status.className = `status${kind ? ` ${kind}` : ""}`;
}

function hhmm(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function dayLabel(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  const midnight = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const at = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const days = Math.round((midnight - at) / 86400000);
  if (days <= 0) return "今天";
  if (days === 1) return "昨天";
  if (days < 7) return "本周";
  return "更早";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: {
      "X-Reader-Token": token,
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  if (response.status === 204) return null;
  let data = null;
  try {
    data = await response.json();
  } catch (_error) {
    data = null;
  }
  if (response.status === 401) {
    state.unauthorized = true;
    setBanner("鉴权失败：请从平台侧栏的「笔记」重新打开这个窗口。");
    throw new Error("未授权");
  }
  if (!response.ok) {
    throw new Error((data && data.error) || `请求失败（${response.status}）`);
  }
  return data;
}

/* ------------------------------ 列表 ------------------------------ */

function groupNotes(notes) {
  const groups = [];
  const index = new Map();
  const push = (label) => {
    if (!index.has(label)) {
      index.set(label, []);
      groups.push({ label, notes: index.get(label) });
    }
    return index.get(label);
  };
  for (const note of notes) {
    if (note.pinned) {
      push("置顶").push(note);
      continue;
    }
    push(dayLabel(note.updated_at)).push(note);
  }
  return groups;
}

function noteMetaText(note) {
  const parts = [];
  parts.push(dayLabel(note.updated_at) === "今天" ? hhmm(note.updated_at) : `${new Date(note.updated_at).getMonth() + 1}-${String(new Date(note.updated_at).getDate()).padStart(2, "0")}`);
  if (note.book_title_snapshot) parts.push(note.book_title_snapshot);
  if (Number.isInteger(note.page_index)) parts.push(`第 ${note.page_index + 1} 页`);
  return parts.join(" · ");
}

function renderList() {
  el.list.replaceChildren();
  const notes = state.notes;
  el.listEmpty.hidden = notes.length > 0;
  for (const group of groupNotes(notes)) {
    const heading = document.createElement("div");
    heading.className = "list-group";
    heading.textContent = group.label;
    el.list.append(heading);
    for (const note of group.notes) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "note-row";
      row.dataset.id = note.id;
      if (state.current && state.current.id === note.id) row.classList.add("on");

      const title = document.createElement("span");
      title.className = "note-row-title";
      if (note.pinned) {
        const pin = document.createElement("span");
        pin.className = "pin";
        pin.textContent = "★";
        title.append(pin);
      }
      title.append(document.createTextNode(note.title || "未命名笔记"));

      const excerpt = document.createElement("span");
      excerpt.className = "note-row-excerpt";
      excerpt.textContent = note.excerpt || "（空）";

      const meta = document.createElement("span");
      meta.className = "note-row-meta";
      meta.textContent = noteMetaText(note);

      row.append(title, excerpt, meta);
      row.addEventListener("click", () => selectNote(note.id));
      el.list.append(row);
    }
  }
}

async function loadList({ keepSelection = true } = {}) {
  if (state.unauthorized) return;
  const keyword = el.search.value.trim();
  const url = keyword ? `/api/notes?query=${encodeURIComponent(keyword)}` : "/api/notes";
  try {
    const data = await api(url);
    state.notes = data.notes || [];
    setBanner("");
    renderList();
    if (keepSelection && state.current && !state.current.id && state.notes.length === 0) {
      el.headerHint.textContent = "Markdown 笔记本 · 本地保存";
    }
  } catch (error) {
    if (!state.unauthorized) setBanner(String(error.message || error));
  }
}

/* ------------------------------ 教材下拉 ------------------------------ */

function ensureBookOption(revisionId, label) {
  if (!revisionId) return;
  const exists = Array.from(el.book.options).some((option) => option.value === revisionId);
  if (exists) return;
  const option = document.createElement("option");
  option.value = revisionId;
  option.textContent = label || "（已移除的教材）";
  el.book.append(option);
}

async function loadBooks() {
  if (state.books.length) return;
  try {
    const data = await api("/api/books");
    state.books = (data.books || [])
      .filter((book) => book.status === "ACTIVE" && book.active_revision)
      .map((book) => ({ id: book.active_revision.id, title: book.title }));
    for (const book of state.books) ensureBookOption(book.id, book.title);
  } catch (_error) {
    // 教材列表只影响「关联教材」下拉，失败不阻断笔记本身
  }
}

/* ------------------------------ 编辑器 ------------------------------ */

function blankNote(seed = null) {
  const note = {
    id: null,
    title: "",
    body: "",
    excerpt: "",
    book_source_revision_id: null,
    page_index: null,
    book_title_snapshot: null,
    pinned: false,
    created_at: null,
    updated_at: null,
  };
  if (seed?.book_source_revision_id) {
    note.book_source_revision_id = seed.book_source_revision_id;
    note.book_title_snapshot = readerContext?.revisionId === seed.book_source_revision_id
      ? readerContext.bookTitle || null
      : null;
  }
  if (Number.isInteger(seed?.page_index)) note.page_index = seed.page_index;
  return note;
}

function collect() {
  const pageText = el.page.value.trim();
  const page = pageText === "" ? null : Math.max(0, parseInt(pageText, 10) - 1);
  return {
    title: el.title.value,
    body: bodyText(),
    book_source_revision_id: el.book.value || null,
    page_index: Number.isInteger(page) && page >= 0 ? page : null,
    pinned: el.pinned.checked,
  };
}

function applyNote(note) {
  state.current = note;
  el.title.value = note.title || "";
  loadLive(note.body || "", { autoFocus: !note.id });
  if (note.book_source_revision_id) {
    ensureBookOption(note.book_source_revision_id, note.book_title_snapshot);
  }
  el.book.value = note.book_source_revision_id || "";
  el.page.value = Number.isInteger(note.page_index) ? String(note.page_index + 1) : "";
  el.pinned.checked = Boolean(note.pinned);
  el.editor.hidden = false;
  el.editorEmpty.hidden = true;
  state.dirty = false;
  el.del.disabled = !note.id;
  el.exportOne.disabled = !note.id;
  const seeded = !note.id && Number.isInteger(note.page_index);
  const seedLabel = seeded
    ? contextLabel({ bookTitle: note.book_title_snapshot, page: note.page_index })
    : "";
  setStatus(
    note.id
      ? `已保存 ${hhmm(note.updated_at)}`
      : (seeded ? `新笔记 · 已带入 ${seedLabel}` : "新笔记 · 输入内容后自动保存"),
    "",
  );
  if (NARROW.matches) showView("editor");
  if (note.id) localStorage.setItem("notes-last", note.id);
  renderList();
}

function showEmptyEditor() {
  state.current = null;
  el.editor.hidden = true;
  el.editorEmpty.hidden = false;
  setStatus("—", "");
  renderList();
  if (NARROW.matches) showView("list");
}

function startNewNote() {
  if (state.dirty) flush();
  applyNote(blankNote(contextSeed(readerContext)));
  state.autoBook = readerContext?.revisionId || null;
  el.title.focus();
}

/* ------------------------------ Reader 阅读位置 ------------------------------ */

function renderContextHint() {
  const label = readerContext ? contextLabel(readerContext) : "";
  if (EMBED && label) {
    el.headerHint.textContent = `当前：${label}`;
    el.newNote.textContent = "＋ 本页笔记";
    el.newNote.title = `新建一条并带上 ${label}`;
  } else {
    el.headerHint.textContent = "Markdown 笔记本 · 本地保存";
    el.newNote.textContent = "+ 新建";
    el.newNote.removeAttribute("title");
  }
  el.usePage.hidden = !(EMBED && label);
}

// 翻页 / 换教材后同步：只调整还没落库的新草稿，用户已经动手写内容就不再改它。
function applyReaderContext() {
  renderContextHint();
  if (!state.current || state.current.id) return;
  if (bodyText().trim() || el.title.value.trim()) return;
  const seed = contextSeed(readerContext);
  if (seed.book_source_revision_id) {
    ensureBookOption(seed.book_source_revision_id, readerContext.bookTitle);
    const current = el.book.value || "";
    if (!current || current === state.autoBook) {
      el.book.value = seed.book_source_revision_id;
    }
    state.autoBook = seed.book_source_revision_id;
  }
  if (Number.isInteger(seed.page_index)) el.page.value = String(seed.page_index + 1);
}

function useReadingPage() {
  if (!readerContext) return;
  if (!state.current) startNewNote();
  const seed = contextSeed(readerContext);
  if (seed.book_source_revision_id) {
    ensureBookOption(seed.book_source_revision_id, readerContext.bookTitle);
    el.book.value = seed.book_source_revision_id;
    state.autoBook = seed.book_source_revision_id;
  }
  if (Number.isInteger(seed.page_index)) el.page.value = String(seed.page_index + 1);
  scheduleSave();
}

window.addEventListener("message", (event) => {
  if (event.origin !== location.origin) return;
  const data = event.data;
  if (!data || data.type !== CONTEXT_MESSAGE_TYPE) return;
  const next = normalizeContext(data);
  if (!next || sameContext(next, readerContext)) return;
  readerContext = next;
  applyReaderContext();
});

async function selectNote(id) {
  if (state.current && state.current.id === id) {
    if (NARROW.matches) showView("editor");
    return;
  }
  if (state.dirty) await flush();
  try {
    const data = await api(`/api/notes/${id}`);
    applyNote(data.note);
  } catch (error) {
    if (!state.unauthorized) setBanner(String(error.message || error));
  }
}

function showView(name) {
  // 窄窗单栏：列表与编辑互斥显示
  document.body.classList.toggle("view-list", name === "list");
  document.body.classList.toggle("view-editor", name === "editor");
}

/* ------------------------- 实时渲染块编辑器 -------------------------
 * 正文按空行切成语块：非编辑中的块直接渲染 Markdown，光标所在块显示源码；
 * 按Enter提交当前块（立即渲染）并开新块继续写，列表/引用/代码块内回车照常换行；
 * 点击渲染块切到源码编辑，Esc / Ctrl+Enter / 点击其他位置落盘当前块。 */

const live = { blocks: [""], active: -1, lastBody: "" };

// 当前块的编辑框：唯一的 textarea，在块之间移动位置
const blockEditor = document.createElement("textarea");
blockEditor.id = "note-text";
blockEditor.spellcheck = false;
blockEditor.placeholder = "用 Markdown 记录…";
el.text = blockEditor;

function bodyText() {
  return joinBlocks(live.blocks);
}

function fitEditor() {
  blockEditor.style.height = "auto";
  blockEditor.style.height = `${blockEditor.scrollHeight}px`;
}

function renderBlockView(index) {
  const div = document.createElement("div");
  div.className = "markdown-body block-view";
  div.dataset.block = String(index);
  const source = live.blocks[index];
  if (source.trim()) {
    renderAssistantAnswer(div, source);
  } else {
    const hint = document.createElement("p");
    hint.className = "block-empty-hint";
    hint.textContent = "（空段落，点击继续写）";
    div.append(hint);
  }
  return div;
}

function renderLive() {
  const scroll = el.live.scrollTop;
  el.live.replaceChildren();
  for (let index = 0; index < live.blocks.length; index += 1) {
    if (index === live.active) {
      blockEditor.value = live.blocks[index];
      el.live.append(blockEditor);
    } else {
      el.live.append(renderBlockView(index));
    }
  }
  el.live.scrollTop = scroll;
  if (live.active >= 0) fitEditor();
}

function loadLive(body, { autoFocus = false } = {}) {
  live.blocks = parseBlocks(body);
  live.lastBody = bodyText();
  if (autoFocus) {
    live.active = 0;
    renderLive();
    blockEditor.focus();
    const end = blockEditor.value.length;
    blockEditor.setSelectionRange(end, end);
  } else {
    live.active = -1;
    renderLive();
  }
}

function updateBody() {
  const body = bodyText();
  if (body === live.lastBody) return;
  live.lastBody = body;
  scheduleSave();
}

// 把当前编辑框内容写回块数组；空块且不止一块时直接移除
function settleActive() {
  const text = blockEditor.value;
  live.blocks[live.active] = text;
  if (text.trim() === "" && live.blocks.length > 1) live.blocks.splice(live.active, 1);
}

function commitActive() {
  if (live.active < 0) return;
  settleActive();
  live.active = -1;
  renderLive();
  updateBody();
}

function activate(index, { focus = true } = {}) {
  if (live.active >= 0 && live.active !== index) {
    settleActive();
    const removed = live.blocks[live.active].trim() === "" && live.blocks.length > 1;
    if (removed) {
      live.blocks.splice(live.active, 1);
      if (live.active < index) index -= 1;
    }
    updateBody();
  }
  index = Math.max(0, Math.min(index, live.blocks.length - 1));
  live.active = index;
  renderLive();
  if (focus) {
    blockEditor.focus();
    const end = blockEditor.value.length;
    blockEditor.setSelectionRange(end, end);
  }
}

blockEditor.addEventListener("input", () => {
  if (live.active < 0) return;
  live.blocks[live.active] = blockEditor.value;
  fitEditor();
  updateBody();
});

// 回车提交：光标处把当前块切成两段——前半段立即渲染，后半段成为新编辑块
function splitActiveBlock() {
  const value = blockEditor.value;
  const start = blockEditor.selectionStart;
  const end = blockEditor.selectionEnd;
  let before = value.slice(0, start);
  const after = value.slice(end);
  if (after.trim() === "") {
    // 后半段为空时，前半段行尾的悬空换行没有意义，修剪掉与 parseBlocks 规范一致
    before = before.replace(/\n+$/, "");
  }
  const at = live.active;
  live.blocks[at] = before;
  live.blocks.splice(at + 1, 0, after);
  live.active = at + 1;
  if (before.trim() === "") {
    // 光标前没有内容：丢弃空块，直接编辑后半段
    live.blocks.splice(at, 1);
    live.active = at;
  }
  renderLive();
  updateBody();
  blockEditor.focus();
  blockEditor.setSelectionRange(0, 0);
}

blockEditor.addEventListener("keydown", (event) => {
  if (event.isComposing) return;
  if (event.key === "Escape" || ((event.ctrlKey || event.metaKey) && event.key === "Enter")) {
    event.preventDefault();
    commitActive();
    return;
  }
  if (
    event.key === "Enter" &&
    !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey
  ) {
    const value = blockEditor.value;
    const pos = blockEditor.selectionStart;
    const textBefore = value.slice(0, pos);
    if (insideFence(textBefore)) return; // 代码围栏内：照常换行
    const lineStart = textBefore.lastIndexOf("\n") + 1;
    const line = textBefore.slice(lineStart);
    if (continuesOnEnter(line)) return; // 列表 / 引用 / 表格行：照常换行继续
    event.preventDefault();
    splitActiveBlock();
    return;
  }
  if (event.key === "ArrowUp" && !event.shiftKey && live.active > 0) {
    const before = blockEditor.value.slice(0, blockEditor.selectionStart);
    if (!before.includes("\n")) {
      event.preventDefault();
      activate(live.active - 1);
    }
    return;
  }
  if (event.key === "ArrowDown" && !event.shiftKey && live.active < live.blocks.length - 1) {
    const value = blockEditor.value;
    const after = value.slice(blockEditor.selectionEnd);
    if (blockEditor.selectionEnd >= value.length || !after.includes("\n")) {
      event.preventDefault();
      activate(live.active + 1);
    }
  }
});

el.live.addEventListener("click", (event) => {
  if (event.target === blockEditor) return;
  const view = event.target.closest("[data-block]");
  if (view) {
    activate(Number(view.dataset.block));
    return;
  }
  // 点在块与块之间或末尾空白：直接写最后一块；最后一块非空则追加一个空段
  const last = live.blocks.length - 1;
  const lastEmpty = live.blocks[last].trim() === "";
  if (lastEmpty || live.active === last) {
    activate(last);
  } else {
    live.blocks.push("");
    updateBody();
    activate(live.blocks.length - 1);
  }
});

// 点编辑区以外（标题、来源行、底部按钮等）时落盘当前块
document.addEventListener("pointerdown", (event) => {
  if (live.active < 0) return;
  if (el.live.contains(event.target)) return;
  commitActive();
});

/* ------------------------------ 保存 ------------------------------ */

function changedFields(values) {
  const current = state.current || {};
  const payload = {};
  for (const key of ["title", "body", "book_source_revision_id", "page_index", "pinned"]) {
    const before = key === "title" ? (current.title || "") : current[key];
    const now = key === "title" ? values.title : values[key];
    if (key === "pinned") {
      if (Boolean(before) !== Boolean(now)) payload.pinned = Boolean(now);
      continue;
    }
    if ((before ?? null) !== (now ?? null)) payload[key] = now;
  }
  return payload;
}

function scheduleSave() {
  state.dirty = true;
  setStatus("未保存…", "");
  if (state.timer) clearTimeout(state.timer);
  state.timer = setTimeout(() => {
    state.timer = null;
    save();
  }, AUTOSAVE_DELAY);
}

async function save({ silent = false } = {}) {
  if (state.timer) {
    clearTimeout(state.timer);
    state.timer = null;
  }
  if (!state.current) return;
  if (state.saving) return state.saving;

  const values = collect();
  const isNew = !state.current.id;
  if (isNew && !values.title.trim() && !values.body.trim()) {
    state.dirty = false;
    setStatus("新笔记 · 输入内容后自动保存", "");
    return;
  }
  const payload = isNew ? values : changedFields(values);
  if (!isNew && Object.keys(payload).length === 0) {
    state.dirty = false;
    setStatus(`已保存 ${hhmm(state.current.updated_at)}`, "");
    return;
  }

  const seq = ++state.seq;
  el.save.disabled = true;
  setStatus(isNew ? "正在创建…" : "正在保存…", "");
  state.saving = (async () => {
    try {
      const data = isNew
        ? await api("/api/notes", { method: "POST", body: JSON.stringify(payload) })
        : await api(`/api/notes/${state.current.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      if (seq !== state.seq) return;
      const note = data.note;
      state.current = note;
      // 标题留空时服务端会从正文派生，回填让用户看到实际标题
      if (!el.title.value.trim() && note.title) el.title.value = note.title;
      state.dirty = false;
      setStatus(`已保存 ${hhmm(note.updated_at)}`, "ok");
      el.del.disabled = false;
      el.exportOne.disabled = false;
      setBanner("");
      await loadList();
      renderList();
    } catch (error) {
      if (seq !== state.seq) return;
      state.dirty = true;
      setStatus(`保存失败：${error.message || error}（点击重试）`, "error");
      if (!silent && !state.unauthorized) setBanner(String(error.message || error));
    } finally {
      if (seq === state.seq) el.save.disabled = false;
      state.saving = null;
    }
  })();
  return state.saving;
}

async function flush() {
  if (!state.dirty) return;
  await save({ silent: true });
}

/* ------------------------------ 删除 / 导出 ------------------------------ */

async function deleteCurrent() {
  if (!state.current || !state.current.id) {
    showEmptyEditor();
    return;
  }
  const title = el.title.value.trim() || state.current.title || "未命名笔记";
  if (!window.confirm(`删除笔记「${title}」？此操作不可撤销。`)) return;
  if (state.timer) {
    clearTimeout(state.timer);
    state.timer = null;
  }
  state.dirty = false;
  try {
    await api(`/api/notes/${state.current.id}`, { method: "DELETE" });
    showEmptyEditor();
    await loadList();
    setBanner("已删除该笔记。", "ok");
    setTimeout(() => setBanner(""), 2600);
  } catch (error) {
    if (!state.unauthorized) setBanner(String(error.message || error));
  }
}

async function downloadMarkdown(url, fallbackName) {
  // 桌面外壳（Tauri/WebView2）不接管 <a download> 的 blob 下载，改由本地服务落盘；
  // dialog=1 让服务端先弹系统「另存为」对话框，用户可自选保存位置。
  const saveToDisk = Boolean(window.__TAURI_INTERNALS__ || window.__TAURI__);
  if (saveToDisk) url = `${url}${url.includes("?") ? "&" : "?"}save=1&dialog=1`;
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { "X-Reader-Token": token, Accept: "text/markdown, application/json" },
  });
  if (response.status === 401) {
    state.unauthorized = true;
    setBanner("鉴权失败：请从平台侧栏的「笔记」重新打开这个窗口。");
    return;
  }
  if (!response.ok) {
    let message = `导出失败（${response.status}）`;
    try {
      const data = await response.json();
      if (data && data.error) message = data.error;
    } catch (_error) {
      /* 保留默认提示 */
    }
    setBanner(message);
    return;
  }
  const name = filenameFromDisposition(response.headers.get("Content-Disposition"), fallbackName);
  if (saveToDisk) {
    const payload = await response.json().catch(() => null);
    if (payload?.cancelled) {
      setBanner("已取消导出。");
      return;
    }
    setBanner(payload?.path ? `已导出 ${payload.filename}（保存到：${payload.path}）` : "导出已保存。");
    return;
  }
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 4000);
}

function todayStamp() {
  const now = new Date();
  return `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
}

async function exportCurrent() {
  if (!state.current || !state.current.id) {
    await save();
  }
  if (!state.current || !state.current.id) return;
  await downloadMarkdown(`/api/notes/${state.current.id}/export`, `笔记_${todayStamp()}.md`);
}

async function exportAll() {
  if (state.dirty) await flush();
  await downloadMarkdown("/api/notes/export", `笔记本_${todayStamp()}.md`);
}

/* ------------------------------ 事件绑定 ------------------------------ */

el.newNote.addEventListener("click", startNewNote);
el.usePage.addEventListener("click", useReadingPage);
el.save.addEventListener("click", () => save());
el.del.addEventListener("click", deleteCurrent);
el.exportOne.addEventListener("click", exportCurrent);
el.exportAll.addEventListener("click", exportAll);
el.back.addEventListener("click", () => {
  if (state.dirty) flush();
  showView("list");
});
el.status.addEventListener("click", () => {
  if (el.status.classList.contains("error")) save();
});

el.title.addEventListener("input", () => {
  scheduleSave();
});
for (const control of [el.book, el.page, el.pinned]) {
  control.addEventListener("change", () => {
    scheduleSave();
  });
}

el.search.addEventListener("input", () => {
  if (state.searchTimer) clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(() => {
    state.searchTimer = null;
    loadList();
  }, 220);
});

document.addEventListener("keydown", (event) => {
  if (event.isComposing) return;
  const meta = event.ctrlKey || event.metaKey;
  if (meta && event.key.toLowerCase() === "s") {
    event.preventDefault();
    save();
    return;
  }
  if (event.key === "Escape" && NARROW.matches && document.body.classList.contains("view-editor")) {
    event.preventDefault();
    if (state.dirty) flush();
    showView("list");
  }
});

// 关窗前尽力保住最后一段输入：keepalive 请求不依赖页面存活
window.addEventListener("pagehide", () => {
  if (!state.dirty || !state.current || !state.current.id) return;
  try {
    const body = JSON.stringify(collect());
    if (body.length > 60000) return;
    fetch(`/api/notes/${state.current.id}`, {
      method: "PATCH",
      keepalive: true,
      credentials: "same-origin",
      headers: { "X-Reader-Token": token, "Content-Type": "application/json" },
      body,
    });
  } catch (_error) {
    /* 尽力而为 */
  }
});

window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

NARROW.addEventListener("change", () => {
  if (!NARROW.matches) document.body.classList.remove("view-editor", "view-list");
});

/* ------------------------------ 启动 ------------------------------ */

async function boot() {
  renderContextHint();
  await loadBooks();
  await loadList();
  if (state.notes.length) {
    // 回到上次编辑的笔记；找不到就打开最新一条
    const last = localStorage.getItem("notes-last");
    const target = state.notes.find((note) => note.id === last) || state.notes[0];
    if (target) await selectNote(target.id);
  }
  if (!state.current) {
    // 从 Reader 抽屉进来且本子上还没有笔记：直接开一张带页码的草稿，省掉一次点击
    if (EMBED) startNewNote();
    else showEmptyEditor();
  }
}

boot();
