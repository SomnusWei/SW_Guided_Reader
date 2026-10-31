import {createScreens, createChapterEntry, createAssistantModelMenu, createMessageRail, header, action,
  providerDisplayName, providerOptionLabel, PROVIDER_NAMES} from "/screens.js";
import { streamAssistantResponse } from "/assistant-stream.js";
import * as pdfjsLib from "/vendor/pdf.mjs";
import {
  lineBounds, nearestCellBoundary, nearestLine, resolveSelection, resolvedText,
  selectionPresentationQuads,
} from "/selection.js";
import { renderAssistantAnswer, renderedSelectionToRaw, selectionToMarkdown } from "/assistant-render.js";
import { createMemoryUI } from "/memory-ui.js";
import { createGuideUI } from "/guide-ui.js";
import { createAssistantNavigator } from "/assistant-navigator.js";
import { createVisualSelection } from "/visual-selection.js";
import { createPdfSession } from "/pdf-session.js";
import { pageRenderWindow, pageRenderPlan, pageViewportRange } from "/geometry.js";
import { canvasContentBounds, createReadingDisplay, createZoomEditor, createPortraitLayout } from "/reading-display.js";
import { createAnnotationUI, annotationPageIndex, isHexColor } from "/annotation-ui.js";
import { createAnnotationPanel } from "/annotation-panel.js";
import { downloadMarkdown, markdownExportUrl } from "/export-md.js";
import { createSkillStore, createSkillPicker, createSkillsSettings } from "/skills-ui.js";
import { createAiKeysSettings } from "/ai-keys-ui.js";
import { comboFromEvent, createShortcutsSection } from "/shortcuts-ui.js";
import { createSettingsUI, createOcrBanner } from "/settings-ui.js";
import { createPluginsSettings } from "/plugins-ui.js";
import { createAssistantHistoryUI } from "/assistant-history-ui.js";
import { createPluginHost } from "/plugin-host.js";
import { createThemeController } from "/theme.js";
import { createOnboarding } from "/onboarding.js";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.mjs";
const pdfSession = createPdfSession(options => pdfjsLib.getDocument(options));

const elements = Object.fromEntries(
  ["library-home", "library-empty", "import-input", "book-list", "book-count", "reader", "reader-title", "viewer", "pages", "page-number", "page-total", "previous-page", "next-page", "zoom-out", "zoom-in", "zoom-value", "reader-more", "reader-more-toggle", "reader-more-menu", "preparation-status", "printed-page-edit", "printed-page-label", "outline-toggle", "outline-panel", "outline-close", "outline-status", "outline-tree", "outline-empty", "status", "back-to-library", "search-toggle", "search-panel", "search-close", "search-form", "search-query", "search-coverage", "search-results", "search-empty", "marks-toggle", "marks-count", "marks-panel", "marks-page", "marks-list", "marks-empty", "marks-close", "assistant-toggle", "assistant-panel", "assistant-resize-handle", "assistant-expand", "assistant-title", "assistant-model", "assistant-model-lock", "assistant-close", "assistant-context-bar", "assistant-root-switcher", "assistant-back", "assistant-depth", "assistant-close-root", "assistant-breadcrumb", "assistant-children", "assistant-child-list", "assistant-scope", "assistant-first-turn", "assistant-draft-text", "assistant-supplement-label", "assistant-supplement", "assistant-start", "assistant-readiness", "assistant-turns", "assistant-empty", "assistant-follow-up", "assistant-question", "assistant-send", "assistant-answer-actions", "assistant-ask-deeper", "assistant-save-excerpt", "assistant-cancel-selection", "selection-actions", "copy-selection", "ask-selection", "save-highlight", "add-note", "cancel-selection", "note-editor", "annotation-note", "save-note", "save-underline", "annotations-toggle", "annotations-count", "annotations-panel", "annotations-close", "annotations-list", "annotations-empty", "annotations-status", "annotations-export", "annotations-sync", "annotation-textbox", "annotation-region", "annotation-sticky", "reader-settings", "outline-import", "assistant-export", "assistant-history", "notes-toggle", "notes-panel", "notes-panel-close", "notes-panel-context", "notes-frame"]
    .map((id) => [id, document.getElementById(id)]),
);

const assistantModelMenu = createAssistantModelMenu(elements["assistant-model"]);
let assistantProviderOptionKey = "";
const assistantNavigator = createAssistantNavigator(elements["assistant-panel"], (...args) => focusAssistant(...args));
createMessageRail(elements['assistant-turns']);
const modelControl = document.querySelector('.assistant-model-control');

const query = new URLSearchParams(location.search);
const launchToken = query.get("token") || sessionStorage.getItem("reader-token") || "";
if (launchToken) {
  sessionStorage.setItem("reader-token", launchToken);
  if (query.has("token")) history.replaceState(null, "", location.pathname);
}

const state = {
  books: [], book: null, revision: null, pdf: null, zoom: 1,
  currentPage: 0, generation: 0, renderGeneration: 0, renderTasks: new Map(), pageRenderRequests: new Map(), pageProxies: new Map(), rendered: new Set(),
  scrollFrame: 0, saveTimer: 0, resizeTimer: 0, viewerLayoutWidth: 0, priorityTimer: 0,
  preparation: new Map(), overlayData: new Map(), eventSource: null,
  preparationRetryTimer: 0, preparationRetryDelay: 1000,
  annotationData: new Map(), guideSelection: null, selection: null, selecting: false, selectionMenuPoint: null,
  searchRequest: 0, searchMatch: null,
  outlineRequest: 0, outlineNodes: [], pageLabels: new Map(), mapTimer: 0,
  readerSessionId: null, assistantConfigured: false, assistantCooling: false,
  assistantActiveProvider: "deepseek", assistantModelSelectionInitialized: false,
  assistantStatusAvailable: false, assistantOffReason: null,
  assistantState: { version: 0, roots: [], focused_ref: null, current: null },
  assistantDraft: null, assistantAnswerSelection: null, assistantAnswerSelectionStash: null,
  assistantProviderStatuses: [],
  assistantSaveIntents: new Map(), assistantSavedTurns: new Map(),
  assistantReviewPolls: new Map(),
  assistantPending: false, assistantStatusTimer: 0,
  assistantStreamControllers: new Map(),
  assistantScrollPositions: new Map(), assistantChildRequests: new Map(),
  assistantViewDrafts: new Map(), assistantRenderedKey: null,
  assistantDockWidth: 410, assistantDockWidthBeforeExpanded: 410,
  assistantExpanded: false,
  learningChapterId: null,
  directoryMode: null, currentSectionId: null, currentOutlineId: null, outlineKinship: null,
  pinnedLanding: null, navigationAnchorId: null,
  directoryCollapsed: null, directoryExpandedFor: null,
};

const assistantRegionImages = new Map();
let assistantRegionView = null;
const visualSelection = createVisualSelection({
  viewer: elements.viewer,
  toggle: document.getElementById('visual-select-toggle'),
  hint: document.getElementById('visual-select-hint'),
  enabled: () => Boolean(state.pdf && state.readerSessionId && !state.assistantPending),
  onEnter: () => { state.pinnedLanding = null; clearSelection(); hideAssistantAnswerActions(); },
  onSelect: stageVisualAssistant,
});

let settings = null;
let latestOcr = null;
const annotationUI = createAnnotationUI({
  pages: elements.pages,
  viewer: elements.viewer,
  api,
  revisionId: () => state.revision?.id,
  announce,
  onChange: (annotation, options = {}) => {
    upsertAnnotation(annotation);
    renderAnnotations(annotationPageIndex(annotation));
    updateMarksPanel();
    annotationPanel.upsert(annotation);
    if (options.created) {
      // 文本框/区域高亮也纳入 Ctrl+Z 撤销栈（高亮/下划线/笔记在 saveAnnotation 里入栈）。
      if (state.revision?.id) {
        annotationUndoStack.push({ revisionId: state.revision.id, annotation });
      }
      annotationPanel.load({ force: true });
    }
  },
});

const annotationPanel = createAnnotationPanel({
  api,
  revisionId: () => state.revision?.id,
  goToPage: (...args) => goToPage(...args),
  announce,
  annotationUI,
  onChanged: annotation => {
    upsertAnnotation(annotation);
    renderAnnotations(annotationPageIndex(annotation));
    updateMarksPanel();
  },
  onDeleted: annotation => {
    const pageIndex = annotationPageIndex(annotation);
    state.annotationData.set(pageIndex,
      (state.annotationData.get(pageIndex) || []).filter(value => value.id !== annotation.id));
    renderAnnotations(pageIndex);
    updateMarksPanel();
  },
  exportMarkdown: () => exportAnnotationsMarkdown(),
  syncAll: () => syncAllAnnotations({ silent: false }),
  syncVisible: () => Boolean(settings?.wantsSyncButton),
  onRender: () => syncAnnotationsBadge(),
});

// AI 对话历史：查看本地归档的临时解释会话（只读），避免重复提问浪费 token。
const assistantHistoryUI = createAssistantHistoryUI({
  api,
  revisionId: () => state.revision?.id,
  announce,
  launchToken,
  onExport: (rootId) => {
    const revisionId = state.revision?.id;
    if (!revisionId) { announce("请先打开一本教材。", true); return; }
    downloadMarkdown({
      url: markdownExportUrl(revisionId, "assistant_conversation", rootId),
      token: launchToken, scope: "assistant_conversation", announce,
    }).catch((error) => announce(error.message || "导出失败，请稍后重试。", true));
  },
});

const skillStore = createSkillStore({ api, announce });
const skillPicker = createSkillPicker({ store: skillStore, bookId: () => state.book?.id });
elements["assistant-follow-up"].prepend(skillPicker.element);
const skillsSettings = createSkillsSettings({
  store: skillStore, announce, onChange: () => skillPicker.sync(),
});
const aiKeysSettings = createAiKeysSettings({ api, announce });
const shortcutRuntime = { map: new Map(), recording: false, onChange: null };
const shortcutsSettings = createShortcutsSection({ api, announce, runtime: shortcutRuntime });
shortcutRuntime.onChange = (map) => { shortcutRuntime.map = map; };
const ocrBanner = createOcrBanner();
elements.reader.append(ocrBanner.element);

// 插件宿主在文件后半段才创建（它依赖选区状态），设置页与主题却要用它。
// 用一个可改写的转发桥接一下，避免为了顺序把插件宿主整个提前。
const pluginBridge = { refresh: () => {}, pushTheme: () => {}, openWindow: () => {} };
// 主题：服务端 settings.json 是权威，localStorage 只是给首屏防闪脚本用的镜像。
const themeController = createThemeController({
  request: api,
  onChange: (theme) => pluginBridge.pushTheme(theme),
});
const pluginsSettings = createPluginsSettings({
  api, announce, onOpen: (id) => pluginBridge.openWindow(id), onChanged: () => pluginBridge.refresh(),
});

settings = createSettingsUI({
  api,
  revisionId: () => state.revision?.id,
  announce,
  skillsSection: skillsSettings,
  aiKeysSection: aiKeysSettings,
  shortcutsSection: shortcutsSettings,
  pluginsSection: pluginsSettings,
  themeController,
  onSyncNow: () => syncAllAnnotations({ silent: false }),
  onSyncStateChange: () => annotationPanel.render(),
});
themeController.load();

// 三步首启引导：只在 settings.onboarding.done 为 false 时弹一次（老用户零打扰）。
const onboarding = createOnboarding({
  api,
  announce,
  onDone: () => skillsSettings.refresh(),
});
onboarding.maybeShow();
window.onboarding = onboarding;
Object.assign(state, {
  annotationUI, annotationPanel, ocrBanner, skillStore, skillPicker, settings,
});
shortcutsSettings.refresh();

const MASTER_WORKSPACE_MOTION = Object.freeze({
  workspace: 220,
  sidebarDelay: 96,
  sidebarReveal: 124,
  restoreWorkspaceDelay: 36,
  restoreWorkspace: 184,
  sidebarHide: 96,
});
let masterWorkspaceMorph = null;
let masterMorphFocusOrigin = null;
let masterSidebarScrollPosition = 0;
let assistantWorkspaceMorph = null;
let assistantMorphFocusOrigin = null;
let assistantSidebarScrollPosition = 0;
let selectionActionsDismissal = null;

elements["assistant-expand"].addEventListener("pointerdown", () => {
  const active = document.activeElement;
  assistantMorphFocusOrigin = null;
  const conversation = elements["assistant-panel"].querySelector(".assistant-conversation");
  if (active instanceof HTMLElement && conversation?.contains(active)) {
    assistantMorphFocusOrigin = captureMasterFocus(active);
  }
});

const memory = createMemoryUI({ api, announce, home: () => showHome(), resume: async () => { await loadBooks(); const b = state.books.filter(b => b.active_revision?.position.updated_at).sort((a,b) => b.active_revision.position.updated_at.localeCompare(a.active_revision.position.updated_at))[0]; if(b) await enterReader(b); else await showHome(); },
  enterView: () => { screens.close(); elements['library-home'].hidden = true; elements.reader.hidden = true; },
  leaveView: () => { elements['library-home'].hidden = false; },
  returnToSource: async item => {
  const books = (await api('/api/books')).books;
  const book = books.find(b => b.id === item.book_id && b.active_revision?.id === item.book_source_revision_id);
  if (!book) throw new Error('原教材当前不可用。');
  if (state.book?.id !== book.id) {
    if (state.book) closeReader();
    const target = item.source_kind === 'MASTER' ? null
      : {page:item.source.pdf_page_index, y:Math.min(...item.source.quads.flat().map(p => p[1]))};
    await openBook(book, target);
  }
  if (state.revision?.id !== item.book_source_revision_id || !state.pdf) throw new Error('请重新打开原教材。');
  if (item.source_kind === 'MASTER') {
    throw new Error('知识点学习已在 2.0 移除，这条旧记录只能查看不能回到原文。');
  } else {
    setAssistantPanelOpen(false);
    goToPage(item.source.pdf_page_index, Math.min(...item.source.quads.flat().map(p => p[1])));
    await refreshAnnotationPage(item.source.pdf_page_index);
    elements['marks-panel'].hidden = false;
    elements['marks-toggle'].setAttribute('aria-expanded', 'true');
    updateMarksPanel();
    const card = [...elements['marks-list'].children].find(n => n.dataset.annotationId === item.source_id);
    if (card) { card.scrollIntoView({ block: 'center' }); card.tabIndex = -1; card.focus({ preventScroll: true }); }
  }
} });

const ZOOM_LEVELS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4];
const DIRECTORY_PANE_WIDTH = 304;
const DIRECTORY_GAP = 0;
// 展示名统一由 screens.js 的 PROVIDER_NAMES 提供；这里保留常量名作为 tests-js 切片的边界锚点。
const ASSISTANT_PROVIDER_LABELS = PROVIDER_NAMES;
const ASSISTANT_DOCK_MIN_WIDTH = 320;
const ASSISTANT_DOCK_MAX_WIDTH = 760;

const teachingUiOptions = { state, api, goToPage,
  streamGuide: (path, options, onEvent) => streamAssistantResponse(path, {
    ...options,
    credentials: "same-origin",
    headers: { "X-Reader-Token": launchToken, Accept: "text/event-stream", ...(options?.headers || {}) },
  }, onEvent, fetch, { label: "导读", codePrefix: "GUIDE" }),
  readingAnchor: () => captureZoomAnchor(undefined, elements.viewer.getBoundingClientRect().top + 1),
  hideContextMenu: hideSelectionActions,
  contextMenu: openVisibleSelectionActions,
  layout: change => {
    const anchor = captureZoomAnchor();
    const previousReserve = inlineReserve();
    change();
    // Inline cards consume space inside the viewer without changing its width,
    // so the viewer ResizeObserver cannot detect this layout change.
    if (inlineReserve() !== previousReserve) relayoutPages(anchor);
    else evaluateDirectoryMode();
  },
  closeDock: () => claimRightDock("guide"),
  closeDirectory: () => closeDirectory(),
  setDockWidth: applyAssistantDockWidth,
  dockWidth: () => state.assistantDockWidth,
  explain: openAssistantDraftFromVisibleSelection };
const guide = createGuideUI(teachingUiOptions);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    signal: options.signal || ((!options.method || options.method === "GET") ? AbortSignal.timeout(15000) : undefined),
    credentials: "same-origin",
    headers: { "X-Reader-Token": launchToken, "X-Assistant-View": "current", ...(options.headers || {}) },
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({ error: `请求失败（${response.status}）` }));
  if (!response.ok) {
    const error = new Error(payload.error || `请求失败（${response.status}）`);
    error.code = payload.code || `HTTP_${response.status}`;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function announce(message, error = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("error", error);
  elements.status.classList.add("visible");
  clearTimeout(announce.timer);
  announce.timer = setTimeout(() => elements.status.classList.remove("visible"), error ? 6000 : 2800);
}

async function loadBooks() {
  try {
    const payload = await api('/api/books');
    state.books = payload.books;
    elements['book-count'].textContent = String(state.books.length);
    renderLibrary();
    elements['library-empty'].hidden = state.books.length > 0;
  } catch(error) {
    const message = document.createElement('p'); message.className = 'local-error'; message.textContent = error.message;
    elements['book-list'].replaceChildren(message, action('重试读取书库', loadBooks));
    elements['library-empty'].hidden = true;
  }
}

function renderLibrary() { screens.library(state.books).catch(error => announce(error.message, true)); }

async function enterReader(book, target) {
  screens.close();
  const alreadyOpen = state.book?.id === book.id && state.pdf;
  await openBook(book, target);
  if (target && state.pdf && alreadyOpen) goToPage(target.page, target.y);
}
async function showHome() {
  screens.close();
  elements['library-home'].hidden = false;
  await loadBooks();
}
const screens = createScreens({api, home: showHome, memory: () => memory.open(), read: enterReader,
  remove: removeBook, revision: chooseRevision, announce, isAuxiliary:isAuxiliaryOutlineRoot});
const chapterEntry = createChapterEntry({api, goToPage, revision: () => state.revision?.id,
  outlineSnapshot: () => state.outlineNodes,
  published: () => {},
  closePeers: () => {
    closeDirectory();
    elements["search-panel"].hidden = true;
    elements["marks-panel"].hidden = true;
    elements["search-toggle"].setAttribute("aria-expanded", "false");
    elements["marks-toggle"].setAttribute("aria-expanded", "false");
    state.searchRequest += 1;
    clearSearchMatch();
  },
  openOverview: async chapterId => {
    const book = state.book; if (!book) return;
    await savePosition(); closeReader(); await loadBooks();
    await screens.open(state.books.find(b => b.id === book.id) || book, chapterId);
  }});
const homeHeader = header('home', showHome, () => memory.open(), action('＋ 导入教材', () => elements['import-input'].click(), 'primary-action'));
elements['library-home'].querySelector('.home-toolbar').replaceWith(homeHeader);
homeHeader.querySelector('.space-navigation button:last-child').classList.add('memory-open');
// The hidden file input remains owned by the intake flow.
elements['library-home'].append(elements['import-input']);

function chooseRevision(book) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "application/pdf,.pdf";
  input.addEventListener("change", () => input.files[0] && importPdf(input.files[0], book.id));
  input.click();
}

async function importPdf(file, bookId = null) {
  if (!file.name.toLowerCase().endsWith(".pdf") && file.type !== "application/pdf") {
    announce("请选择 PDF 文件。", true);
    return;
  }
  document.getElementById('import-status')?.remove();
  announce(bookId ? "正在验证新来源版本……" : "正在验证并导入 PDF……");
  const parameters = new URLSearchParams();
  if (bookId) parameters.set("book_id", bookId);
  try {
    const result = await api(`/api/books?${parameters}`, {
      method: "POST",
      headers: { "X-File-Name": encodeURIComponent(file.name), "Content-Type": "application/pdf" },
      body: file,
    });
    state.book = null;
    await loadBooks();
    const refreshed = state.books.find((book) => book.id === result.book.id) || result.book;
    await openBook(refreshed);
    announce(result.duplicate ? "书库中已有这份文件，没有重复导入。" : (bookId ? "已添加新来源版本。" : "PDF 已导入，可以开始阅读。"));
  } catch (_error) {
    const message = _error.message || "导入失败，请检查 PDF 后重试。";
    let status = document.getElementById('import-status');
    if(!status) { status = document.createElement('p'); status.id = 'import-status'; status.className = 'local-error'; elements['library-home'].querySelector('main').prepend(status); }
    status.textContent = message;
    announce(message, true);
  } finally {
    elements["import-input"].value = "";
  }
}

async function removeBook(book) {
  const confirmed = window.confirm(`删除《${book.title}》？\n\n这会永久删除本应用中保存的 PDF 版本、阅读位置、学习地图、高亮和笔记，且无法撤销。`);
  if (!confirmed) return;
  try {
    await api(`/api/books/${book.id}`, { method: "DELETE" });
    if (state.book?.id === book.id) state.book = null;
    await loadBooks();
    announce("已删除教材、阅读位置、学习地图、高亮和笔记。");
  } catch (_error) {
    if (state.book?.id === book.id) state.book = null;
    await loadBooks();
    announce("删除未完成，请重试。", true);
  }
}

let firstPagePreviewRequest = null;
let readerContextGeneration = -1;

function startReaderContext() {
  const generation = state.generation;
  if (readerContextGeneration === generation) return;
  readerContextGeneration = generation;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (generation !== state.generation || !state.revision) return;
    refreshAssistantStatus();
    skillStore.refresh().then(() => skillPicker.sync()).catch(() => {});
    annotationPanel.load().catch(() => {});
    state.settings?.load().then(() => state.annotationPanel?.render()).catch(() => {});
    startPreparation().catch(error => {
      if (generation !== state.generation) return;
      elements['preparation-status'].textContent = '文字准备暂时不可用';
      announce(error.message, true);
    });
    loadBookMap();
  }));
}

async function showFirstPagePreview() {
  const generation = state.generation, revision = state.revision, index = state.currentPage;
  const wrapper = elements.pages.children[index];
  const controller = new AbortController();
  firstPagePreviewRequest = controller;
  try {
    const region = await api(`/api/revisions/${revision.id}/visual-region`, {
      method:'POST', headers:{'Content-Type':'application/json'}, signal:controller.signal,
      body:JSON.stringify({pdf_page_index:index, rect:[0,0,1,1]}),
    });
    if (region.source_sha256 !== revision.blob_sha256 || region.pdf_page_index !== index
        || !region.image?.data_url?.startsWith('data:image/png;base64,')) return;
    const image = new Image();
    image.className = 'page-preview'; image.alt = ''; image.setAttribute('aria-hidden', 'true');
    image.src = region.image.data_url;
    await image.decode();
    if (controller.signal.aborted || generation !== state.generation || !wrapper.isConnected
        || state.rendered.has(index)) return;
    wrapper.append(image);
  } catch { /* The normal PDF renderer remains the fallback, including locked PDFs. */ }
  finally { if (firstPagePreviewRequest === controller) firstPagePreviewRequest = null; }
}

async function openBook(book, initialTarget = null) {
  if (state.book?.id === book.id && state.pdf) return;
  firstPagePreviewRequest?.abort();
  visualSelection.cancel({clear: true});
  state.generation += 1;
  const generation = state.generation;
  // These two controls are shared across openings; an old save cannot reset them.
  elements["save-highlight"].disabled = false;
  elements["save-note"].disabled = false;
  cancelRenders();
  state.pageProxies.clear();
  closePreparationStream();
  applyDirectoryMode(null);
  state.book = book;
  state.revision = book.active_revision;
  state.readerSessionId = crypto.randomUUID();
  state.assistantState = emptyAssistantState();
  state.navigationAnchorId = null;
  state.pinnedLanding = initialTarget ? {pageIndex:initialTarget.page, normalizedY:initialTarget.y ?? 0} : null;
  state.directoryCollapsed = null;
  state.directoryExpandedFor = null;
  state.assistantDraft = null;
  state.assistantAnswerSelection = null;
  state.assistantAnswerSelectionStash = null;
  state.assistantSaveIntents.clear();
  state.assistantSavedTurns.clear();
  clearAssistantReviewPolls();
  state.annotationData.clear();
  state.annotationPanel?.reset();
  state.annotationUI?.cancel();
  state.assistantScrollPositions.clear();
  state.assistantChildRequests.clear();
  state.zoom = state.revision.position.zoom || 1;
  state.learningChapterId = null;
  state.currentPage = initialTarget?.page ?? state.revision.position.pdf_page_index ?? 0;
  state.pdf = null;
  elements["reader-title"].textContent = book.title;
  elements["library-home"].hidden = true;
  elements.reader.hidden = false;
  elements["page-total"].textContent = `/ ${state.revision.page_count}`;
  elements["page-number"].max = String(state.revision.page_count);
  elements["zoom-value"].value = `${Math.round(state.zoom * 100)}%`;
  renderLibrary();
  elements.pages.replaceChildren();
  buildPlaceholders();
  resetAssistantPanel();
  applyRestoredPosition(initialTarget?.y ?? state.revision.position.normalized_offset ?? 0);
  void showFirstPagePreview();
  try {
    const pdf = await pdfSession.open({
      url: `/api/revisions/${state.revision.id}/pdf`,
      httpHeaders: { "X-Reader-Token": launchToken },
      withCredentials: true,
      // These local books stream quickly. Avoid racing the stream with extra
      // ranges; retain seeking for very large files and the small-file default.
      disableRange: state.revision.byte_size >= 32 * 1024 * 1024
        && state.revision.byte_size <= 256 * 1024 * 1024,
      wasmUrl: "/vendor/pdfjs/wasm/",
      cMapUrl: "/vendor/pdfjs/cmaps/",
      cMapPacked: true,
      standardFontDataUrl: "/vendor/pdfjs/standard_fonts/",
      iccUrl: "/vendor/pdfjs/iccs/",
    });
    if (!pdf) return;
    if (generation !== state.generation) return;
    if (pdf.numPages !== state.revision.page_count) throw new Error("保存的 PDF 元数据与来源文件不再一致");
    await nextFrame();
    if (generation !== state.generation) return;
    state.pdf = pdf;
    scheduleViewportUpdate();
  } catch (_error) {
    if (generation !== state.generation) return;
    await pdfSession.close();
    announce("无法打开这份 PDF，请重试。", true);
  }
}

function closeReader() {
  firstPagePreviewRequest?.abort();
  void pdfSession.close();
  visualSelection.cancel({clear: true});
  applyDirectoryMode(null);
  abortAssistantStreams();
  chapterEntry.reset();
  guide.close();
  state.generation += 1;
  cancelRenders();
  closePreparationStream();
  clearSelection();
  clearSearchMatch();
  setNotesPanelOpen(false);
  state.book = null;
  state.revision = null;
  state.learningChapterId = null;
  state.pdf = null;
  state.pageProxies.clear();
  state.readerSessionId = null;
  state.assistantState = emptyAssistantState();
  state.assistantDraft = null;
  state.assistantAnswerSelection = null;
  state.assistantAnswerSelectionStash = null;
  state.assistantSaveIntents.clear();
  state.assistantSavedTurns.clear();
  clearAssistantReviewPolls();
  state.annotationData.clear();
  state.annotationPanel?.reset();
  state.annotationUI?.cancel();
  state.ocrBanner?.hide();
  state.assistantScrollPositions.clear();
  state.assistantChildRequests.clear();
  elements.reader.hidden = true;
  elements["marks-panel"].hidden = true;
  elements["search-panel"].hidden = true;
  closeDirectory();
  setAssistantPanelOpen(false);
  elements["search-toggle"].setAttribute("aria-expanded", "false");
  elements["marks-toggle"].setAttribute("aria-expanded", "false");
  elements["library-home"].hidden = false;
  elements.pages.replaceChildren();
  state.outlineRequest += 1;
  state.outlineNodes = [];
  state.outlineKinship = null;
  state.pageLabels.clear();
  clearTimeout(state.mapTimer);
  clearTimeout(state.assistantStatusTimer);
  renderLibrary();
}

async function returnToLibrary() {
  await savePosition();
  try {
    await clearAssistantSession();
  } catch (_error) {
    announce("临时 AI 对话未能清除，请重试关闭 Reader。", true);
    return;
  }
  closeReader();
}

function displayedRatio(geometry) {
  const [x0, y0, x1, y1] = geometry.media_box;
  const width = x1 - x0;
  const height = y1 - y0;
  return geometry.rotation % 180 === 0 ? height / width : width / height;
}

// 2.0 删除行间教学后，页宽不再被行间面板占用；保留此函数以免改动大量排版公式
function inlineReserve() { return 0; }

function pageWidth() {
  const horizontalRoom = 72;
  return Math.max(240, Math.min(920, elements.viewer.clientWidth - inlineReserve() - horizontalRoom)) * state.zoom;
}

function snappedPageSize(ratio) {
  const pixelRatio = Math.max(window.devicePixelRatio || 1, 1);
  const targetWidth = pageWidth();
  const width = alignedCssDimension(targetWidth, pixelRatio);
  const height = alignedCssDimension(width.css * ratio, pixelRatio);
  return {
    pixelRatio,
    backingWidth: width.backing,
    backingHeight: height.backing,
    cssWidth: width.css,
    cssHeight: height.css,
  };
}

function alignedCssDimension(target, pixelRatio) {
  const center = Math.round(target * 64);
  let exact = null;
  let closest = null;
  for (let offset = -512; offset <= 512; offset += 1) {
    const css = (center + offset) / 64;
    const physical = css * pixelRatio;
    const backing = Math.round(physical);
    const pixelError = Math.abs(physical - backing);
    const distance = Math.abs(css - target);
    const candidate = { css, backing, distance, pixelError };
    if (pixelError <= 1e-7 && (!exact || distance < exact.distance)) exact = candidate;
    if (!closest || pixelError < closest.pixelError - 1e-9 || (
      Math.abs(pixelError - closest.pixelError) <= 1e-9 && distance < closest.distance
    )) closest = candidate;
  }
  return exact || closest;
}

function pageLeftMargin(size) {
  const available = elements.viewer.clientWidth - inlineReserve();
  const inset = 32;
  const fits = size.cssWidth <= available - inset * 2;
  const naturalLeft = fits ? (available - size.cssWidth) / 2 : inset;
  return Math.max(0, alignedCssDimension(naturalLeft, size.pixelRatio).css - inset);
}

function applyPageSize(wrapper, size, leftMargin = pageLeftMargin(size)) {
  wrapper.style.width = `${size.cssWidth}px`;
  wrapper.style.height = `${size.cssHeight}px`;
  wrapper.style.marginLeft = `${leftMargin}px`;
  wrapper.style.marginRight = "0";
}

function buildPlaceholders() {
  const sizes = new Map();
  const margins = new Map();
  const pages = state.revision.page_geometry.map((geometry, index) => {
    const ratio = displayedRatio(geometry);
    if (!sizes.has(ratio)) sizes.set(ratio, snappedPageSize(ratio));
    const size = sizes.get(ratio);
    if (!margins.has(size.cssWidth)) margins.set(size.cssWidth, pageLeftMargin(size));
    const page = document.createElement("article");
    page.className = "page";
    page.dataset.index = String(index);
    page.dataset.page = String(index + 1);
    applyPageSize(page, size, margins.get(size.cssWidth));
    return page;
  });
  elements.pages.replaceChildren(...pages);
  state.pagePixelCost = Math.max(...[...sizes.values()].map(size => size.backingWidth * size.backingHeight));
  state.viewerLayoutWidth = elements.viewer.clientWidth;
}

function applyRestoredPosition(offset = state.revision.position.normalized_offset || 0) {
  const page = elements.pages.children[state.currentPage];
  if (!page) return;
  elements.viewer.scrollTop = snapScroll(page.offsetTop + page.offsetHeight * offset);
  setCurrentPage(state.currentPage);
}

function scheduleViewportUpdate() {
  if (state.scrollFrame) return;
  state.scrollFrame = requestAnimationFrame(() => {
    state.scrollFrame = 0;
    updateViewport();
  });
}

// Navigation destinations and the current-identity projection share one coordinate
// authority. A page-granular PARTIAL node (trailing 习题/答案 blocks) effectively
// starts where its previous sibling's stored range ends — the outline partition
// boundary — and runs until the next sibling's known start or the parent's end.
// This projects stored authority onto nodes whose own y is not yet resolved; it
// never mints new ranges or guesses from text.
function buildOutlineKinship(nodes) {
  const byId = new Map();
  const siblings = new Map();
  for (const node of [...nodes].sort((a, b) => a.order_index - b.order_index)) {
    byId.set(node.outline_node_id, node);
    const key = node.parent_id || "ROOT";
    if (!siblings.has(key)) siblings.set(key, []);
    siblings.get(key).push(node);
  }
  const spans = new Map();
  for (const group of siblings.values()) {
    for (let i = 0; i < group.length; i += 1) {
      const node = group[i];
      if (!Number.isInteger(node.start_page)) continue;
      if (node.resolution_state === "RESOLVED" && Number.isFinite(node.start_y)
        && Number.isInteger(node.end_page) && Number.isFinite(node.end_y)) continue;
      const previous = group[i - 1];
      const startY = Number.isFinite(node.start_y) ? node.start_y
        : previous && previous.end_page === node.start_page && Number.isFinite(previous.end_y)
          ? previous.end_y : 0;
      let endPage = null, endY = null;
      for (let j = i + 1; j < group.length; j += 1) {
        if (Number.isInteger(group[j].start_page)) {
          endPage = group[j].start_page;
          endY = Number.isFinite(group[j].start_y) ? group[j].start_y : 0;
          break;
        }
      }
      if (endPage === null) {
        // The last child of a page-only parent: walk up until some ancestor has a
        // next sibling with a known start (typically the next chapter), else the
        // book end. Every node must own a span or clicks on it lose their identity.
        let ancestor = node.parent_id ? byId.get(node.parent_id) : null;
        while (ancestor && endPage === null) {
          const ancestorGroup = siblings.get(ancestor.parent_id || "ROOT") || [];
          const at = ancestorGroup.findIndex(s => s.outline_node_id === ancestor.outline_node_id);
          for (let j = at + 1; j < ancestorGroup.length && endPage === null; j += 1) {
            if (Number.isInteger(ancestorGroup[j].start_page)) {
              endPage = ancestorGroup[j].start_page;
              endY = Number.isFinite(ancestorGroup[j].start_y) ? ancestorGroup[j].start_y : 0;
            }
          }
          ancestor = ancestor.parent_id ? byId.get(ancestor.parent_id) : null;
        }
      }
      if (endPage === null && node.parent_id) {
        const parent = byId.get(node.parent_id);
        if (parent && Number.isInteger(parent.end_page)) {
          endPage = parent.end_page;
          endY = Number.isFinite(parent.end_y) ? parent.end_y : 1;
        }
      }
      if (endPage === null) { endPage = Number.POSITIVE_INFINITY; endY = 0; }
      // The next known sibling starts on the same page with no y of its own — the
      // two are indistinguishable within that page. The node claims the whole page
      // instead of an empty span; passive derivation stays ambiguous (falls to the
      // shallower level) until a navigation anchor names one of them. The refined
      // boundary y is kept for the landing target even though containment ignores it.
      const wholePage = endPage === node.start_page && endY !== null && endY <= startY + 1e-9;
      spans.set(node.outline_node_id,
        wholePage ? { wholePage: true, startPage: node.start_page, startY } : { startPage: node.start_page, startY, endPage, endY });
    }
  }
  return { byId, spans };
}

function outlineNavigationTarget(node, kinship) {
  if (!Number.isInteger(node.start_page)) return null;
  const span = kinship?.spans.get(node.outline_node_id);
  const y = Number.isFinite(node.start_y) ? node.start_y : span?.startY ?? 0;
  return { page: node.start_page, y };
}

function renderReaderSectionHint() {
  const point = captureZoomAnchor(undefined, elements.viewer.getBoundingClientRect().top + 1);
  const inRange = (startPage, startY, endPage, endY) =>
    (startPage < point.pageIndex || startPage === point.pageIndex && startY <= point.normalizedY)
    && (endPage > point.pageIndex || endPage === point.pageIndex && endY > point.normalizedY);
  // Implementation §7.5: the current item is the deepest OutlineNode whose physical
  // range contains the reading anchor — subsection when the authority is that fine,
  // Section otherwise. Uniqueness is checked per depth; an ambiguous deepest level
  // falls back to its unambiguous ancestor, never to text guessing.
  const resolvedContaining = point ? state.outlineNodes.filter(n => n.resolution_state === "RESOLVED"
    && [n.start_page,n.start_y,n.end_page,n.end_y].every(v => v !== null && v !== undefined)
    && inRange(n.start_page, n.start_y, n.end_page, n.end_y)) : [];
  // A page-granular PARTIAL node is a valid current identity over its projected
  // coarse span (previous sibling's end → next known start): the destination the
  // Directory navigates to is the very same coordinate, so click and tracking agree.
  // Siblings that share a start page with no y are indistinguishable — each claims
  // the whole page, so passively the level is ambiguous and falls to the shallower
  // ancestor; the most recent navigation anchor names the one the reader entered,
  // until the anchor leaves its span or a deeper RESOLVED range takes over.
  const spanContains = span => span.wholePage
    ? point.pageIndex === span.startPage
    : Number.isInteger(span.endPage) && inRange(span.startPage, span.startY, span.endPage, span.endY);
  const coarseContaining = point && state.outlineKinship
    ? [...state.outlineKinship.spans.entries()
        .filter(([id, span]) => spanContains(span))
        .map(([id]) => state.outlineKinship.byId.get(id))]
    : [];
  const deepestOf = list => {
    const byDepth = new Map();
    for (const node of list) {
      if (!byDepth.has(node.depth)) byDepth.set(node.depth, []);
      byDepth.get(node.depth).push(node);
    }
    for (const depth of [...byDepth.keys()].sort((a, b) => b - a)) {
      const group = byDepth.get(depth);
      if (group.length === 1) return group[0];
    }
    return null;
  };
  const deepestResolved = deepestOf(resolvedContaining);
  let deepest = deepestOf([...resolvedContaining, ...coarseContaining]) || deepestResolved;
  const anchored = state.navigationAnchorId && state.outlineKinship
    ? state.outlineKinship.byId.get(state.navigationAnchorId) : null;
  const anchoredTarget = anchored ? outlineNavigationTarget(anchored, state.outlineKinship) : null;
  const pinnedToClickedNode = anchoredTarget && state.pinnedLanding
    && state.pinnedLanding.pageIndex === anchoredTarget.page
    && Math.abs(state.pinnedLanding.normalizedY - anchoredTarget.y) < 1e-9;
  const anchoredInRange = anchored && [...resolvedContaining, ...coarseContaining]
    .some(n => n.outline_node_id === anchored.outline_node_id);
  // While the clicked destination is pinned, the clicked node owns its landing.
  // CSS-pixel rounding can put the measured top anchor just before a same-page
  // subsection boundary; user scrolling releases the pin and restores ordinary
  // range-based tracking.
  if (pinnedToClickedNode || (anchoredInRange && (!deepestResolved || deepestResolved.depth <= anchored.depth))) {
    deepest = anchored;
  }
  let section = deepestResolved?.kind === "SECTION" ? deepestResolved : null;
  if (!section && deepestResolved && deepestResolved.kind !== "CHAPTER") {
    let parent = state.outlineNodes.find(n => n.outline_node_id === deepestResolved.parent_id);
    while (parent && parent.kind !== "SECTION") parent = state.outlineNodes.find(n => n.outline_node_id === parent.parent_id);
    section = parent || null;
  }
  if (!section) {
    const sections = resolvedContaining.filter(n => n.kind === "SECTION");
    section = sections.length === 1 ? sections[0] : null;
  }
  // The derived outline identity (Implementation §7.5) drives the Directory's active
  // item; the Directory never re-derives it on its own. Section/chapter identity
  // for Knowledge Points keeps its resolved-range-only semantics.
  state.currentSectionId = section?.outline_node_id ?? null;
  state.currentOutlineId = deepest?.outline_node_id ?? null;
  let chapter = section;
  while (chapter && chapter.kind !== "CHAPTER") chapter = state.outlineNodes.find(n => n.outline_node_id === chapter.parent_id);
  if (!chapter && point) {
    const matches = state.outlineNodes.filter(n => n.kind === "CHAPTER" && n.resolution_state === "RESOLVED"
      && [n.start_page,n.start_y,n.end_page,n.end_y].every(v => v != null)
      && (n.start_page < point.pageIndex || n.start_page === point.pageIndex && n.start_y <= point.normalizedY)
      && (n.end_page > point.pageIndex || n.end_page === point.pageIndex && n.end_y > point.normalizedY));
    chapter = matches.length === 1 ? matches[0] : null;
  }
  // Page-level Outline bookmarks identify the chapter to prepare before its exact
  // physical range exists. This is navigation context, never a KP source range.
  if (!chapter && point) {
    const chapters = state.outlineNodes.filter(n => n.kind === "CHAPTER" && Number.isInteger(n.start_page))
      .sort((a,b) => a.start_page - b.start_page);
    const preceding = chapters.filter(n => n.start_page <= point.pageIndex);
    const candidate = preceding.at(-1);
    const beyondBody = candidate && state.outlineNodes.some(n => !n.parent_id && isAuxiliaryOutlineRoot(n)
      && Number.isInteger(n.start_page) && n.start_page > candidate.start_page && n.start_page <= point.pageIndex);
    if (candidate?.resolution_state === "PARTIAL"
        && !beyondBody && chapters.filter(n => n.start_page === candidate.start_page).length === 1) chapter = candidate;
  }
  const learningChapterId = chapter && !isAuxiliaryOutlineRoot(chapter) ? chapter.outline_node_id : null;
  const chapterChanged = state.learningChapterId !== learningChapterId;
  state.learningChapterId = learningChapterId;
  chapterEntry.sync(learningChapterId, section?.outline_node_id || null);
  if (!elements["outline-panel"].hidden) {
    if (state.currentOutlineId && state.currentOutlineId !== state.directoryExpandedFor) {
      // Reading moved into a possibly folded branch: expand only that path and
      // leave every other branch's user fold state untouched.
      state.directoryExpandedFor = state.currentOutlineId;
      expandDirectoryPath(state.currentOutlineId);
    }
    syncDirectoryActive();
  }
}

function updateViewport() {
  if (!state.pdf) return;
  if (state.pinnedLanding) {
    const page = elements.pages.children[state.pinnedLanding.pageIndex];
    if (page) {
      const desired = snapScroll(page.offsetTop + page.offsetHeight * state.pinnedLanding.normalizedY);
      if (Math.abs(elements.viewer.scrollTop - desired) > 0.5) {
        elements.viewer.scrollTop = desired;
        return; // The resulting scroll event re-runs the viewport update.
      }
    }
  }
  const top = elements.viewer.scrollTop;
  const bottom = top + elements.viewer.clientHeight;
  const {first, last, bestIndex} = pageViewportRange(elements.pages.children.length, index => {
    const page = elements.pages.children[index];
    const pageTop = page.offsetTop;
    const pageBottom = pageTop + page.offsetHeight + parseFloat(page.style.marginBottom || "20");
    return {top:pageTop,bottom:pageBottom};
  }, top, bottom);
  setCurrentPage(bestIndex);
  renderReaderSectionHint();
  const {start: keepStart, end: keepEnd} = pageRenderWindow(
    first ?? bestIndex, Math.max(first ?? bestIndex, last), state.pdf.numPages, state.pagePixelCost);
  const {visible, preload} = pageRenderPlan({
    first: first ?? bestIndex, last: Math.max(first ?? bestIndex, last), focus: bestIndex,
    start: keepStart, end: keepEnd, rendered: state.rendered,
  });
  // The original page on screen must finish before speculative neighbour work.
  for (const index of [...visible, ...preload]) renderPage(index);
  for (const index of new Set([...state.rendered, ...state.pageRenderRequests.keys(), ...state.pageProxies.keys()])) {
    if (index < keepStart || index > keepEnd) clearPage(index);
  }
  scheduleSave();
  schedulePreparationPriority(keepStart, keepEnd);
}

async function renderPage(index) {
  if (!state.pdf || state.rendered.has(index) || state.pageRenderRequests.has(index)) return;
  const pdf = state.pdf;
  const generation = state.generation;
  const renderGeneration = state.renderGeneration;
  const wrapper = elements.pages.children[index];
  const request = {};
  state.pageRenderRequests.set(index, request);
  const ownsPage = () => generation === state.generation && renderGeneration === state.renderGeneration
    && state.pageRenderRequests.get(index) === request && elements.pages.children[index] === wrapper;
  let task = null;
  wrapper.classList.add("loading");
  try {
    const page = await state.pdf.getPage(index + 1);
    if (!ownsPage()) { releasePageProxy(page, index, pdf); return; }
    state.pageProxies.set(index, page);
    const base = page.getViewport({ scale: 1 });
    const size = snappedPageSize(base.height / base.width);
    const viewport = page.getViewport({ scale: size.backingWidth / base.width });
    const canvas = document.createElement("canvas");
    canvas.width = size.backingWidth;
    canvas.height = size.backingHeight;
    canvas.style.width = `${size.cssWidth}px`;
    canvas.style.height = `${size.cssHeight}px`;
    canvas.dataset.outputScaleX = String(size.pixelRatio);
    canvas.dataset.outputScaleY = String(size.pixelRatio);
    applyPageSize(wrapper, size);
    const preview = wrapper.querySelector('.page-preview');
    wrapper.replaceChildren(canvas, ...(preview ? [preview] : []));
    const canvasContext = canvas.getContext("2d", { alpha: false });
    canvasContext.imageSmoothingEnabled = true;
    canvasContext.imageSmoothingQuality = "high";
    task = page.render({
      canvasContext,
      viewport,
      transform: [
        canvas.width / viewport.width,
        0,
        0,
        canvas.height / viewport.height,
        0,
        0,
      ],
      // 批注一律由应用自己的覆盖层绘制（annotation-ui）。若放行 pdf.js 的注解层，
      // 写回 PDF 的原生注解会和覆盖层各画一条，同一位置出现重复下划线/高亮
      // （外部阅读器只画原生那条，所以只有一条）。
      annotationMode: pdfjsLib.AnnotationMode.DISABLE,
    });
    state.renderTasks.set(index, task);
    await task.promise;
    if (ownsPage()) {
      state.rendered.add(index);
      wrapper.querySelector('.page-preview')?.remove();
      startReaderContext();
      scheduleViewportUpdate();
      renderGuideEntries(index);
      syncPagePreparationUi(index);
      ensureOverlay(index);
      visualSelection.render();
    }
  } catch (error) {
    if (ownsPage() && error?.name !== "RenderingCancelledException") announce(`第 ${index + 1} 页无法显示，请重试。`, true);
  } finally {
    if (ownsPage()) {
      state.pageRenderRequests.delete(index);
      state.renderTasks.delete(index);
      wrapper?.classList.remove("loading");
    }
  }
}

function clearPage(index) {
  const page = state.pageProxies.get(index), pdf = state.pdf;
  state.pageProxies.delete(index);
  state.pageRenderRequests.delete(index);
  const task = state.renderTasks.get(index);
  if (task) task.cancel();
  state.renderTasks.delete(index);
  state.rendered.delete(index);
  const wrapper = elements.pages.children[index];
  if (wrapper) {
    releasePageCanvas(wrapper.querySelector('canvas'), task);
    wrapper.replaceChildren();
    wrapper.classList.remove("loading");
  }
  if (state.selection?.pageIndex === index) clearSelection();
  releasePageProxy(page, index, pdf, task);
}

function cancelRenders() {
  for (const canvas of elements.pages.querySelectorAll('canvas')) {
    releasePageCanvas(canvas, state.renderTasks.get(Number(canvas.closest('.page').dataset.index)));
  }
  state.pageRenderRequests.clear();
  for (const task of state.renderTasks.values()) task.cancel();
  state.renderTasks.clear();
  state.rendered.clear();
}

function releasePageCanvas(canvas, task) {
  if (!canvas) return;
  const release = () => { canvas.width = 0; canvas.height = 0; };
  if (task) void task.promise.catch(() => {}).then(release);
  else release();
}

function releasePageProxy(page, index, pdf, task) {
  if (!page) return;
  // PDF.js keeps display lists and decoded images after the canvas is gone.
  // Finish cancellation first; a quick return or zoom may already reuse the page.
  void Promise.resolve(task?.promise).catch(() => {}).then(() => {
    if (state.pdf !== pdf || (!state.pageProxies.has(index) && !state.pageRenderRequests.has(index))) {
      page.cleanup();
    }
  });
}

function setCurrentPage(index) {
  state.currentPage = Math.max(0, Math.min(index, (state.revision?.page_count || 1) - 1));
  if (document.activeElement !== elements['page-number']) {
    elements["page-number"].value = String(state.currentPage + 1);
  }
  updateMarksPanel();
  updatePrintedPageLabel();
  updatePreparationLabel();
  pushNotesContext();
}

function goToPage(index, offset = 0) {
  const bounded = Math.max(0, Math.min(index, state.revision.page_count - 1));
  const page = elements.pages.children[bounded];
  // Learning footers and other async in-flow reservations shift pages after the
  // landing; the pin keeps the destination honest until the reader takes over.
  state.pinnedLanding = { pageIndex: bounded, normalizedY: offset };
  elements.viewer.scrollTop = snapScroll(page.offsetTop + page.offsetHeight * offset);
  setCurrentPage(bounded);
  scheduleViewportUpdate();
  scheduleSave();
}

function setZoom(value, anchor = captureZoomAnchor()) {
  const newZoom = Math.max(0.5, Math.min(4, Math.round(value * 100) / 100));
  if (newZoom === state.zoom || !state.revision) return;
  state.zoom = newZoom;
  elements["zoom-value"].value = `${Math.round(state.zoom * 100)}%`;
  relayoutPages(anchor);
}

function adjacentZoom(direction) {
  const tolerance = 0.001;
  if (direction > 0) return ZOOM_LEVELS.find((level) => level > state.zoom + tolerance) ?? 4;
  return [...ZOOM_LEVELS].reverse().find((level) => level < state.zoom - tolerance) ?? 0.5;
}

function fitReadingDisplay(mode) {
  if (!state.pdf) return;
  const index = state.currentPage;
  const wrapper = elements.pages.children[index];
  const availableWidth = Math.max(240, elements.viewer.clientWidth - inlineReserve() - 64);
  const width = wrapper.offsetWidth;
  let targetWidth = availableWidth;
  let bounds = [0, 1];
  if (mode === "page") {
    const ratio = displayedRatio(state.revision.page_geometry[index]);
    targetWidth = Math.min(availableWidth, Math.max(100, elements.viewer.clientHeight - 40) / ratio);
  } else if (mode === "content") {
    const canvas = wrapper.querySelector("canvas");
    if (!canvas || !state.rendered.has(index)) {announce("本页仍在显示，请稍后再试。"); return;}
    bounds = canvasContentBounds(canvas, state.overlayData.get(index)?.lines);
    if (!bounds) {announce("本页没有可识别的正文，保留原页显示。"); return;}
    targetWidth /= bounds[1] - bounds[0];
  }
  const anchor = captureZoomAnchor();
  if (anchor) {
    anchor.pageIndex = index;
    anchor.normalizedX = (bounds[0] + bounds[1]) / 2;
    anchor.viewportX = (elements.viewer.clientWidth - inlineReserve()) / 2;
    if (mode === "page") {anchor.normalizedY = 0; anchor.viewportY = 20;}
  }
  setZoom(state.zoom * targetWidth / width, anchor);
  // Re-center even when rounding/clamping leaves the zoom unchanged.
  restoreZoomAnchor(anchor);
  elements.viewer.focus({preventScroll:true});
}

createZoomEditor({input:elements["zoom-value"], getZoom:() => state.zoom, applyZoom:setZoom, viewer:elements.viewer});
const readingDisplay = createReadingDisplay({reader:elements.reader, zoomButton:document.getElementById("reading-display-toggle"), applyFit:fitReadingDisplay});

function relayoutPages(anchor = captureZoomAnchor()) {
  if (!state.revision) return;
  if (visualSelection.active) visualSelection.cancel();
  state.renderGeneration += 1;
  cancelRenders();
  state.pinnedLanding = null;
  const sizes = new Map();
  const margins = new Map();
  [...elements.pages.children].forEach((wrapper, index) => {
    const ratio = displayedRatio(state.revision.page_geometry[index]);
    if (!sizes.has(ratio)) sizes.set(ratio, snappedPageSize(ratio));
    const size = sizes.get(ratio);
    if (!margins.has(size.cssWidth)) margins.set(size.cssWidth, pageLeftMargin(size));
    if (wrapper.firstChild) wrapper.replaceChildren();
    wrapper.classList.remove("loading");
    applyPageSize(wrapper, size, margins.get(size.cssWidth));
  });
  state.pagePixelCost = Math.max(...[...sizes.values()].map(size => size.backingWidth * size.backingHeight));
  state.viewerLayoutWidth = elements.viewer.clientWidth;
  // The initial original page can already be visible while PDF.js loads.
  restoreZoomAnchor(anchor);
  scheduleViewportUpdate();
  scheduleSave();
  evaluateDirectoryMode();
}

function captureZoomAnchor(clientX, clientY) {
  if (!state.revision || !elements.pages.children.length) return null;
  const viewerRect = elements.viewer.getBoundingClientRect();
  const x = clientX ?? (viewerRect.left + viewerRect.width / 2);
  const y = clientY ?? (viewerRect.top + viewerRect.height / 2);
  const contentY = elements.viewer.scrollTop + y - viewerRect.top;
  const pages = elements.pages.children;
  let low = 0, high = pages.length - 1, bestPage = null;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const page = pages[middle];
    const top = page.offsetTop, bottom = top + page.offsetHeight;
    if (contentY < top) high = middle - 1;
    else if (contentY > bottom) low = middle + 1;
    else { bestPage = page; break; }
  }
  if (!bestPage) {
    const before = pages[Math.max(0, high)];
    const after = pages[Math.min(pages.length - 1, low)];
    const beforeDistance = before ? Math.max(0, contentY - before.offsetTop - before.offsetHeight) : Infinity;
    const afterDistance = after ? Math.max(0, after.offsetTop - contentY) : Infinity;
    bestPage = beforeDistance <= afterDistance ? before : after;
  }
  if (!bestPage) return null;
  const pageRect = bestPage.getBoundingClientRect();
  return {
    pageIndex: Number(bestPage.dataset.index),
    normalizedX: Math.max(0, Math.min(1, (x - pageRect.left) / pageRect.width)),
    normalizedY: Math.max(0, Math.min(1, (y - pageRect.top) / pageRect.height)),
    viewportX: x - viewerRect.left,
    viewportY: y - viewerRect.top,
  };
}

function restoreZoomAnchor(anchor) {
  if (!anchor) return;
  const page = elements.pages.children[anchor.pageIndex];
  if (!page) return;
  const viewerRect = elements.viewer.getBoundingClientRect();
  const pageRect = page.getBoundingClientRect();
  const pointX = pageRect.left + anchor.normalizedX * pageRect.width;
  const pointY = pageRect.top + anchor.normalizedY * pageRect.height;
  elements.viewer.scrollLeft = snapScroll(
    elements.viewer.scrollLeft + pointX - (viewerRect.left + anchor.viewportX),
  );
  elements.viewer.scrollTop = snapScroll(
    elements.viewer.scrollTop + pointY - (viewerRect.top + anchor.viewportY),
  );
  setCurrentPage(anchor.pageIndex);
}

function snapScroll(value) {
  const pixelRatio = Math.max(window.devicePixelRatio || 1, 1);
  return alignedCssDimension(value, pixelRatio).css;
}

function scheduleSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(savePosition, 350);
}

function readingZoomForSave() { return state.zoom; }

async function savePosition() {
  if (!state.revision || !state.pdf) return;
  const page = elements.pages.children[state.currentPage];
  if (!page) return;
  const normalizedOffset = currentNormalizedOffset(page);
  const zoom = readingZoomForSave();
  try {
    await api(`/api/revisions/${state.revision.id}/position`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pdf_page_index: state.currentPage, normalized_offset: normalizedOffset, zoom }),
    });
    state.revision.position = { pdf_page_index: state.currentPage, normalized_offset: normalizedOffset, zoom, updated_at: new Date().toISOString() };
  } catch (error) {
    announce("阅读位置未保存，请稍后重试。", true);
  }
}

function savePositionKeepalive() {
  if (!state.revision || !state.pdf) return;
  const page = elements.pages.children[state.currentPage];
  if (!page) return;
  fetch(`/api/revisions/${state.revision.id}/position`, {
    method: "PUT",
    credentials: "same-origin",
    headers: { "X-Reader-Token": launchToken, "Content-Type": "application/json" },
    body: JSON.stringify({ pdf_page_index: state.currentPage, normalized_offset: currentNormalizedOffset(page), zoom: readingZoomForSave() }),
    keepalive: true,
  }).catch(() => {});
}

function currentNormalizedOffset(page) {
  return Math.max(0, Math.min(1, (elements.viewer.scrollTop - page.offsetTop) / page.offsetHeight));
}

function preparationFinished(pages, activeJobs = false) {
  return pages.length > 0 && !activeJobs && pages.every(p => ["READY", "FAILED"].includes(p.status));
}

function schedulePreparationReconnect(revisionId, generation) {
  if (state.revision?.id !== revisionId || state.generation !== generation) return;
  clearTimeout(state.preparationRetryTimer);
  const delay = state.preparationRetryDelay;
  state.preparationRetryDelay = Math.min(delay * 2, 10000);
  state.preparationRetryTimer = setTimeout(() => {
    state.preparationRetryTimer = 0;
    if (state.revision?.id === revisionId && state.generation === generation) startPreparation(false);
  }, delay);
}

async function startPreparation(schedule = true) {
  if (!state.revision) return;
  const revisionId = state.revision.id;
  const generation = state.generation;
  const current = () => state.revision?.id === revisionId && state.generation === generation;
  clearTimeout(state.preparationRetryTimer);
  state.preparationRetryTimer = 0;
  try {
    const saved = await api(`/api/revisions/${revisionId}/preparation`);
    if (!current()) return;
    const recoveringStatus = !!elements["preparation-status"].onclick;
    if (saved.ocr) state.ocrBanner?.update(saved.ocr);
    applyPreparationStatuses(saved.pages);
    if (recoveringStatus) updatePreparationLabel();
    if (preparationFinished(saved.pages, (saved.jobs?.QUEUED || 0) + (saved.jobs?.RUNNING || 0) > 0)) {
      return;
    }
    if (schedule && saved.pages.some(p => !["READY", "FAILED"].includes(p.status))) await api(`/api/revisions/${revisionId}/preparation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_page: state.currentPage, visible_pages: [state.currentPage] }),
      signal: AbortSignal.timeout(15000),
    });
  } catch (error) {
    if (!current()) return;
    elements["preparation-status"].textContent = "文字状态读取失败，点击重试";
    elements["preparation-status"].onclick = () => startPreparation();
    elements["preparation-status"].className = "preparation-status failed";
    if (!schedule) schedulePreparationReconnect(revisionId, generation);
    return;
  }
  if (!current()) return;
  state.eventSource?.close();
  const stream = new EventSource(`/api/revisions/${revisionId}/preparation/events`, { withCredentials: true });
  state.eventSource = stream;
  stream.addEventListener("pages", (event) => {
    if (!current() || state.eventSource !== stream) return;
    const payload = JSON.parse(event.data);
    state.preparationRetryDelay = 1000;
    if (payload.ocr) state.ocrBanner?.update(payload.ocr);
    applyPreparationStatuses(payload.pages);
    if (preparationFinished(payload.pages, payload.active_jobs)) {
      stream.close();
      if (state.eventSource === stream) state.eventSource = null;
    }
  });
  stream.onerror = () => {
    if (!current() || state.eventSource !== stream) return;
    stream.close();
    state.eventSource = null;
    updatePreparationLabel();
    schedulePreparationReconnect(revisionId, generation);
  };
}

function closePreparationStream() {
  state.eventSource?.close();
  state.eventSource = null;
  clearTimeout(state.preparationRetryTimer);
  state.preparationRetryTimer = 0;
  state.preparationRetryDelay = 1000;
  state.preparation.clear();
  state.overlayData.clear();
  state.annotationData.clear();
  clearTimeout(state.priorityTimer);
  elements["preparation-status"].textContent = "正在读取已保存文字层…";
  elements["preparation-status"].onclick = null;
  elements["preparation-status"].className = "preparation-status";
}

function applyPreparationStatuses(pages) {
  elements["preparation-status"].onclick = null;
  let updated = false;
  for (const page of pages) {
    const previous = state.preparation.get(page.pdf_page_index);
    if (previous?.status === page.status && previous?.prepared_at === page.prepared_at
        && previous?.failure_code === page.failure_code) continue;
    updated = true;
    state.preparation.set(page.pdf_page_index, page);
    const wrapper = elements.pages.children[page.pdf_page_index];
    if (wrapper) {
      wrapper.dataset.preparation = page.status;
      syncPagePreparationUi(page.pdf_page_index);
    }
    if (page.status === "READY" && previous?.status !== "READY") ensureOverlay(page.pdf_page_index);
  }
  if (updated) updatePreparationLabel();
}

function coverageText(coverage) {
  const failed = coverage.statuses.FAILED || 0;
  if (coverage.complete) return `已检索全书 ${coverage.total_pages} 页`;
  const suffix = failed ? `，其中 ${failed} 页准备失败` : "";
  return `已检索 ${coverage.ready_pages} / ${coverage.total_pages} 页，其余页面仍在准备${suffix}`;
}

async function loadBookMap() {
  const revisionId = state.revision?.id;
  if (!revisionId) return;
  const request = ++state.outlineRequest;
  elements["outline-status"].hidden = false;
  elements["outline-status"].textContent = "正在读取教材目录…";
  try {
    let outline = await api(`/api/revisions/${revisionId}/outline?stored=1`);
    if (!outline.evidence_source) outline = await api(`/api/revisions/${revisionId}/outline`);
    const labels = outline.page_labels;
    if (request !== state.outlineRequest || state.revision?.id !== revisionId) return;
    state.outlineNodes = outline.nodes;
    state.outlineKinship = buildOutlineKinship(outline.nodes);
    for (const index of state.rendered) renderGuideEntries(index);
    state.pageLabels = new Map(labels.labels.map((row) => [row.pdf_page_index, row]));
    renderOutline(outline);
    updatePrintedPageLabel();
  } catch (_error) {
    if (request !== state.outlineRequest) return;
    elements["outline-status"].hidden = false;
    elements["outline-status"].textContent = "目录暂时不可用，可以稍后重试。";
    elements["outline-empty"].hidden = false;
    updatePrintedPageLabel();
  }
}

function renderGuideEntries(index) {
  const page = elements.pages.children[index];
  if (!page) return;
  page.querySelectorAll(".section-guide-entry").forEach(button => button.remove());
  for (const node of state.outlineNodes) {
    if (node.kind !== "SECTION" || node.resolution_state !== "RESOLVED"
        || node.start_page !== index || !Number.isFinite(node.start_y)) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "section-guide-entry";
    button.dataset.sectionId = node.outline_node_id;
    button.style.top = `${node.start_y * 100}%`;
    button.textContent = "导读 ›";
    button.setAttribute("aria-label", `${node.title} · 导读`);
    button.addEventListener("click", () => guide.open(node.outline_node_id));
    page.append(button);
  }
}

function computeDirectoryMode({ viewerWidth, pageCssWidth, inlineReserve = 0, splitActive = false,
    paneWidth = DIRECTORY_PANE_WIDTH, gap = DIRECTORY_GAP }) {
  // All geometry is expressed against the un-split viewer so re-evaluations inside
  // split mode compare like with like. The page-width formula mirrors pageWidth()
  // exactly; a mode is only chosen when the rendered page width cannot change.
  const unsplit = viewerWidth + (splitActive ? paneWidth + gap : 0);
  const base = Math.max(240, Math.min(920, unsplit - inlineReserve - 72));
  const pageLeft = Math.max(32, (unsplit - inlineReserve - pageCssWidth) / 2);
  if (pageLeft >= paneWidth + gap) return "margin";
  const narrowed = unsplit - paneWidth - gap;
  const narrowedBase = Math.max(240, Math.min(920, narrowed - inlineReserve - 72));
  const fitsNarrowed = pageCssWidth <= narrowed - inlineReserve - 64;
  if (narrowedBase === base && fitsNarrowed) return "split";
  return "navigation";
}

function evaluateDirectoryMode() {
  if (!state.revision || !elements.pages.children.length) return;
  if (elements.reader.classList.contains("portrait-mode")) { applyDirectoryMode("portrait"); return; }
  if (elements["outline-panel"].hidden) return;
  applyDirectoryMode(computeDirectoryMode({
    viewerWidth: elements.viewer.clientWidth,
    pageCssWidth: elements.pages.children[0].offsetWidth,
    inlineReserve: inlineReserve(),
    splitActive: state.directoryMode === "split",
    paneWidth: DIRECTORY_PANE_WIDTH,
  }));
}

function applyDirectoryMode(mode) {
  if (state.directoryMode === mode) return;
  const wasSplit = state.directoryMode === "split";
  state.directoryMode = mode;
  elements.reader.classList.toggle("directory-split", mode === "split");
  elements.reader.classList.toggle("directory-margin", mode === "margin");
  elements.reader.classList.toggle("directory-nav", mode === "navigation");
  if (wasSplit && mode !== "split" && state.revision) {
    // Split keeps page sizes fixed and recenters via CSS margins; on exit the inline
    // margins may be stale from the narrowed stage, so re-center for full width.
    [...elements.pages.children].forEach((wrapper, index) => {
      applyPageSize(wrapper, snappedPageSize(displayedRatio(state.revision.page_geometry[index])));
    });
  }
}

let directoryActiveSnapshot = null;
function syncDirectoryActive({ reveal = false } = {}) {
  // The deepest authoritative outline item owns the highlight; its Section/Chapter
  // ancestors stay plain (the chapter is only a last-resort fallback).
  const activeId = state.currentOutlineId || state.learningChapterId;
  if (!activeId) return;
  const tree = elements["outline-tree"];
  // The reading point changes every scroll frame, but its directory identity
  // usually does not. A replaced tree or an explicit reveal still refreshes it.
  if (!reveal && directoryActiveSnapshot?.generation === state.generation
      && directoryActiveSnapshot.activeId === activeId
      && directoryActiveSnapshot.target?.isConnected
      && tree.contains(directoryActiveSnapshot.target)) return;
  let active = null;
  for (const target of tree.querySelectorAll(".outline-target")) {
    const isActive = target.closest("li")?.dataset.nodeId === activeId;
    target.classList.toggle("is-active", isActive);
    if (isActive) {
      target.setAttribute("aria-current", "true");
      active = target;
    } else {
      target.removeAttribute("aria-current");
    }
  }
  directoryActiveSnapshot = { generation: state.generation, activeId, target: active };
  active?.scrollIntoView({ block: reveal ? "center" : "nearest", inline: "nearest" });
}

function openDirectory() {
  if (elements["outline-panel"].hidden) {
    elements["outline-panel"].hidden = false;
    elements["outline-toggle"].setAttribute("aria-expanded", "true");
    chapterEntry.close();
    elements["search-panel"].hidden = true;
    elements["marks-panel"].hidden = true;
    elements["search-toggle"].setAttribute("aria-expanded", "false");
    elements["marks-toggle"].setAttribute("aria-expanded", "false");
    state.searchRequest += 1;
    clearSearchMatch();
    evaluateDirectoryMode();
  }
  loadBookMap();
  syncDirectoryActive({ reveal: true });
}

function closeDirectory({ focusViewer = false } = {}) {
  if (elements["outline-panel"].hidden) return;
  const pinnedAnchor = elements.reader.classList.contains("portrait-mode") && elements.reader.classList.contains("portrait-directory-pinned")
    ? capturePortraitAnchor() : null;
  portraitLayout.directoryClosed();
  elements["outline-panel"].hidden = true;
  elements["outline-toggle"].setAttribute("aria-expanded", "false");
  applyDirectoryMode(null);
  if (pinnedAnchor && state.pdf) relayoutPages(pinnedAnchor);
  if (focusViewer) elements.viewer.focus({ preventScroll: true });
}

function isOutlineAncestor(ancestorId, nodeId) {
  if (!state.outlineKinship) return false;
  let current = state.outlineKinship.byId.get(nodeId);
  while (current) {
    if (current.outline_node_id === ancestorId) return true;
    current = current.parent_id ? state.outlineKinship.byId.get(current.parent_id) : null;
  }
  return false;
}

// Progressive collapse: nodes with real child OutlineNodes can fold. The collapse
// set lives for the Reader session (per book); the current authoritative reading
// path is always kept expanded, and entering a previously collapsed branch expands
// only that path without disturbing other branches' user state.
function expandDirectoryPath(nodeId) {
  if (!state.directoryCollapsed || !state.outlineKinship || !nodeId) return;
  let current = state.outlineKinship.byId.get(nodeId);
  while (current) {
    if (state.directoryCollapsed.delete(current.outline_node_id)) {
      const item = elements["outline-tree"].querySelector(`li[data-node-id="${current.outline_node_id}"]`);
      const list = item?.querySelector(":scope > ul");
      if (list) {
        list.hidden = false;
        const fold = item.querySelector(":scope > .outline-row .outline-fold");
        if (fold) {
          fold.textContent = "收起";
          fold.setAttribute("aria-expanded", "true");
        }
      }
    }
    current = current.parent_id ? state.outlineKinship.byId.get(current.parent_id) : null;
  }
}

function renderOutline(payload) {
  renderReaderSectionHint();
  const nodes = payload.nodes || [];
  const children = new Map();
  for (const node of nodes) {
    const key = node.parent_id || "ROOT";
    if (!children.has(key)) children.set(key, []);
    children.get(key).push(node);
  }
  for (const values of children.values()) {
    values.sort((a, b) => a.order_index - b.order_index);
  }

  // First render of a book session: chapters outside the current reading path
  // start collapsed; everything deeper defaults to expanded until the user folds it.
  if (!state.directoryCollapsed) {
    const collapsed = new Set();
    const keep = new Set();
    const anchorId = state.currentOutlineId || state.learningChapterId;
    for (let n = anchorId && state.outlineKinship?.byId.get(anchorId);
         n; n = n.parent_id ? state.outlineKinship.byId.get(n.parent_id) : null) {
      keep.add(n.outline_node_id);
    }
    for (const node of nodes) {
      if (node.kind === "CHAPTER" && !keep.has(node.outline_node_id)) collapsed.add(node.outline_node_id);
    }
    state.directoryCollapsed = collapsed;
  }

  function branch(values, depth) {
    const list = document.createElement("ul");
    list.className = `outline-level outline-level-${depth}`;
    for (const node of values) {
      const item = document.createElement("li");
      item.dataset.nodeId = node.outline_node_id;
      const row = document.createElement("div");
      row.className = "outline-row";
      const target = document.createElement("button");
      target.type = "button";
      target.className = "outline-target";
      target.disabled = node.start_page === null;
      const title = document.createElement("span");
      title.textContent = node.title;
      target.append(title);
      if (node.source === "PDF_BOOKMARK") {
        const badge = document.createElement("small");
        badge.className = "outline-bookmark";
        badge.textContent = "书签";
        badge.title = "来自 PDF 书签";
        target.append(badge);
      }
      if (node.start_page === null) {
        const meta = document.createElement("small");
        meta.textContent = "页码待核实";
        target.append(meta);
      } else {
        target.title = `PDF 第 ${node.start_page + 1} 页`;
      }
      target.addEventListener("click", () => {
        if (node.start_page === null) return;
        // The landing anchor uses the same projected coordinate the current-identity
        // derivation tests against, so the clicked node becomes — and stays — the
        // authoritative current item for as long as the reading anchor is in its range.
        state.navigationAnchorId = node.outline_node_id;
        const destination = outlineNavigationTarget(node, state.outlineKinship);
        goToPage(destination.page, destination.y);
        renderReaderSectionHint();
        if (state.directoryMode === "navigation") closeDirectory({ focusViewer: true });
        else elements.viewer.focus({ preventScroll: true });
      });
      row.append(target);
      item.append(row);
      const descendants = children.get(node.outline_node_id) || [];
      if (descendants.length) {
        const nested = branch(descendants, depth + 1);
        nested.id = `outline-children-${node.outline_node_id}`;
        const expanded = !state.directoryCollapsed.has(node.outline_node_id);
        nested.hidden = !expanded;
        // Editorial text affordance: no triangles, no persistent icon — a low-weight
        // "展开/收起" label surfaces at the row end on hover or keyboard focus, and
        // folding only changes child visibility, never the PDF position.
        const fold = document.createElement("button");
        fold.type = "button";
        fold.className = "outline-fold";
        fold.textContent = expanded ? "收起" : "展开";
        fold.setAttribute("aria-expanded", String(expanded));
        fold.setAttribute("aria-controls", nested.id);
        fold.addEventListener("click", () => {
          const opening = nested.hidden;
          // The authoritative reading path must never fold away its current item.
          const currentId = state.currentOutlineId || state.learningChapterId;
          if (!opening && currentId && isOutlineAncestor(node.outline_node_id, currentId)) return;
          nested.hidden = !opening;
          if (opening) state.directoryCollapsed.delete(node.outline_node_id);
          else state.directoryCollapsed.add(node.outline_node_id);
          fold.textContent = opening ? "收起" : "展开";
          fold.setAttribute("aria-expanded", String(opening));
        });
        row.append(fold);
        item.append(nested);
      }
      list.append(item);
    }
    return list;
  }

  const roots = children.get("ROOT") || [];
  const otherRoots = roots.filter(isAuxiliaryOutlineRoot);
  const mainRoots = roots.filter((node) => !isAuxiliaryOutlineRoot(node));
  const main = branch(mainRoots, 0);
  if (otherRoots.length) main.append(makeAuxiliaryOutlineGroup(otherRoots, branch));
  elements["outline-tree"].replaceChildren(main);
  elements["outline-empty"].hidden = nodes.length > 0;
  const status = elements["outline-status"];
  if (payload.identity_conflict) {
    // Reconciliation detail stays in internal logs, not the reading UI.
    status.hidden = true;
    console.info("[directory] identity conflict guarded; serving the saved stable tree.",
      { nodes: nodes.length, evidence_source: payload.evidence_source });
  } else if (nodes.length) {
    status.hidden = true;
  } else if (payload.waiting_for_toc_completion) {
    status.hidden = false;
    status.textContent = "已发现目录页，正在等待连续目录页准备完成。";
  } else {
    status.hidden = false;
    status.textContent = "未发现可用的 PDF 书签或已准备目录页。";
  }
  const importButton = elements["outline-import"];
  if (importButton) {
    const hasBookmarks = nodes.some((node) => node.source === "PDF_BOOKMARK");
    importButton.hidden = hasBookmarks;
    importButton.disabled = false;
    importButton.textContent = "导入 PDF 书签";
  }
  // The current reading path must be expanded before the active item is revealed.
  state.directoryExpandedFor = state.currentOutlineId || state.learningChapterId || null;
  expandDirectoryPath(state.directoryExpandedFor);
  syncDirectoryActive({ reveal: true });
}

function isAuxiliaryOutlineRoot(node) {
  const title = node.title.normalize("NFKC").replace(/[\s·•:：—_\-]/g, "");
  return /^(?:封面|书名|扉页|版权(?:页|信息)?|本书配套资源介绍|配套资源介绍|前言|序言|序|致读者|王道训练营|目录|目次|(?:主要)?参考(?:文献|资料)|索引|(?:再版|第[一二三四五六七八九十0-9]+版)?后记|附录.*)$/.test(title);
}

function makeAuxiliaryOutlineGroup(nodes, branch) {
  const item = document.createElement("li");
  item.className = "outline-other-group";
  item.dataset.outlineGroup = "other";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "outline-other-toggle";
  toggle.setAttribute("aria-expanded", "false");
  toggle.innerHTML = `<span aria-hidden="true">▸</span><strong>其他内容</strong><small>${nodes.length} 项</small>`;
  const nested = branch(nodes, 1);
  nested.hidden = true;
  toggle.addEventListener("click", () => {
    const opening = nested.hidden;
    nested.hidden = !opening;
    toggle.querySelector("span").textContent = opening ? "▾" : "▸";
    toggle.setAttribute("aria-expanded", String(opening));
  });
  item.append(toggle, nested);
  return item;
}

function updatePrintedPageLabel() {
  // Page labels remain internal evidence; the toolbar's page input is PDF-based.
  elements["printed-page-edit"].hidden = true;
}

async function editPrintedPageLabel() {
  if (!state.revision) return;
  const current = state.pageLabels.get(state.currentPage)?.printed_label || "";
  const value = window.prompt(`设置 PDF 第 ${state.currentPage + 1} 页对应的印刷页码`, current);
  if (value === null) return;
  const printedLabel = value.trim();
  if (!printedLabel) {
    announce("印刷页码不能为空；未知页会保持“印刷页未知”。", true);
    return;
  }
  try {
    await api(`/api/revisions/${state.revision.id}/page-labels/${state.currentPage}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ printed_label: printedLabel }),
    });
    await loadBookMap();
    announce("本页印刷页码已手工保存。");
  } catch (_error) {
    announce("印刷页码未保存，请重试。", true);
  }
}

async function runSearch() {
  const revisionId = state.revision?.id;
  if (!revisionId) return;
  const request = ++state.searchRequest;
  const queryText = elements["search-query"].value.trim();
  clearSearchMatch();
  elements["search-coverage"].textContent = "正在读取搜索范围…";
  elements["search-empty"].textContent = queryText ? "正在搜索…" : "输入关键词以搜索已准备页面。";
  elements["search-empty"].hidden = false;
  elements["search-results"].replaceChildren();
  try {
    const payload = await api(`/api/revisions/${revisionId}/search?q=${encodeURIComponent(queryText)}`);
    if (request !== state.searchRequest || state.revision?.id !== revisionId) return;
    elements["search-coverage"].textContent = coverageText(payload.coverage);
    const rows = payload.results.map((result) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "search-result";
      const page = document.createElement("strong");
      page.textContent = `PDF 第 ${result.pdf_page_index + 1} 页`;
      const snippet = document.createElement("span");
      snippet.textContent = result.snippet;
      button.append(page, snippet);
      button.addEventListener("click", () => activateSearchResult(result));
      return button;
    });
    elements["search-results"].replaceChildren(...rows);
    elements["search-empty"].hidden = rows.length > 0;
    if (!rows.length) {
      elements["search-empty"].textContent = queryText ? "在当前已检索页面中没有找到结果。" : "输入关键词以搜索已准备页面。";
    }
    if (payload.truncated) announce("结果较多，当前显示前 100 个匹配页面。");
  } catch (_error) {
    if (request !== state.searchRequest) return;
    elements["search-coverage"].textContent = "暂时无法读取搜索范围";
    elements["search-empty"].textContent = "搜索失败，请稍后重试。";
  }
}

function clearSearchMatch() {
  document.querySelectorAll(".search-match-quad").forEach((node) => node.remove());
  state.searchMatch = null;
}

function activateSearchResult(result) {
  clearSearchMatch();
  state.searchMatch = {
    pageIndex: result.pdf_page_index,
    ranges: result.match_ranges || [],
    focusPending: true,
  };
  goToPage(result.pdf_page_index);
  renderSearchMatch(
    result.pdf_page_index,
    elements.pages.children[result.pdf_page_index]?.querySelector(".text-overlay"),
  );
}

function renderSearchMatch(index, overlay = elements.pages.children[index]?.querySelector(".text-overlay")) {
  const match = state.searchMatch;
  const data = state.overlayData.get(index);
  if (!overlay || !data || match?.pageIndex !== index) return;
  overlay.querySelectorAll(".search-match-quad").forEach((node) => node.remove());
  const resolved = match.ranges.flatMap((range) => resolveSelection(
    data.lines,
    { lineOrdinal: range.line_ordinal, boundary: range.cell_start },
    { lineOrdinal: range.line_ordinal, boundary: range.cell_end },
  ));
  let firstMarker = null;
  for (const quad of selectionPresentationQuads(resolved)) {
    const xs = quad.map(([x]) => x);
    const ys = quad.map(([, y]) => y);
    const marker = document.createElement("span");
    marker.className = "search-match-quad";
    marker.style.cssText = `left:${Math.min(...xs) * 100}%;top:${Math.min(...ys) * 100}%;width:${(Math.max(...xs) - Math.min(...xs)) * 100}%;height:${(Math.max(...ys) - Math.min(...ys)) * 100}%`;
    overlay.append(marker);
    firstMarker ||= marker;
  }
  if (firstMarker && match.focusPending) {
    match.focusPending = false;
    requestAnimationFrame(() => firstMarker.isConnected && firstMarker.scrollIntoView({ block: "center", inline: "nearest" }));
  }
}

function syncPagePreparationUi(index) {
  const wrapper = elements.pages.children[index];
  if (!wrapper) return;
  wrapper.querySelector(".preparation-retry:not(.overlay-retry)")?.remove();
  if (state.preparation.get(index)?.status !== "FAILED" || !wrapper.querySelector("canvas")) return;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "preparation-retry";
  retry.textContent = "文字准备失败 · 重试";
  retry.addEventListener("click", async (event) => {
    event.stopPropagation();
    const revisionId = state.revision?.id;
    if (!revisionId) return;
    retry.disabled = true;
    retry.textContent = "已加入重试队列……";
    try {
      await api(`/api/revisions/${revisionId}/preparation/retry`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pdf_page_index: index }),
      });
      if (state.revision?.id !== revisionId) return;
      applyPreparationStatuses([{
        ...state.preparation.get(index), status: "NOT_PREPARED", failure_code: null,
      }]);
      startPreparation(false);
    } catch (_error) {
      if (state.revision?.id !== revisionId) return;
      retry.disabled = false;
      retry.textContent = "文字准备失败 · 重试";
      announce("重试未能加入队列，请稍后再试。", true);
    }
  });
  wrapper.append(retry);
}

function updatePreparationLabel() {
  const pages = [...state.preparation.values()];
  const ready = pages.filter((page) => page.status === "READY").length;
  const failed = pages.filter((page) => page.status === "FAILED").length;
  const output = elements["preparation-status"];
  let text, title, className;
  if (!pages.length) {
    text = "正在读取已保存文字层…";
    className = "preparation-status";
  } else if (ready + failed === pages.length) {
    text = failed ? `${ready} 页文字层 · ${failed} 页失败 · 原书为准` : "文字层已生成";
    title = "OCR 处理完成不代表全文识别无误；可能存在漏字、错字，请以原 PDF 为准。";
    className = `preparation-status ${failed ? "failed" : "ready"}`;
  } else {
    const current = state.preparation.get(state.currentPage)?.status;
    const pageLabel = current === "READY" ? "本页可选"
      : current === "PREPARING" ? "本页识别中"
      : current === "FAILED" ? "本页文字失败" : "本页待识别";
    text = `${pageLabel} · ${ready}/${pages.length} 页${current === "READY" ? " · 原书为准" : ""}`;
    title = "原 PDF 可继续阅读；文字层优先准备当前页。OCR 可能漏字、错字，请以原书为准。";
    className = `preparation-status ${current === "FAILED" ? "failed" : ""}`;
  }
  // Avoid replacing unchanged toolbar text before viewport geometry is read.
  // Read the actual element so reconnect/retry messages can recover immediately.
  if (output.textContent !== text) output.textContent = text;
  if (title !== undefined && output.title !== title) output.title = title;
  if (output.className !== className) output.className = className;
}

function schedulePreparationPriority(first, last) {
  if (!state.revision) return;
  clearTimeout(state.priorityTimer);
  if (state.preparation.size === state.revision.page_count
      && [...state.preparation.values()].every(p => ["READY", "FAILED"].includes(p.status))) return;
  const revisionId = state.revision.id;
  state.priorityTimer = setTimeout(() => {
    if (state.revision?.id !== revisionId) return;
    const visiblePages = [];
    for (let index = first; index <= last; index += 1) visiblePages.push(index);
    api(`/api/revisions/${revisionId}/preparation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_page: state.currentPage, visible_pages: visiblePages }),
      signal: AbortSignal.timeout(10000),
    }).catch(() => {});
  }, 180);
}

async function refreshAssistantStatus() {
  clearTimeout(state.assistantStatusTimer);
  try {
    const status = await api("/api/assistant/status");
    state.assistantStatusAvailable = true;
    state.assistantProviderStatuses = Array.isArray(status.providers) ? status.providers : [];
    if (!state.assistantModelSelectionInitialized) {
      const fallback = state.assistantProviderStatuses.find(providerSelectable)?.provider
        || state.assistantProviderStatuses[0]?.provider || "deepseek";
      state.assistantActiveProvider = status.active_provider || status.provider || fallback;
      state.assistantModelSelectionInitialized = true;
    }
    applySelectedProviderStatus();
    if (state.assistantCooling) {
      state.assistantStatusTimer = setTimeout(
        refreshAssistantStatus,
        Math.max(1000, (Number(status.retry_after_seconds || 1) + 0.25) * 1000),
      );
    }
  } catch (_error) {
    state.assistantStatusAvailable = false;
    state.assistantConfigured = false;
    state.assistantCooling = false;
    state.assistantOffReason = "STATUS_UNAVAILABLE";
    state.assistantProviderStatuses = [];
    assistantProviderOptionKey = "";
    syncModelSelector();
    syncAskEligibility();
  }
}

function selectedProviderStatus() {
  return state.assistantProviderStatuses.find(
    (provider) => provider.provider === state.assistantActiveProvider
  ) || null;
}

function providerSelectable(provider) {
  return Boolean(
    provider
    && provider.provider
    && provider.configuration_valid
    && !["DEVELOPMENT_DISABLED", "UNSUPPORTED_PLATFORM"].includes(provider.ai_off_reason)
  );
}

// Provider options come from /api/assistant/status; the static markup only covers the
// window before the first status response.
function providerOffTitle(status) {
  const name = providerDisplayName(status.provider, status);
  switch (status.ai_off_reason) {
    case "INVALID_CONFIGURATION":
      return `${name} 尚未配置完整（endpoint / model / 凭据）。`;
    case "CREDENTIAL_NOT_FOUND":
      return `${name} 缺少 Windows 凭据${status.credential_target ? `“${status.credential_target}”` : ""}。`;
    case "CREDENTIAL_EMPTY":
      return `${name} 的 Windows 凭据为空。`;
    case "CREDENTIAL_READ_FAILED":
      return `${name} 的 Windows 凭据读取失败。`;
    case "CREDENTIAL_UNAVAILABLE":
      return `${name} 的凭据不可用（Windows 凭据与环境变量均不可读）。`;
    case "UNSUPPORTED_PLATFORM":
      return `${name} 在当前系统不可用：需要 Windows 凭据或环境变量中的 API Key。`;
    case "DEVELOPMENT_DISABLED":
      return `${name} 已在当前开发配置中关闭。`;
    case "COOLING":
      return `${name} 正在短暂冷却，稍后自动恢复。`;
    default:
      return `${name} 当前不可调用。`;
  }
}

function renderProviderOptions() {
  const select = elements["assistant-model"];
  const statuses = state.assistantProviderStatuses;
  if (!select || !statuses.length) return;
  const key = statuses.map(
    (provider) => `${provider.provider}|${provider.display_name || ""}|${provider.model || ""}`
  ).join(";");
  if (key !== assistantProviderOptionKey) {
    const known = statuses.some((provider) => provider.provider === state.assistantActiveProvider);
    if (!known) {
      const fallback = statuses.find(providerSelectable) || statuses[0];
      if (fallback) state.assistantActiveProvider = fallback.provider;
    }
    select.replaceChildren(...statuses.map((status) => {
      const option = document.createElement("option");
      option.value = status.provider;
      option.textContent = providerOptionLabel(status);
      if (status.ai_off_reason) option.title = providerOffTitle(status);
      return option;
    }));
    assistantProviderOptionKey = key;
  }
  select.value = state.assistantActiveProvider;
}

function syncModelSelector() {
  renderProviderOptions();
  elements["assistant-model"].value = state.assistantActiveProvider;
  elements["assistant-model"].closest('.assistant-model').dataset.shortName =
    providerDisplayName(state.assistantActiveProvider, selectedProviderStatus());
  const rootLocked = Boolean(state.assistantState.current) && !state.assistantDraft;
  elements["assistant-model"].disabled = rootLocked || state.assistantPending;
  elements["assistant-model-lock"].hidden = !rootLocked;
  elements["assistant-model"].title = rootLocked
    ? "当前解释树已固定使用此模型；从教材新选区开始时可重新选择"
    : "选择下一条新解释使用的模型";
  assistantModelMenu.sync();
  syncFirstTurnEligibility();
}

function applySelectedProviderStatus() {
  const selected = selectedProviderStatus();
  state.assistantConfigured = Boolean(selected?.configured);
  state.assistantCooling = Boolean(selected?.cooling);
  state.assistantOffReason = selected?.ai_off_reason || null;
  syncModelSelector();
  syncAskEligibility();
}

function selectedProviderUnavailableMessage(selected) {
  const label = selected?.provider ? providerDisplayName(selected.provider, selected) : "所选模型";
  if (!selected) return "当前服务没有返回所选模型的状态；请刷新 Reader 后重试。";
  if (!selected.configuration_valid) {
    if (!selected.endpoint_valid) return `${label} 的 endpoint 配置无效；Reader 其余功能不受影响。`;
    if (!selected.model_valid) return `${label} 的 model 配置无效；Reader 其余功能不受影响。`;
    return `${label} 的 provider 配置无效；Reader 其余功能不受影响。`;
  }
  if (selected.ai_off_reason === "DEVELOPMENT_DISABLED") {
    return `${label} 已在当前开发配置中关闭；请选择其他可用模型。`;
  }
  if (selected.ai_off_reason === "UNSUPPORTED_PLATFORM") {
    return `${label} 在当前系统不可用：需要 Windows 凭据或环境变量中的 API Key。`;
  }
  if (selected.ai_off_reason === "CREDENTIAL_UNAVAILABLE") {
    return `${label} 的凭据不可用（Windows 凭据与环境变量均不可读）；不会影响其他模型。`;
  }
  if (selected.cooling || selected.ai_off_reason === "COOLING") {
    const seconds = Math.max(1, Number(selected.retry_after_seconds || 1));
    return `${label} 连续失败，正在短暂冷却（约 ${seconds} 秒）；不会切换到其他模型。`;
  }
  if (!selected.credential_available) {
    const target = selected.credential_target ? `“${selected.credential_target}”` : "对应 target";
    if (selected.credential_reason === "CREDENTIAL_READ_FAILED") {
      return `无法读取 ${label} 的 Windows 凭据 ${target}；请检查当前 Core Service 的 Windows 用户身份。`;
    }
    if (selected.credential_reason === "CREDENTIAL_EMPTY") {
      return `${label} 的 Windows 凭据 ${target} 为空；请重新配置该凭据。`;
    }
    return `未找到 ${label} 的 Windows 凭据 ${target}；请在保存该凭据的 Windows 用户下启动 Core Service。`;
  }
  return `${label} 当前不可调用；Reader 其余功能不受影响。`;
}

function syncAskEligibility() {
  const hasSelection = Boolean(
    (state.selection?.resolved.length || state.guideSelection) && state.revision?.id && state.readerSessionId
  );
  const anyProviderSelectable = state.assistantProviderStatuses.some(providerSelectable);
  const ask = elements["ask-selection"];
  ask.disabled = !hasSelection || !state.assistantStatusAvailable
    || !anyProviderSelectable || state.assistantPending;
  if (!hasSelection) {
    ask.title = "请先选择当前教材页中的文字";
  } else if (!state.assistantStatusAvailable) {
    ask.title = "AI 状态接口不可用；请重启 Core Service 后重试";
  } else if (!anyProviderSelectable) {
    ask.title = "Assistant 当前已关闭或没有有效模型配置；Reader 其余功能不受影响";
  } else {
    ask.title = "在 Assistant 中选择模型后发送";
  }
  ask.setAttribute("aria-disabled", String(ask.disabled));
  syncFirstTurnEligibility();
}

function syncFirstTurnEligibility() {
  if (!elements["assistant-start"]) return;
  const selected = selectedProviderStatus();
  const visual = state.assistantDraft?.request.source_kind === 'VISUAL_REGION';
  const preparation = visual && (state.assistantDraft.preparing || state.assistantDraft.error);
  const visionAvailable = !visual || selected?.supports_images === true;
  const callable = Boolean(selected?.configured && !selected.cooling && visionAvailable && !preparation);
  const unavailable = preparation
    ? state.assistantDraft.error || '正在准备原 PDF 区域…'
    : !visionAvailable ? '当前模型不支持图像，请选择支持图像的模型。' : selectedProviderUnavailableMessage(selected);
  const start = elements["assistant-start"];
  start.disabled = !state.assistantDraft || !callable || state.assistantPending;
  start.setAttribute("aria-disabled", String(start.disabled));
  start.title = state.assistantPending
    ? "正在发送首条请求"
    : callable
      ? `使用 ${providerDisplayName(state.assistantActiveProvider, selected)} 发送首条请求`
      : unavailable;
  const readiness = elements["assistant-readiness"];
  readiness.hidden = !state.assistantDraft || callable;
  readiness.textContent = readiness.hidden ? "" : unavailable;
}

function emptyAssistantState() {
  return { version: 0, roots: [], focused_ref: null, current: null };
}

function assistantLocationKey(rootId, nodeId = null) {
  return `${rootId}:${nodeId || "root"}`;
}

function assistantTurnKey(rootId, nodeId, turnId) {
  return `${assistantLocationKey(rootId, nodeId)}:${turnId}`;
}

function clearAssistantReviewPolls() {
  for (const timer of state.assistantReviewPolls.values()) clearTimeout(timer);
  state.assistantReviewPolls.clear();
}

function rememberAssistantScroll() {
  const current = state.assistantState.current;
  if (!current) return;
  if (!state.assistantDraft) {
    const input = elements["assistant-question"];
    state.assistantViewDrafts.set(assistantLocationKey(current.root_id, current.node_id),
      {text: input.value, start: input.selectionStart, end: input.selectionEnd});
  }
  state.assistantScrollPositions.set(
    assistantLocationKey(current.root_id, current.node_id),
    elements["assistant-turns"].scrollTop,
  );
}

function forgetAssistantScroll(rootId, removedNodeIds = null) {
  for (const key of state.assistantViewDrafts.keys()) {
    if (removedNodeIds === null ? key.startsWith(`${rootId}:`)
      : [...removedNodeIds].some(id => key === assistantLocationKey(rootId, id))) state.assistantViewDrafts.delete(key);
  }
  if (removedNodeIds === null) {
    for (const key of state.assistantScrollPositions.keys()) {
      if (key.startsWith(`${rootId}:`)) state.assistantScrollPositions.delete(key);
    }
    return;
  }
  for (const nodeId of removedNodeIds) {
    state.assistantScrollPositions.delete(assistantLocationKey(rootId, nodeId));
  }
}

function splitLimits() {
  const available = elements.reader.clientWidth || window.innerWidth;
  if (elements.reader.classList.contains("portrait-mode")) {
    return {available, centerMin:Math.min(600, available - 210), leftMin:210, rightMin:320};
  }
  const leftMin = Math.min(210, Math.max(140, Math.round(available * .20)));
  const rightMin = Math.min(240, Math.max(160, Math.round(available * .23)));
  return {
    available,
    centerMin: Math.min(600, Math.max(360, Math.round(available * .58)), available - leftMin - rightMin),
    leftMin,
    rightMin,
  };
}

function assistantDockLimits() {
  if (elements.reader.classList.contains("portrait-mode")) return {minimum:ASSISTANT_DOCK_MIN_WIDTH, maximum:ASSISTANT_DOCK_MAX_WIDTH};
  const { available, centerMin } = splitLimits();
  const left = state.directoryMode === "split" && !elements["outline-panel"].hidden ? DIRECTORY_PANE_WIDTH : 0;
  const remaining = Math.max(0, available - left - centerMin);
  const minimum = Math.min(ASSISTANT_DOCK_MIN_WIDTH, remaining);
  const maximum = Math.max(minimum, Math.min(ASSISTANT_DOCK_MAX_WIDTH, remaining));
  return { minimum, maximum };
}

function applyAssistantDockWidth(width) {
  const { minimum, maximum } = assistantDockLimits();
  state.assistantDockWidth = Math.round(Math.max(minimum, Math.min(maximum, width)));
  elements.reader.style.setProperty("--assistant-dock-width", `${state.assistantDockWidth}px`);
  elements.reader.style.setProperty("--guide-width", `${state.assistantDockWidth}px`);
  elements["assistant-resize-handle"].setAttribute("aria-valuemin", String(minimum));
  elements["assistant-resize-handle"].setAttribute("aria-valuemax", String(maximum));
  elements["assistant-resize-handle"].setAttribute("aria-valuenow", String(state.assistantDockWidth));
  evaluateDirectoryMode();
}

function renderAssistantViewportMode() {
  const expanded = state.assistantExpanded && !elements["assistant-panel"].hidden;
  const heading = elements["assistant-panel"].querySelector('.assistant-heading');
  const context = elements["assistant-context-bar"];
  if (expanded) {
    heading.prepend(elements["assistant-scope"]);
  } else {
    context.before(elements["assistant-scope"]);
    closeAssistantRegionImage();
  }
  context.hidden = !state.assistantState.current || state.assistantState.current.depth < 4;
  elements.reader.classList.toggle("assistant-expanded", expanded);
  const masterExpand = document.getElementById('master-expand');
  if (masterExpand) {
    masterExpand.textContent = expanded ? '还原' : '展开';
    masterExpand.setAttribute('aria-pressed', String(expanded));
    masterExpand.setAttribute('aria-label', expanded ? '还原 Master 工作区' : '展开 Master 工作区');
  }
  elements["assistant-expand"].setAttribute("aria-pressed", String(expanded));
  elements["assistant-expand"].textContent = expanded ? "还原" : "展开";
  elements["assistant-expand"].setAttribute(
    "aria-label", expanded ? "还原临时解释面板" : "展开临时解释面板",
  );
  elements["assistant-expand"].title = expanded ? "还原临时解释面板" : "展开临时解释面板";
}

function captureMasterFocus(element = document.activeElement) {
  if (!(element instanceof HTMLElement)) return null;
  const selection = element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement
    ? { start: element.selectionStart, end: element.selectionEnd, direction: element.selectionDirection }
    : null;
  return { element, selection };
}

function masterFocusAnchor(workspace, preferredFocus) {
  const focused = preferredFocus?.element;
  if (focused instanceof HTMLElement && workspace.contains(focused)) return focused;
  const history = workspace.querySelector("#master-history");
  if (history) {
    const bounds = history.getBoundingClientRect();
    const visible = [...history.querySelectorAll(".master-message")].find((message) => {
      const rect = message.getBoundingClientRect();
      return rect.bottom > bounds.top + 8 && rect.top < bounds.bottom - 8;
    });
    if (visible) return visible;
  }
  return workspace.querySelector("#master-title") || workspace;
}

function masterAnchorDescriptor(anchor) {
  if (!(anchor instanceof HTMLElement)) return null;
  if (anchor.id) return { kind: "id", value: anchor.id };
  const message = anchor.closest("[data-message-id]");
  if (message?.dataset.messageId) return { kind: "message", value: message.dataset.messageId };
  return null;
}

function resolveMasterAnchor(workspace, descriptor) {
  if (!descriptor) return workspace.querySelector("#master-title") || workspace;
  if (descriptor.kind === "id") return document.getElementById(descriptor.value);
  if (descriptor.kind === "message") {
    return [...workspace.querySelectorAll("[data-message-id]")]
      .find((message) => message.dataset.messageId === descriptor.value) || null;
  }
  return null;
}

function captureMasterMorphSnapshot() {
  const workspace = document.getElementById("master-workspace");
  const panel = elements["assistant-panel"];
  const sidebar = workspace.querySelector("#master-topic-sidebar");
  if (sidebar?.offsetParent) masterSidebarScrollPosition = sidebar.scrollTop;
  const preferredFocus = masterMorphFocusOrigin || captureMasterFocus();
  masterMorphFocusOrigin = null;
  const anchor = masterFocusAnchor(workspace, preferredFocus);
  return {
    workspace,
    panelRect: panel.getBoundingClientRect(),
    anchor: masterAnchorDescriptor(anchor),
    anchorRect: anchor.getBoundingClientRect(),
    historyScroll: workspace.querySelector("#master-history")?.scrollTop || 0,
    sidebarScroll: masterSidebarScrollPosition,
    focus: preferredFocus,
  };
}

function restoreMasterMorphState(snapshot, { focus = false } = {}) {
  const history = snapshot.workspace.querySelector("#master-history");
  const sidebar = snapshot.workspace.querySelector("#master-topic-sidebar");
  if (history) history.scrollTop = snapshot.historyScroll;
  masterSidebarScrollPosition = snapshot.sidebarScroll;
  if (sidebar?.offsetParent) sidebar.scrollTop = snapshot.sidebarScroll;
  if (!focus) return;
  const target = snapshot.focus?.element;
  if (!(target instanceof HTMLElement) || !target.isConnected || target.hidden) return;
  target.focus({ preventScroll: true });
  if (snapshot.focus.selection && (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement)) {
    target.setSelectionRange(
      snapshot.focus.selection.start,
      snapshot.focus.selection.end,
      snapshot.focus.selection.direction,
    );
  }
}

function masterMorphSurface(rect) {
  const surface = document.createElement("div");
  surface.className = "master-workspace-morph-surface";
  Object.assign(surface.style, {
    top: `${rect.top}px`,
    right: `${Math.max(0, innerWidth - rect.right)}px`,
    bottom: `${Math.max(0, innerHeight - rect.bottom)}px`,
    left: `${rect.left}px`,
  });
  document.body.append(surface);
  return surface;
}

function masterSidebarGhost(sidebar) {
  if (!(sidebar instanceof HTMLElement) || !sidebar.offsetParent) return null;
  const rect = sidebar.getBoundingClientRect();
  const ghost = sidebar.cloneNode(true);
  ghost.className = "master-topic-sidebar-ghost";
  ghost.removeAttribute("id");
  ghost.setAttribute("aria-hidden", "true");
  ghost.querySelectorAll("[id]").forEach((node) => node.removeAttribute("id"));
  ghost.querySelectorAll("button, a, input, select, textarea").forEach((node) => node.tabIndex = -1);
  Object.assign(ghost.style, {
    top: `${rect.top}px`, left: `${rect.left}px`, width: `${rect.width}px`, height: `${rect.height}px`,
  });
  document.body.append(ghost);
  return ghost;
}

function finishMasterWorkspaceMorph(token) {
  if (!masterWorkspaceMorph || masterWorkspaceMorph.token !== token) return;
  const { animations, surface, sidebarGhost, snapshot } = masterWorkspaceMorph;
  animations.forEach((animation) => animation.cancel());
  surface?.remove();
  sidebarGhost?.remove();
  elements.reader.classList.remove("master-workspace-morphing");
  delete elements.reader.dataset.masterMorphPhase;
  restoreMasterMorphState(snapshot, { focus: true });
  masterWorkspaceMorph = null;
}

function cancelMasterWorkspaceMorph() {
  if (!masterWorkspaceMorph) return;
  finishMasterWorkspaceMorph(masterWorkspaceMorph.token);
}

function animateMasterWorkspace(expanded) {
  cancelMasterWorkspaceMorph();
  const snapshot = captureMasterMorphSnapshot();
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const sidebarBefore = snapshot.workspace.querySelector("#master-topic-sidebar");
  const sidebarGhost = expanded || reduced ? null : masterSidebarGhost(sidebarBefore);
  const surface = expanded || reduced ? null : masterMorphSurface(snapshot.panelRect);

  if (expanded) state.assistantDockWidthBeforeExpanded = state.assistantDockWidth;
  state.assistantExpanded = expanded;
  if (!expanded) applyAssistantDockWidth(state.assistantDockWidthBeforeExpanded);
  renderAssistantViewportMode();
  hideAssistantAnswerActions(true);
  restoreMasterMorphState(snapshot);

  if (reduced) {
    restoreMasterMorphState(snapshot, { focus: true });
    return;
  }

  const workspace = snapshot.workspace;
  const conversation = workspace.querySelector(".master-conversation");
  const afterAnchor = resolveMasterAnchor(workspace, snapshot.anchor)
    || workspace.querySelector("#master-title") || workspace;
  const afterRect = afterAnchor.getBoundingClientRect();
  const deltaX = snapshot.anchorRect.left - afterRect.left;
  const deltaY = snapshot.anchorRect.top - afterRect.top;
  const easing = "cubic-bezier(.2,.72,.22,1)";
  const token = Symbol("master-workspace-morph");
  const animations = [];
  elements.reader.classList.add("master-workspace-morphing");
  elements.reader.dataset.masterMorphPhase = expanded ? "expanding" : "restoring";

  if (expanded) {
    const panel = elements["assistant-panel"];
    animations.push(panel.animate([
      { clipPath: `inset(0 0 0 ${Math.max(0, snapshot.panelRect.left)}px)` },
      { clipPath: "inset(0 0 0 0)" },
    ], { duration: MASTER_WORKSPACE_MOTION.workspace, easing, fill: "both" }));
    animations.push(conversation.animate([
      { transform: `translate3d(${deltaX}px, ${deltaY}px, 0)`, opacity: .985 },
      { transform: "translate3d(0, 0, 0)", opacity: 1 },
    ], { duration: MASTER_WORKSPACE_MOTION.workspace, easing, fill: "both" }));
    const sidebar = workspace.querySelector("#master-topic-sidebar");
    if (sidebar) animations.push(sidebar.animate([
      { opacity: 0, transform: "translate3d(-10px, 0, 0)" },
      { opacity: 1, transform: "translate3d(0, 0, 0)" },
    ], {
      delay: MASTER_WORKSPACE_MOTION.sidebarDelay,
      duration: MASTER_WORKSPACE_MOTION.sidebarReveal,
      easing,
      fill: "both",
    }));
  } else {
    const targetRect = elements["assistant-panel"].getBoundingClientRect();
    if (surface) {
      animations.push(surface.animate([
        { transform: "translate3d(0, 0, 0)", opacity: 1 },
        { transform: `translate3d(${targetRect.left}px, 0, 0)`, opacity: 1 },
      ], {
        delay: MASTER_WORKSPACE_MOTION.restoreWorkspaceDelay,
        duration: MASTER_WORKSPACE_MOTION.restoreWorkspace,
        easing,
        fill: "both",
      }));
    }
    animations.push(conversation.animate([
      { transform: `translate3d(${deltaX}px, ${deltaY}px, 0)`, opacity: .985 },
      { transform: "translate3d(0, 0, 0)", opacity: 1 },
    ], {
      delay: MASTER_WORKSPACE_MOTION.restoreWorkspaceDelay,
      duration: MASTER_WORKSPACE_MOTION.restoreWorkspace,
      easing,
      fill: "both",
    }));
    if (sidebarGhost) animations.push(sidebarGhost.animate([
      { opacity: 1, transform: "translate3d(0, 0, 0)" },
      { opacity: 0, transform: "translate3d(-10px, 0, 0)" },
    ], { duration: MASTER_WORKSPACE_MOTION.sidebarHide, easing, fill: "both" }));
  }

  masterWorkspaceMorph = { token, animations, surface, sidebarGhost, snapshot };
  Promise.allSettled(animations.map((animation) => animation.finished))
    .then(() => finishMasterWorkspaceMorph(token));
}

function captureAssistantMorphSnapshot() {
  const panel = elements["assistant-panel"];
  const workspace = panel.querySelector(".assistant-workspace-body");
  const conversation = workspace.querySelector(".assistant-conversation");
  const history = elements["assistant-turns"];
  const sidebar = workspace.querySelector(".assistant-topic-sidebar");
  if (sidebar?.offsetParent) assistantSidebarScrollPosition = sidebar.scrollTop;
  const preferredFocus = assistantMorphFocusOrigin || captureMasterFocus();
  assistantMorphFocusOrigin = null;
  const historyBounds = history.getBoundingClientRect();
  const anchor = preferredFocus?.element instanceof HTMLElement && conversation.contains(preferredFocus.element)
    ? preferredFocus.element
    : [...history.querySelectorAll(".assistant-turn")].find((turn) => {
      const rect = turn.getBoundingClientRect();
      return rect.bottom > historyBounds.top + 8 && rect.top < historyBounds.bottom - 8;
    }) || elements["assistant-title"];
  return {
    panelRect: panel.getBoundingClientRect(), workspace, conversation, history, sidebar,
    sidebarWasVisible: Boolean(sidebar.offsetParent),
    sidebarRect: sidebar.getBoundingClientRect(),
    anchor, anchorRect: anchor.getBoundingClientRect(),
    historyScroll: history.scrollTop,
    sidebarScroll: assistantSidebarScrollPosition,
    focus: preferredFocus,
  };
}

function restoreAssistantMorphState(snapshot, { focus = false } = {}) {
  snapshot.history.scrollTop = snapshot.historyScroll;
  assistantSidebarScrollPosition = snapshot.sidebarScroll;
  if (snapshot.sidebar?.offsetParent) snapshot.sidebar.scrollTop = snapshot.sidebarScroll;
  if (!focus) return;
  const target = snapshot.focus?.element;
  if (!(target instanceof HTMLElement) || !target.isConnected || !target.offsetParent) return;
  target.focus({ preventScroll: true });
  if (snapshot.focus.selection && (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement)) {
    target.setSelectionRange(
      snapshot.focus.selection.start,
      snapshot.focus.selection.end,
      snapshot.focus.selection.direction,
    );
  }
}

function assistantSidebarGhost(sidebar) {
  if (!(sidebar instanceof HTMLElement) || !sidebar.offsetParent) return null;
  const rect = sidebar.getBoundingClientRect();
  const ghost = sidebar.cloneNode(true);
  ghost.classList.add("assistant-topic-sidebar-ghost");
  ghost.removeAttribute("id");
  ghost.setAttribute("aria-hidden", "true");
  ghost.querySelectorAll("[id]").forEach((node) => node.removeAttribute("id"));
  ghost.querySelectorAll("button, a, input, select, textarea").forEach((node) => node.tabIndex = -1);
  Object.assign(ghost.style, {
    top: `${rect.top}px`, left: `${rect.left}px`, width: `${rect.width}px`, height: `${rect.height}px`,
  });
  document.body.append(ghost);
  return ghost;
}

function finishAssistantWorkspaceMorph(token) {
  if (!assistantWorkspaceMorph || assistantWorkspaceMorph.token !== token) return;
  const { animations, surface, sidebarGhost, snapshot } = assistantWorkspaceMorph;
  animations.forEach((animation) => animation.cancel());
  surface?.remove();
  sidebarGhost?.remove();
  elements.reader.classList.remove("assistant-workspace-morphing");
  delete elements.reader.dataset.assistantMorphPhase;
  restoreAssistantMorphState(snapshot, { focus: true });
  assistantWorkspaceMorph = null;
}

function cancelAssistantWorkspaceMorph() {
  if (assistantWorkspaceMorph) finishAssistantWorkspaceMorph(assistantWorkspaceMorph.token);
}

function animateAssistantWorkspace(expanded) {
  cancelAssistantWorkspaceMorph();
  const snapshot = captureAssistantMorphSnapshot();
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const narrowTopicsOpen = snapshot.workspace.classList.contains("topics-open");
  const sidebarGhost = expanded || reduced || narrowTopicsOpen ? null : assistantSidebarGhost(snapshot.sidebar);
  const surface = expanded || reduced ? null : masterMorphSurface(snapshot.panelRect);

  if (expanded) state.assistantDockWidthBeforeExpanded = state.assistantDockWidth;
  state.assistantExpanded = expanded;
  if (!expanded) applyAssistantDockWidth(state.assistantDockWidthBeforeExpanded);
  renderAssistantViewportMode();
  hideAssistantAnswerActions(true);
  restoreAssistantMorphState(snapshot);

  if (reduced) {
    restoreAssistantMorphState(snapshot, { focus: true });
    return;
  }

  const afterRect = snapshot.anchor.getBoundingClientRect();
  const deltaX = snapshot.anchorRect.left - afterRect.left;
  const deltaY = snapshot.anchorRect.top - afterRect.top;
  const easing = "cubic-bezier(.2,.72,.22,1)";
  const token = Symbol("assistant-workspace-morph");
  const animations = [];
  elements.reader.classList.add("assistant-workspace-morphing");
  elements.reader.dataset.assistantMorphPhase = expanded ? "expanding" : "restoring";

  if (expanded) {
    const sidebarDeltaX = snapshot.sidebarWasVisible
      ? snapshot.sidebarRect.left - snapshot.sidebar.getBoundingClientRect().left : -10;
    animations.push(elements["assistant-panel"].animate([
      { clipPath: `inset(0 0 0 ${Math.max(0, snapshot.panelRect.left)}px)` },
      { clipPath: "inset(0 0 0 0)" },
    ], { duration: MASTER_WORKSPACE_MOTION.workspace, easing, fill: "both" }));
    animations.push(snapshot.conversation.animate([
      { transform: `translate3d(${deltaX}px, ${deltaY}px, 0)`, opacity: .985 },
      { transform: "translate3d(0, 0, 0)", opacity: 1 },
    ], { duration: MASTER_WORKSPACE_MOTION.workspace, easing, fill: "both" }));
    animations.push(snapshot.sidebar.animate([
      { opacity: narrowTopicsOpen ? 1 : 0, transform: `translate3d(${sidebarDeltaX}px, 0, 0)` },
      { opacity: 1, transform: "translate3d(0, 0, 0)" },
    ], {
      delay: narrowTopicsOpen ? 0 : MASTER_WORKSPACE_MOTION.sidebarDelay,
      duration: narrowTopicsOpen ? MASTER_WORKSPACE_MOTION.workspace : MASTER_WORKSPACE_MOTION.sidebarReveal,
      easing,
      fill: "both",
    }));
  } else {
    const targetRect = elements["assistant-panel"].getBoundingClientRect();
    if (surface) animations.push(surface.animate([
      { transform: "translate3d(0, 0, 0)", opacity: 1 },
      { transform: `translate3d(${targetRect.left}px, 0, 0)`, opacity: 1 },
    ], {
      delay: MASTER_WORKSPACE_MOTION.restoreWorkspaceDelay,
      duration: MASTER_WORKSPACE_MOTION.restoreWorkspace,
      easing,
      fill: "both",
    }));
    animations.push(snapshot.conversation.animate([
      { transform: `translate3d(${deltaX}px, ${deltaY}px, 0)`, opacity: .985 },
      { transform: "translate3d(0, 0, 0)", opacity: 1 },
    ], {
      delay: MASTER_WORKSPACE_MOTION.restoreWorkspaceDelay,
      duration: MASTER_WORKSPACE_MOTION.restoreWorkspace,
      easing,
      fill: "both",
    }));
    if (sidebarGhost) animations.push(sidebarGhost.animate([
      { opacity: 1, transform: "translate3d(0, 0, 0)" },
      { opacity: 0, transform: "translate3d(-10px, 0, 0)" },
    ], { duration: MASTER_WORKSPACE_MOTION.sidebarHide, easing, fill: "both" }));
    if (narrowTopicsOpen && snapshot.sidebar.offsetParent) animations.push(snapshot.sidebar.animate([
      { opacity: 1, transform: `translate3d(${snapshot.sidebarRect.left - snapshot.sidebar.getBoundingClientRect().left}px, 0, 0)` },
      { opacity: 1, transform: "translate3d(0, 0, 0)" },
    ], {
      delay: MASTER_WORKSPACE_MOTION.restoreWorkspaceDelay,
      duration: MASTER_WORKSPACE_MOTION.restoreWorkspace,
      easing,
      fill: "both",
    }));
  }

  assistantWorkspaceMorph = { token, animations, surface, sidebarGhost, snapshot };
  Promise.allSettled(animations.map((animation) => animation.finished))
    .then(() => finishAssistantWorkspaceMorph(token));
}

function setAssistantExpanded(expanded, { animate = true } = {}) {
  if (elements["assistant-panel"].hidden && expanded) return;
  if (expanded === state.assistantExpanded) return;
  // 全屏解释会盖住整个阅读区，笔记抽屉先让位
  if (expanded) setNotesPanelOpen(false);
  if (animate && elements["assistant-panel"].classList.contains("master-active")) {
    animateMasterWorkspace(expanded);
    return;
  }
  if (animate) {
    animateAssistantWorkspace(expanded);
    return;
  }
  cancelMasterWorkspaceMorph();
  cancelAssistantWorkspaceMorph();
  if (expanded) state.assistantDockWidthBeforeExpanded = state.assistantDockWidth;
  state.assistantExpanded = expanded;
  if (!expanded) applyAssistantDockWidth(state.assistantDockWidthBeforeExpanded);
  renderAssistantViewportMode();
  hideAssistantAnswerActions(true);
}

function claimRightDock(owner) {
  if (owner !== "guide") guide.suspend();
  if (owner !== "knowledge") chapterEntry.close();
  if (owner !== "ai") setAssistantPanelOpen(false);
  // 导读 / 知识地图与笔记抢同一列，必须让位；问 AI 则允许与笔记并排
  if (owner !== "notes" && owner !== "ai") setNotesPanelOpen(false);
  // 窄窗里笔记是浮层，和 AI dock 叠在一起只会互相遮挡
  else if (owner === "ai" && elements.reader.clientWidth <= 900) setNotesPanelOpen(false);
}

function setAssistantPanelOpen(open, { focusViewer = false, overlay = false } = {}) {
  const previousDock = elements.reader.classList.contains("assistant-dock-open");
  if (open) claimRightDock("ai");
  if (!open && state.assistantExpanded) setAssistantExpanded(false, { animate: false });
  elements["assistant-panel"].hidden = !open;
  const overlayOpen = open && overlay && elements.reader.clientWidth <= 850 && !elements.reader.classList.contains("portrait-mode");
  elements.reader.classList.toggle("assistant-dock-open", open && !overlayOpen);
  if (open && !overlayOpen) applyAssistantDockWidth(state.assistantDockWidth);
  elements["assistant-panel"].classList.toggle("assistant-overlay-open", overlayOpen);
  elements["assistant-toggle"].setAttribute("aria-expanded", String(open));
  renderAssistantViewportMode();
  hideAssistantAnswerActions(true);
  if (focusViewer) elements.viewer.focus({ preventScroll: true });
  evaluateDirectoryMode();
  if (state.pdf && previousDock !== elements.reader.classList.contains("assistant-dock-open")) relayoutPages();
}

function updateAssistantDockFromPointer(clientX) {
  applyAssistantDockWidth(elements.reader.getBoundingClientRect().right - clientX);
}

function beginAssistantResize(event) {
  if (event.button !== 0 || state.assistantExpanded) return;
  event.preventDefault();
  event.stopPropagation();
  elements["assistant-panel"].classList.add("resizing");
  elements["assistant-resize-handle"].setPointerCapture(event.pointerId);
  updateAssistantDockFromPointer(event.clientX);
  hideSelectionActions();
  hideAssistantAnswerActions(true);
}

function continueAssistantResize(event) {
  if (!elements["assistant-panel"].classList.contains("resizing")) return;
  event.preventDefault();
  updateAssistantDockFromPointer(event.clientX);
}

function finishAssistantResize(event) {
  if (!elements["assistant-panel"].classList.contains("resizing")) return;
  event.preventDefault();
  updateAssistantDockFromPointer(event.clientX);
  elements["assistant-panel"].classList.remove("resizing");
  if (elements["assistant-resize-handle"].hasPointerCapture(event.pointerId)) {
    elements["assistant-resize-handle"].releasePointerCapture(event.pointerId);
  }
}

function resetAssistantPanel() {
  assistantRegionImages.clear();
  renderAssistantRegion(null);
  elements["assistant-title"].textContent = "问 AI";
  elements["assistant-scope"].textContent = state.assistantConfigured
    ? "等待教材选区" : "当前 AI provider 未配置，Reader 其余功能仍可使用";
  elements["assistant-turns"].replaceChildren();
  elements["assistant-empty"].hidden = false;
  elements["assistant-empty"].textContent = "在原始 PDF 中选择文字，右键选择「问 AI」。";
  elements["assistant-first-turn"].hidden = true;
  elements["assistant-follow-up"].hidden = true;
  elements["assistant-context-bar"].hidden = true;
  elements["assistant-breadcrumb"].hidden = true;
  elements["assistant-children"].hidden = true;
  elements["assistant-child-list"].replaceChildren();
  elements["assistant-question"].value = "";
  state.assistantPending = false;
  state.assistantState = emptyAssistantState();
  state.assistantDraft = null;
  state.assistantScrollPositions.clear();
  state.assistantViewDrafts.clear();
  state.assistantRenderedKey = null;
  state.assistantChildRequests.clear();
  renderAssistantNavigation(null);
  hideAssistantAnswerActions();
  syncModelSelector();
}

function openAssistantPanel({ overlay = false } = {}) {
  chapterEntry.close();
  setAssistantPanelOpen(true, { overlay });
  elements["search-panel"].hidden = true;
  elements["marks-panel"].hidden = true;
  elements["search-toggle"].setAttribute("aria-expanded", "false");
  elements["marks-toggle"].setAttribute("aria-expanded", "false");
  state.searchRequest += 1;
  clearSearchMatch();
}

function assistantScopeText(scope) {
  if (scope.kind === "SECTION") {
    return [scope.chapter_title, scope.section_title].filter(Boolean).join(" · ");
  }
  return [scope.chapter_title, `PDF ${scope.pdf_page_index + 1}`].filter(Boolean).join(" · ");
}

function applyAssistantState(nextState) {
  if (!nextState || !Array.isArray(nextState.roots)) return;
  if (Number(nextState.version || 0) < Number(state.assistantState.version || 0)) return;
  if (!state.assistantDraft && state.assistantState.current?.root_id === nextState.current?.root_id
      && state.assistantState.current?.node_id === nextState.current?.node_id) rememberAssistantScroll();
  state.assistantState = nextState;
  const liveRoots = new Set(nextState.roots.map(root => root.root_id));
  for (const rootId of assistantRegionImages.keys()) {
    if (!liveRoots.has(rootId)) assistantRegionImages.delete(rootId);
  }
  if (nextState.current?.provider) state.assistantActiveProvider = nextState.current.provider;
  renderAssistantWorkspace();
}

function renderAssistantNavigation(current) {
  assistantNavigator.render(state.assistantState);
  const entry = elements["assistant-toggle"];
  entry.hidden = !state.assistantDraft && !state.assistantState.roots.length;
  entry.title = state.assistantDraft ? "继续当前选区的临时解释" : `继续临时解释：${current?.label || "已有解释主题"}`;
  const roots = state.assistantState.roots || [];
  elements["assistant-context-bar"].hidden = !current || current.depth < 4;
  elements["assistant-root-switcher"].replaceChildren(...roots.map((root) => {
    const option = document.createElement("option");
    option.value = root.root_id;
    option.textContent = root.label;
    return option;
  }));
  if (state.assistantState.focused_ref?.root_id) {
    elements["assistant-root-switcher"].value = state.assistantState.focused_ref.root_id;
  }
  elements["assistant-back"].hidden = !current?.parent_ref;
  elements["assistant-depth"].textContent = current ? `递归深度：第 ${current.depth} 层，最多 5 层` : "";
  elements["assistant-depth"].hidden = !current || current.depth < 4;
  elements["assistant-close-root"].hidden = !current || Boolean(state.assistantDraft);
  elements["assistant-close-root"].disabled = !current;
  elements["assistant-close-root"].textContent = current?.depth > 1
    ? "关闭本层解释" : "关闭此主题";
  const semanticPath = (current?.breadcrumb || []).slice(1);
  const crumbs = semanticPath.flatMap((crumb, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = crumb.label;
    button.title = crumb.label;
    button.dataset.rootId = crumb.root_id;
    button.dataset.nodeId = crumb.node_id || "";
    button.disabled = index === semanticPath.length - 1;
    const separator = document.createElement("span");
    separator.textContent = "›";
    separator.setAttribute("aria-hidden", "true");
    return [separator, button];
  });
  elements["assistant-breadcrumb"].replaceChildren(...crumbs);
  elements["assistant-breadcrumb"].hidden = crumbs.length === 0;

  const children = current?.children || [];
  elements["assistant-child-list"].replaceChildren(...children.map((child) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = child.label;
    button.title = child.error ? `${child.label}（生成失败）` : child.label;
    button.dataset.rootId = current.root_id;
    button.dataset.nodeId = child.node_id;
    if (child.pending) button.setAttribute("aria-busy", "true");
    return button;
  }));
  elements["assistant-children"].hidden = children.length === 0;
}

function renderAssistantWorkspace() {
  hideAssistantAnswerActions();
  elements["assistant-export"].disabled = !state.assistantState.current;
  if (state.assistantDraft) {
    renderAssistantDraft();
    return;
  }
  const current = state.assistantState.current;
  const root = state.assistantState.roots.find(root => root.root_id === current?.root_id);
  const visual = root?.created_from.kind === 'VISUAL_REGION';
  const regionPreview = assistantRegionImages.get(current?.root_id);
  renderAssistantRegion(regionPreview, current?.scope, current?.root_id);
  renderAssistantNavigation(current);
  elements["assistant-title"].textContent = current?.label || "问 AI";
  elements["assistant-first-turn"].hidden = true;
  if (!current) {
    elements["assistant-scope"].textContent = state.assistantConfigured
      ? "等待教材选区" : "当前 AI provider 未配置，Reader 其余功能仍可使用";
    elements["assistant-turns"].replaceChildren();
    elements["assistant-empty"].textContent = "在原始 PDF 中选择文字，右键选择「问 AI」。";
    elements["assistant-empty"].hidden = false;
    elements["assistant-follow-up"].hidden = true;
    syncModelSelector();
    return;
  }
  elements["assistant-scope"].textContent = visual
    ? assistantRegionSourceText(current.scope, root.created_from.visual_region)
    : assistantScopeText(current.scope);
  elements["assistant-scope"].title = elements["assistant-scope"].textContent;
  elements["assistant-follow-up"].prepend(skillPicker.element);
  elements["assistant-follow-up"].append(modelControl);
  const renderKey = JSON.stringify([current.root_id, current.node_id, current.turns, current.pending, current.error, current.can_retry,
    [...state.assistantSavedTurns.keys()]]);
  if (state.assistantRenderedKey === renderKey && elements["assistant-turns"].firstChild) { syncModelSelector(); return; }
  state.assistantRenderedKey = renderKey;
  const turns = current.turns.map((turn, index) => {
    const article = document.createElement("article");
    article.className = "assistant-turn";
    const question = document.createElement("p");
    question.className = "assistant-question-bubble";
    question.textContent = turn.question;
    const answer = document.createElement("div");
    answer.className = "assistant-answer-bubble";
    renderAssistantAnswer(answer, turn.answer);
    const leadingHeading = answer.firstElementChild;
    if (leadingHeading?.matches("h1, h2, h3")
        && leadingHeading.textContent.trim() === turn.question.trim()) {
      leadingHeading.classList.add("assistant-repeated-heading");
    }
    if (index === current.turns.length - 1) {
      answer.dataset.rootId = current.root_id;
      answer.dataset.nodeId = current.node_id || "";
      answer.dataset.turnId = turn.turn_id;
      answer.dataset.currentAnswer = "true";
      if (current.depth >= 5) answer.title = "已到第 5 层；可在下方继续追问";
    }
    const turnActions = document.createElement("div");
    turnActions.className = "assistant-turn-actions";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "assistant-save-note";
    save.dataset.rootId = current.root_id;
    save.dataset.nodeId = current.node_id || "";
    save.dataset.turnId = turn.turn_id;
    const saveKey = assistantTurnKey(current.root_id, current.node_id, turn.turn_id);
    const saved = state.assistantSavedTurns.has(saveKey);
    save.textContent = saved ? "已保存" : "保存到笔记";
    save.disabled = saved;
    save.hidden = ["READING_GUIDE", "INLINE_GUIDANCE", "VISUAL_REGION"].includes(state.assistantState.roots.find((root) => root.root_id === current.root_id)?.created_from.kind);
    turnActions.append(save);
    // The implicit visual prompt is transport context, not a user-authored message.
    const implicitVisualQuestion = visual && !current.node_id && index === 0
      && turn.question.trim() === '请解释这个区域中的内容和关系。';
    if (!implicitVisualQuestion) article.append(question);
    article.append(answer, turnActions);
    return article;
  });
  if (current.pending) {
    const pending = document.createElement("div");
    pending.className = "assistant-pending";
    pending.textContent = "准备上下文…";
    turns.push(pending);
  } else if (current.error) {
    const error = document.createElement("div");
    error.className = "assistant-error";
    error.textContent = current.error;
    if(current.can_retry) {
      const retry = document.createElement('button'); retry.type = 'button';
      retry.textContent = '重试这层解释';
      retry.onclick = () => retryAssistantChild(current.root_id, current.node_id, retry);
      error.append(retry);
    }
    turns.push(error);
  }
  elements["assistant-turns"].replaceChildren(...turns);
  elements["assistant-empty"].hidden = turns.length > 0;
  elements["assistant-follow-up"].hidden = current.pending || current.turns.length === 0;
  syncModelSelector();
  const locationKey = assistantLocationKey(current.root_id, current.node_id);
  const savedScroll = state.assistantScrollPositions.get(locationKey);
  const draft = state.assistantViewDrafts.get(locationKey);
  elements["assistant-question"].value = draft?.text || "";
  elements["assistant-question"].setSelectionRange(draft?.start || 0, draft?.end || 0);
  requestAnimationFrame(() => {
    if (state.assistantRenderedKey !== renderKey) return;
    elements["assistant-turns"].scrollTop = savedScroll ?? elements["assistant-turns"].scrollHeight;
  });
}

function renderAssistantConversation(conversation) {
  // Compatibility shim for stale callers during the transition to Root/Node state.
  applyAssistantState(conversation);
}

function renderAssistantDraft() {
  state.assistantRenderedKey = null;
  elements["assistant-first-turn"].prepend(skillPicker.element);
  elements["assistant-first-turn"].append(modelControl);
  const draft = state.assistantDraft;
  if (!draft) {
    elements["assistant-first-turn"].hidden = true;
    return;
  }
  elements["assistant-title"].textContent = "问 AI";
  const visual = draft.request.source_kind === 'VISUAL_REGION';
  elements['assistant-first-turn'].classList.toggle('visual-draft', visual);
  document.getElementById('assistant-region-question-label').hidden = !visual;
  elements['assistant-draft-text'].parentElement.hidden = visual;
  // 文字选区模式提供可编辑的补充输入；框选区域沿用自带的问题输入。
  elements["assistant-supplement-label"].hidden = visual;
  if (!visual) elements["assistant-supplement"].value = "";
  renderAssistantRegion(visual ? draft.preview : null, draft.sourceScope, draft);
  elements["assistant-scope"].textContent = draft.request.source_kind === "READING_GUIDE"
    ? "正在解释：导读选区" : draft.request.source_kind === "INLINE_GUIDANCE" ? "正在解释：行间教学"
    : draft.request.source_kind === "MASTER_ANSWER" ? "正在解释：Master 回答选区"
    : visual ? assistantRegionSourceText(draft.sourceScope, draft.request)
    : `PDF ${draft.request.pdf_page_index + 1} · 教材选区`;
  elements["assistant-scope"].title = elements["assistant-scope"].textContent;
  elements["assistant-draft-text"].textContent = draft.selectedText;
  elements["assistant-first-turn"].hidden = false;
  elements["assistant-follow-up"].hidden = true;
  renderAssistantNavigation(state.assistantState.current);
  elements["assistant-turns"].replaceChildren();
  elements["assistant-empty"].textContent = state.assistantState.roots.length
    ? "已有解释仍保留；发送后会新增一个主题。" : "";
  elements["assistant-empty"].hidden = !state.assistantState.roots.length;
  syncModelSelector();
}

function assistantRegionSourceText(scope, region) {
  return [scope?.section_title || scope?.chapter_title,
    Number.isInteger(region?.pdf_page_index) ? `PDF ${region.pdf_page_index + 1}` : null]
    .filter(Boolean).join(' · ');
}

function closeAssistantRegionImage() {
  const dialog = document.getElementById('assistant-region-dialog');
  if (dialog.open) dialog.close();
  document.getElementById('assistant-region-full-image').removeAttribute('src');
}

function openAssistantRegionImage() {
  if (!state.assistantExpanded || !assistantRegionView?.preview?.image?.data_url) return;
  const {preview, scope} = assistantRegionView;
  document.getElementById('assistant-region-full-image').src = preview.image.data_url;
  document.getElementById('assistant-region-dialog-title').textContent = assistantRegionSourceText(scope, preview);
  document.getElementById('assistant-region-dialog').showModal();
}

function renderAssistantRegion(preview, scope, topic) {
  if (assistantRegionView?.topic !== topic || assistantRegionView?.preview !== preview) closeAssistantRegionImage();
  assistantRegionView = preview ? {preview, scope, topic} : null;
  const row = document.getElementById('assistant-region-preview');
  const image = document.getElementById('assistant-region-image');
  row.hidden = !preview?.image?.data_url;
  elements['assistant-panel'].classList.toggle('has-visual-region', !row.hidden);
  if (row.hidden) { image.removeAttribute('src'); return; }
  image.src = preview.image.data_url;
  const title = document.getElementById('assistant-region-title');
  title.textContent = scope?.section_title || scope?.chapter_title || '框选区域';
  title.title = title.textContent;
  document.getElementById('assistant-region-caption').textContent = `PDF ${preview.pdf_page_index + 1}`;
}

async function stageVisualAssistant({pageIndex, rect}) {
  if (!state.revision?.id || !state.readerSessionId || state.assistantPending) return;
  rememberAssistantScroll();
  const draft = {readerSessionId: state.readerSessionId, revisionId: state.revision.id,
    selectedText: `PDF ${pageIndex + 1} 的框选区域`, preparing: true,
    request: {source_kind: 'VISUAL_REGION', pdf_page_index: pageIndex, rect}};
  // Display-only source label; the server still builds all model context.
  const y = (rect[1] + rect[3]) / 2;
  const containing = state.outlineNodes.filter(node => node.resolution_state === 'RESOLVED'
    && (node.start_page < pageIndex || node.start_page === pageIndex && node.start_y <= y)
    && (node.end_page > pageIndex || node.end_page === pageIndex && node.end_y > y))
    .sort((a, b) => b.depth - a.depth);
  draft.sourceScope = {section_title: containing[0]?.title};
  state.assistantDraft = draft;
  document.getElementById('assistant-region-question').value = '';
  openAssistantPanel({overlay: true});
  renderAssistantDraft();
  refreshAssistantStatus();
  try {
    const preview = await api(`/api/revisions/${draft.revisionId}/visual-region`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pdf_page_index: pageIndex, rect}),
    });
    if (state.assistantDraft !== draft || state.readerSessionId !== draft.readerSessionId) return;
    draft.preview = preview;
  } catch (error) {
    if (state.assistantDraft !== draft) return;
    draft.error = `区域准备失败：${error.message}。请重新框选。`;
  } finally {
    if (state.assistantDraft === draft) {
      draft.preparing = false;
      renderAssistantDraft();
      if (!draft.error) document.getElementById('assistant-region-question').focus({preventScroll: true});
    }
  }
}

function showAssistantPending(question, phase = "answering") {
  const pending = document.createElement("div");
  pending.className = "assistant-pending";
  pending.textContent = phase === "preparing" ? "准备上下文…" : `正在回答“${question}”…`;
  elements["assistant-turns"].append(pending);
  elements["assistant-empty"].hidden = true;
  return pending;
}

function abortAssistantStreams() {
  for (const controller of state.assistantStreamControllers.values()) controller.abort();
  state.assistantStreamControllers.clear();
}

function createAssistantStreamView(question, pending, { showQuestion = true } = {}) {
  const article = document.createElement("article");
  article.className = "assistant-turn assistant-stream-turn";
  if (showQuestion) {
    const questionBubble = document.createElement("p");
    questionBubble.className = "assistant-question-bubble";
    questionBubble.textContent = question;
    article.append(questionBubble);
  }
  const answer = document.createElement("div");
  answer.className = "assistant-answer-bubble assistant-answer-streaming";
  answer.hidden = true;
  answer.setAttribute("aria-live", "off");
  article.append(answer);
  pending.before(article);
  return { article, answer, pending, content: "", generation: state.generation, readerSessionId: state.readerSessionId };
}

function clearAssistantStreamTransient() {
  elements["assistant-turns"].querySelectorAll(
    ".assistant-stream-turn,.assistant-stream-error,.assistant-pending"
  ).forEach((node) => node.remove());
}

function appendAssistantDelta(view, content) {
  if (!content || !view.article.isConnected) return;
  view.content += content;
  view.answer.hidden = false;
  view.answer.append(document.createTextNode(content));
  if (!view.scrollFrame) view.scrollFrame = requestAnimationFrame(() => flushAssistantStreamScroll(view));
}

function flushAssistantStreamScroll(view) {
  if (!view.scrollFrame) return;
  cancelAnimationFrame(view.scrollFrame);
  view.scrollFrame = 0;
  if (!view.article.isConnected || view.signal?.aborted
      || view.generation !== state.generation || view.readerSessionId !== state.readerSessionId) return;
  elements["assistant-turns"].scrollTop = elements["assistant-turns"].scrollHeight;
}

function showAssistantStreamFailure(view, error, retry) {
  view.pending.remove();
  const failure = document.createElement("div");
  failure.className = "assistant-error assistant-stream-error";
  const message = document.createElement("span");
  message.textContent = error.message || "AI 解释失败，请稍后再试。";
  const button = document.createElement("button");
  button.type = "button";
  const canContinue = error.code === "AI_RESPONSE_LENGTH_LIMIT" && Boolean(view.content);
  button.textContent = canContinue ? "继续生成" : view.content ? "重新回答" : "重试";
  button.addEventListener("click", () => retry(canContinue ? view.content : ""));
  failure.append(message, button);
  view.article.after(failure);
}

async function runAssistantStream({
  path, body, question, showQuestion = true, retry, streamKey, initialContent = "",
}) {
  clearAssistantStreamTransient();
  const pending = showAssistantPending(question, "preparing");
  const view = createAssistantStreamView(question, pending, { showQuestion });
  if (initialContent) appendAssistantDelta(view, initialContent);
  const controller = new AbortController();
  view.signal = controller.signal;
  state.assistantStreamControllers.set(streamKey, controller);
  let completed = null;
  try {
    await streamAssistantResponse(path, {
      method: "POST",
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        "X-Reader-Token": launchToken,
        "X-Assistant-View": "current",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
      },
      body: JSON.stringify(body),
    }, (event) => {
      if (event.type === "stage" && event.stage === "answering") {
        pending.textContent = "正在回答…";
      } else if (event.type === "delta") {
        appendAssistantDelta(view, event.content);
      } else if (event.type === "complete") {
        completed = event;
      }
    });
    if (!completed) throw new Error("流式回答未正常完成；已保留收到的内容，可以重试。");
    pending.textContent = "完成";
    pending.classList.add("assistant-complete");
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return completed;
  } catch (error) {
    if (error.name === "AbortError") throw error;
    showAssistantStreamFailure(view, error, retry);
    throw error;
  } finally {
    if (state.assistantStreamControllers.get(streamKey) === controller) {
      state.assistantStreamControllers.delete(streamKey);
    }
  }
}

async function advanceAssistantPending(pending, question) {
  await new Promise((resolve) => requestAnimationFrame(resolve));
  if (pending?.isConnected) pending.textContent = `正在回答“${question}”…`;
}

function showAssistantError(error) {
  elements["assistant-turns"].querySelectorAll(".assistant-pending").forEach((node) => node.remove());
  const message = document.createElement("div");
  message.className = "assistant-error";
  message.textContent = error.message || "AI 解释失败，请稍后再试。";
  elements["assistant-turns"].append(message);
}

function stageAssistantSelection() {
  if (state.guideSelection) {
    const { explain } = state.guideSelection;
    hideSelectionActions(); explain(); return;
  }
  const selection = state.selection;
  const revisionId = state.revision?.id;
  const readerSessionId = state.readerSessionId;
  const selectedText = resolvedText(selection?.resolved || []).trim();
  if (!selection?.resolved.length || !selectedText || !revisionId || !readerSessionId || state.assistantPending) return;
  rememberAssistantScroll();
  state.assistantDraft = {
    readerSessionId,
    revisionId,
    selectedText,
    request: {
      pdf_page_index: selection.pageIndex,
      start: { line_ordinal: selection.anchor.lineOrdinal, boundary: selection.anchor.boundary },
      end: { line_ordinal: selection.focus.lineOrdinal, boundary: selection.focus.boundary },
    },
  };
  openAssistantPanel({ overlay: true });
  renderAssistantDraft();
  refreshAssistantStatus();
  clearSelection({ animateActions: true });
}

function openAssistantDraftFromVisibleSelection(selectedText, request) {
  if (!state.readerSessionId || !state.revision?.id || state.assistantPending) return;
  rememberAssistantScroll();
  state.assistantDraft = {
    readerSessionId: state.readerSessionId,
    revisionId: state.revision.id,
    selectedText,
    request,
  };
  openAssistantPanel();
  renderAssistantDraft();
  refreshAssistantStatus();
}

function cacheAssistantRegion(preview, previousRootIds, nextState) {
  if (!preview) return;
  const root = nextState.roots.find(root => {
    const region = root.created_from.visual_region;
    return !previousRootIds.has(root.root_id) && root.created_from.kind === 'VISUAL_REGION'
      && region?.pdf_page_index === preview.pdf_page_index
      && JSON.stringify(region.rect) === JSON.stringify(preview.rect);
  });
  if (root) assistantRegionImages.set(root.root_id, preview);
}

async function sendAssistantFirstTurn(continuationText = "") {
  const draft = state.assistantDraft;
  if (!draft || state.assistantPending || !state.assistantConfigured || state.assistantCooling) return;
  if (draft.request.source_kind === 'VISUAL_REGION') {
    if (draft.preparing || draft.error || !draft.preview || !selectedProviderStatus()?.supports_images) return;
    draft.request.question = document.getElementById('assistant-region-question').value.trim();
  }
  // 文字选区：允许在所选文字之外补充提问；留空则仅解释所选文字。
  const supplement = elements["assistant-supplement"].value.trim();
  const displayQuestion = supplement
    ? `${draft.selectedText}\n\n补充提问：${supplement}` : draft.selectedText;
  state.assistantPending = true;
  const previousRootIds = new Set(state.assistantState.roots.map(root => root.root_id));
  syncModelSelector();
  try {
    const payload = await runAssistantStream({
      path: `/api/revisions/${draft.revisionId}/assistant/ask`,
      question: displayQuestion,
      showQuestion: false,
      retry: (partial) => sendAssistantFirstTurn(partial),
      streamKey: `draft:${draft.readerSessionId}`,
      initialContent: continuationText,
      body: {
        reader_session_id: draft.readerSessionId,
        provider: state.assistantActiveProvider,
        source_kind: "ORIGINAL_PDF",
        ...draft.request,
        ...assistantSkillField(),
        ...(supplement ? { question: supplement } : {}),
        ...(continuationText ? { continuation: { partial_answer: continuationText } } : {}),
      },
    });
    if (state.readerSessionId !== draft.readerSessionId) return;
    cacheAssistantRegion(draft.preview, previousRootIds, payload.assistant);
    state.assistantDraft = null;
    applyAssistantState(payload.assistant);
  } catch (error) {
    if (state.readerSessionId === draft.readerSessionId
        && error.code !== "ASSISTANT_REQUEST_CANCELLED" && error.name !== "AbortError") {
      // The streaming view already owns the partial answer, error, and explicit retry.
    }
    if (reportSkillFailure(error)) return;
    if (error.code?.startsWith("AI_")) {
      await refreshAssistantStatus();
    }
  } finally {
    state.assistantPending = false;
    syncModelSelector();
  }
}

async function sendAssistantFollowUp(continuationText = "") {
  const question = elements["assistant-question"].value.trim();
  const current = state.assistantState.current;
  const readerSessionId = state.readerSessionId;
  if (!question || !current || !readerSessionId || state.assistantPending) return;
  state.assistantPending = true;
  elements["assistant-send"].disabled = true;
  try {
    const payload = await runAssistantStream({
      path: "/api/assistant/follow-up",
      question,
      retry: (partial) => sendAssistantFollowUp(partial),
      streamKey: assistantLocationKey(current.root_id, current.node_id),
      initialContent: continuationText,
      body: {
        reader_session_id: readerSessionId,
        root_id: current.root_id,
        node_id: current.node_id,
        question,
        ...assistantSkillField(),
        ...(continuationText ? { continuation: { partial_answer: continuationText } } : {}),
      },
    });
    if (state.readerSessionId !== readerSessionId) return;
    state.assistantViewDrafts.delete(assistantLocationKey(current.root_id, current.node_id));
    if (state.assistantState.current?.root_id === current.root_id && state.assistantState.current?.node_id === current.node_id) elements["assistant-question"].value = "";
    applyAssistantState(payload.assistant);
  } catch (error) {
    const stillBound = state.assistantState.current?.root_id === current.root_id
      && state.assistantState.current?.node_id === current.node_id;
    if (state.readerSessionId === readerSessionId && stillBound
        && error.code !== "ASSISTANT_REQUEST_CANCELLED" && error.name !== "AbortError") {
      // The streaming view retains the partial answer and retry control.
    }
    if (reportSkillFailure(error)) return;
    if (error.code?.startsWith("AI_")) {
      await refreshAssistantStatus();
    }
  } finally {
    state.assistantPending = false;
    elements["assistant-send"].disabled = false;
  }
}

async function clearAssistantSession({ keepalive = false } = {}) {
  const readerSessionId = state.readerSessionId;
  if (!readerSessionId) return;
  abortAssistantStreams();
  await api("/api/assistant/close", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reader_session_id: readerSessionId }),
    keepalive,
  });
  state.assistantState = emptyAssistantState();
  resetAssistantPanel();
}

async function focusAssistant(rootId, nodeId = null) {
  const readerSessionId = state.readerSessionId;
  if (!readerSessionId || !rootId) return;
  rememberAssistantScroll();
  state.assistantDraft = null;
  hideAssistantAnswerActions(true);
  try {
    const payload = await api("/api/assistant/focus", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reader_session_id: readerSessionId, root_id: rootId, node_id: nodeId }),
    });
    if (state.readerSessionId === readerSessionId) applyAssistantState(payload.assistant);
  } catch (error) {
    if (state.readerSessionId === readerSessionId) showAssistantError(error);
  }
}

function optimisticChildState(childSelection, nodeId) {
  const nextState = structuredClone(state.assistantState);
  const parent = nextState.current;
  const root = nextState.roots.find((candidate) => candidate.root_id === childSelection.rootId);
  if (!parent || !root) return false;
  const parentRef = { root_id: childSelection.rootId, node_id: childSelection.nodeId };
  const node = {
    node_id: nodeId,
    parent_ref: parentRef,
    depth: parent.depth + 1,
    label: childSelection.selectedText,
    active_child_id: null,
    child_pending: false,
    pending: true,
    error: null,
    turns: [],
  };
  root.nodes.push(node);
  root.focused_node_id = nodeId;
  if (childSelection.nodeId) {
    const parentNode = root.nodes.find((candidate) => candidate.node_id === childSelection.nodeId);
    if (parentNode) {
      parentNode.active_child_id = nodeId;
      parentNode.child_pending = true;
    }
  } else {
    root.active_child_id = nodeId;
    root.child_pending = true;
  }
  nextState.focused_ref = { root_id: childSelection.rootId, node_id: nodeId };
  nextState.current = {
    ...node,
    root_id: childSelection.rootId,
    provider: parent.provider,
    model: parent.model,
    scope: parent.scope,
    children: [],
    breadcrumb: [
      ...(parent.breadcrumb || []),
      { root_id: childSelection.rootId, node_id: nodeId, depth: node.depth, label: node.label },
    ],
  };
  state.assistantState = nextState;
  renderAssistantWorkspace();
  return true;
}

function markOptimisticChildError(rootId, nodeId, message, render = true) {
  const root = state.assistantState.roots.find((candidate) => candidate.root_id === rootId);
  const node = root?.nodes.find((candidate) => candidate.node_id === nodeId);
  if (!node) return;
  node.pending = false;
  node.error = message || "AI 解释失败，请稍后再试。";
  const parent = node.parent_ref.node_id
    ? root.nodes.find((candidate) => candidate.node_id === node.parent_ref.node_id)
    : root;
  if (parent) parent.child_pending = false;
  if (state.assistantState.current?.node_id === nodeId) {
    state.assistantState.current.pending = false;
    state.assistantState.current.error = node.error;
    if (render) renderAssistantWorkspace();
  }
}

async function retryAssistantChild(rootId, nodeId, button, continuationText = "") {
  const sessionId = state.readerSessionId;
  const current = state.assistantState.current;
  button.disabled = true;
  try {
    const payload = await runAssistantStream({
      path: '/api/assistant/retry-child',
      question: current?.label || "这层解释",
      retry: (partial) => retryAssistantChild(rootId, nodeId, button, partial),
      streamKey: assistantLocationKey(rootId, nodeId),
      initialContent: continuationText,
      body: {
        reader_session_id: sessionId,
        root_id: rootId,
        node_id: nodeId,
        ...(continuationText ? { continuation: { partial_answer: continuationText } } : {}),
      },
    });
    if(state.readerSessionId === sessionId) applyAssistantState(payload.assistant);
  } catch(error) {
    if(state.readerSessionId === sessionId && error.code !== 'ASSISTANT_REQUEST_CANCELLED') {
      announce(error.message, true);
      if(state.assistantState.current?.root_id === rootId && state.assistantState.current?.node_id === nodeId) await focusAssistant(rootId, nodeId);
    }
  } finally { button.disabled = false; }
}

function assistantSubtreeIds(root, nodeId) {
  const removed = new Set([nodeId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of root?.nodes || []) {
      if (!removed.has(node.node_id) && removed.has(node.parent_ref?.node_id)) {
        removed.add(node.node_id);
        changed = true;
      }
    }
  }
  return removed;
}

async function closeFocusedAssistantLevel() {
  const current = state.assistantState.current;
  const readerSessionId = state.readerSessionId;
  if (!current || !readerSessionId) return;
  state.assistantStreamControllers.get(
    assistantLocationKey(current.root_id, current.node_id)
  )?.abort();
  rememberAssistantScroll();
  hideAssistantAnswerActions(true);
  const closingChild = current.depth > 1;
  const root = state.assistantState.roots.find((candidate) => candidate.root_id === current.root_id);
  const removedNodeIds = closingChild ? assistantSubtreeIds(root, current.node_id) : null;
  try {
    const payload = await api(closingChild ? "/api/assistant/close-child" : "/api/assistant/close-root", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reader_session_id: readerSessionId,
        root_id: current.root_id,
        ...(closingChild ? { node_id: current.node_id } : {}),
      }),
    });
    if (state.readerSessionId === readerSessionId) {
      forgetAssistantScroll(current.root_id, removedNodeIds);
      applyAssistantState(payload.assistant);
    }
  } catch (error) {
    if (state.readerSessionId === readerSessionId) showAssistantError(error);
  }
}

// 把当前选区映射成可保存的摘录数据；返回 {range, ...data} 或 null。
// 只做映射与校验，不碰工具栏、不写 state——capture 与 pointerup 暂存共用。
// quiet：pointerup 后台暂存时用，映射失败不弹提示（普通点击不该冒 toast）。
function mapAssistantAnswerSelection({ quiet = false } = {}) {
  const current = state.assistantState.current;
  const selection = window.getSelection();
  if (!current || !selection || selection.isCollapsed || selection.rangeCount !== 1) return null;
  const range = selection.getRangeAt(0);
  const startElement = range.startContainer.nodeType === Node.ELEMENT_NODE
    ? range.startContainer : range.startContainer.parentElement;
  const endElement = range.endContainer.nodeType === Node.ELEMENT_NODE
    ? range.endContainer : range.endContainer.parentElement;
  const startBubble = startElement?.closest(".assistant-answer-bubble");
  const endBubble = endElement?.closest(".assistant-answer-bubble");
  if (!startBubble || startBubble !== endBubble || startBubble.dataset.currentAnswer !== "true") return null;
  const mapped = renderedSelectionToRaw(range, startBubble);
  if (mapped?.blocked) {
    if (!quiet) announce(mapped.reason, true);
    return null;
  }
  if (!mapped) {
    if (!quiet) announce("这段渲染内容暂时无法精确对应原回答，请选择普通正文文字。", true);
    return null;
  }
  return {
    range,
    rootId: startBubble.dataset.rootId,
    nodeId: startBubble.dataset.nodeId || null,
    turnId: startBubble.dataset.turnId,
    startOffset: mapped.startOffset,
    endOffset: mapped.endOffset,
    sourceSpans: mapped.sourceSpans,
    selectedText: mapped.selectedText,
    // 源码切片只含可见文本，markdown 标记（**/#/| 等）会丢；这里从渲染后的
    // DOM 反向序列化出带格式的版本，存笔记时随摘录一起提交，还原失败则回落纯文本
    excerptMarkdown: selectionToMarkdown(range, startBubble) || mapped.selectedText,
    // 层数已到顶 / 正在生成时只关掉「再问一层」，存摘录不受影响
    canDeepen: current.depth < 5 && !current.child_pending && !current.pending
      && !state.assistantPending,
  };
}

function captureAssistantAnswerSelection() {
  hideAssistantAnswerActions();
  const mapped = mapAssistantAnswerSelection();
  if (!mapped) return null;
  const { range, ...data } = mapped;
  state.assistantAnswerSelection = data;
  return range;
}

function openAssistantAnswerContextMenu(event) {
  let range = captureAssistantAnswerSelection();
  // 右键按下会把选区塌陷成光标（浏览器原生行为），活选区没了；退回
  // pointerup 时暂存的上一次有效选区，工具栏照样弹出。
  let stashUsed = false;
  if (!range && state.assistantAnswerSelectionStash) {
    const stash = state.assistantAnswerSelectionStash;
    state.assistantAnswerSelection = stash.data;
    range = stash.range;
    stashUsed = true;
  }
  if (!range) return;
  const rects = Array.from(range.getClientRects());
  const { clientX, clientY } = event;
  const inside = rects.some((rect) => (
    clientX >= rect.left && clientX <= rect.right
      && clientY >= rect.top && clientY <= rect.bottom
  ));
  // 右键没点在选区上时，锚定到选区附近弹工具栏，而不是静默失败——
  // 否则用户右键位置稍有偏差，功能就像坏了（真机反馈：摘录功能"用不了"）。
  let anchorX = clientX;
  let anchorY = clientY;
  if (!inside) {
    const target = rects.find((rect) => rect.width > 0 && rect.height > 0);
    if (!target) {
      hideAssistantAnswerActions();
      return;
    }
    anchorX = target.left + target.width / 2;
    anchorY = target.bottom + 5;
  }
  event.preventDefault();
  const deepener = elements["assistant-ask-deeper"];
  const canDeepen = Boolean(state.assistantAnswerSelection?.canDeepen);
  deepener.hidden = !canDeepen;
  deepener.disabled = !canDeepen;
  const action = elements["assistant-answer-actions"];
  action.hidden = false;
  if (stashUsed) action.dataset.stashed = "1";
  else delete action.dataset.stashed;
  requestAnimationFrame(() => {
    if (action.hidden || !state.assistantAnswerSelection) return;
    const below = anchorY + 5;
    const above = anchorY - action.offsetHeight - 5;
    const top = below + action.offsetHeight <= innerHeight - 8 ? below : above;
    action.style.left = `${Math.max(8, Math.min(innerWidth - action.offsetWidth - 8, anchorX + 5))}px`;
    action.style.top = `${Math.max(76, Math.min(innerHeight - action.offsetHeight - 8, top))}px`;
  });
}

function hideAssistantAnswerActions(clearNativeSelection = false) {
  elements["assistant-answer-actions"].hidden = true;
  const saveExcerpt = elements["assistant-save-excerpt"];
  if (saveExcerpt) {
    delete saveExcerpt.dataset.saving;
    saveExcerpt.disabled = false;
    saveExcerpt.textContent = "所选内容存为笔记";
  }
  state.assistantAnswerSelection = null;
  if (clearNativeSelection) window.getSelection()?.removeAllRanges();
}

async function sendAssistantChild() {
  const childSelection = state.assistantAnswerSelection;
  const readerSessionId = state.readerSessionId;
  if (!childSelection || !readerSessionId || state.assistantPending) return;
  const requestKey = assistantLocationKey(childSelection.rootId, childSelection.nodeId);
  if (state.assistantChildRequests.has(requestKey)) return;
  const nodeId = crypto.randomUUID();
  rememberAssistantScroll();
  hideAssistantAnswerActions(true);
  if (!optimisticChildState(childSelection, nodeId)) return;
  state.assistantChildRequests.set(requestKey, nodeId);
  try {
    const payload = await runAssistantStream({
      path: "/api/assistant/child",
      question: childSelection.selectedText,
      retry: (partial) => retryAssistantChild(
        childSelection.rootId, nodeId, { disabled: false }, partial
      ),
      streamKey: assistantLocationKey(childSelection.rootId, nodeId),
      body: {
        reader_session_id: readerSessionId,
        root_id: childSelection.rootId,
        parent_node_id: childSelection.nodeId,
        turn_id: childSelection.turnId,
        start_offset: childSelection.startOffset,
        end_offset: childSelection.endOffset,
        source_spans: childSelection.sourceSpans,
        node_id: nodeId,
        ...assistantSkillField(),
      },
    });
    if (state.readerSessionId === readerSessionId) applyAssistantState(payload.assistant);
  } catch (error) {
    if (state.readerSessionId === readerSessionId
        && error.code !== "ASSISTANT_REQUEST_CANCELLED") {
      markOptimisticChildError(childSelection.rootId, nodeId, error.message, false);
    }
    reportSkillFailure(error);
    if (error.code?.startsWith("AI_")) await refreshAssistantStatus();
  } finally {
    state.assistantChildRequests.delete(requestKey);
    syncModelSelector();
  }
}

async function saveAssistantTurn(button) {
  if (button.disabled || button.dataset.saving === "true") return;
  const readerSessionId = state.readerSessionId;
  const revisionId = state.revision?.id;
  // Reopening keeps the revision id; pending note writes belong to one opening.
  const generation = state.generation;
  const currentReader = () => state.revision?.id === revisionId && state.generation === generation;
  const rootId = button.dataset.rootId;
  const nodeId = button.dataset.nodeId || null;
  const turnId = button.dataset.turnId;
  if (!readerSessionId || !revisionId || !rootId || !turnId) return;
  const turnKey = assistantTurnKey(rootId, nodeId, turnId);
  const saveIntentId = state.assistantSaveIntents.get(turnKey) || crypto.randomUUID();
  state.assistantSaveIntents.set(turnKey, saveIntentId);
  button.dataset.saving = "true";
  button.disabled = true;
  button.textContent = "正在保存…";
  try {
    const payload = await api(`/api/revisions/${revisionId}/assistant/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reader_session_id: readerSessionId,
        root_id: rootId,
        node_id: nodeId,
        turn_id: turnId,
        save_intent_id: saveIntentId,
      }),
    });
    if (!currentReader()) return;
    state.assistantSavedTurns.set(turnKey, payload.annotation.id);
    upsertAnnotation(payload.annotation);
    renderAnnotations(payload.annotation.pdf_page_index);
    updateMarksPanel();
    renderAssistantWorkspace();
    announce("已保存到笔记；AI 审查会随后更新状态。");
    pollSavedExplanationReview(payload.annotation.id, payload.annotation.pdf_page_index, 0);
  } catch (error) {
    if (!currentReader()) return;
    button.disabled = false;
    button.textContent = "重试保存到笔记";
    announce(`保存失败：${error.message || "请稍后重试。"}`, true);
  } finally {
    if (currentReader()) delete button.dataset.saving;
  }
}

// 只把 AI 回答里选中的那段文字存成 AI 笔记：与「保存到笔记」同一条批注链路，
// 因此同样会在 PDF 页面留下 AI 标记并可跳页；区别是不存整段解释、不跑 AI 审查。
async function saveAssistantExcerpt() {
  const selection = state.assistantAnswerSelection;
  const button = elements["assistant-save-excerpt"];
  const readerSessionId = state.readerSessionId;
  const revisionId = state.revision?.id;
  const generation = state.generation;
  const currentReader = () => state.revision?.id === revisionId && state.generation === generation;
  if (!selection || !readerSessionId || !revisionId || !selection.turnId) return;
  if (button.disabled || button.dataset.saving === "true") return;
  const turnKey = assistantTurnKey(selection.rootId, selection.nodeId, selection.turnId);
  // 同一轮可以存多段：幂等键要带选区范围，否则第二段会撞成同一条
  const intentKey = `${turnKey}#${selection.startOffset}-${selection.endOffset}`;
  const saveIntentId = state.assistantSaveIntents.get(intentKey) || crypto.randomUUID();
  state.assistantSaveIntents.set(intentKey, saveIntentId);
  button.dataset.saving = "true";
  button.disabled = true;
  button.textContent = "正在保存…";
  try {
    const payload = await api(`/api/revisions/${revisionId}/assistant/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reader_session_id: readerSessionId,
        root_id: selection.rootId,
        node_id: selection.nodeId,
        turn_id: selection.turnId,
        save_intent_id: saveIntentId,
        excerpt: {
          text: selection.selectedText,
          start: selection.startOffset,
          end: selection.endOffset,
          // 跨粗体/斜体等行内标记时，首尾外沿会把 ** 等标记切进来；
          // 逐段给出可见文本的原文区间，后端按段切片拼接
          spans: selection.sourceSpans,
          // 从渲染 DOM 反向序列化的 markdown 版本；后端仍以 span 切片校验
          // 选区确实来自原回答，通过后优先存这个带格式的版本
          markdown: selection.excerptMarkdown || selection.selectedText,
        },
      }),
    });
    if (!currentReader()) return;
    upsertAnnotation(payload.annotation);
    renderAnnotations(payload.annotation.pdf_page_index);
    updateMarksPanel();
    renderAssistantWorkspace();
    hideAssistantAnswerActions(true);
    announce(
      payload.pdf_sync === "pending"
        ? "所选内容已存为笔记，正在写回 PDF。"
        : "所选内容已存为笔记；点批注面板的「立即写回 PDF」即可写回书页。",
    );
  } catch (error) {
    if (!currentReader()) return;
    button.disabled = false;
    button.textContent = "重试存为笔记";
    announce(`保存失败：${error.message || "请稍后重试。"}`, true);
  } finally {
    delete button.dataset.saving;
  }
}

function upsertAnnotation(annotation) {
  const pageIndex = Number.isInteger(annotation.pdf_page_index) ? annotation.pdf_page_index
    : Number.isInteger(annotation.page_index) ? annotation.page_index : 0;
  if (!Number.isInteger(annotation.pdf_page_index)) annotation.pdf_page_index = pageIndex;
  const values = state.annotationData.get(pageIndex) || [];
  const existing = values.findIndex((value) => value.id === annotation.id);
  const next = [...values];
  if (existing >= 0) next[existing] = annotation;
  else next.push(annotation);
  state.annotationData.set(pageIndex, next);
  trimPageDataCaches();
}

async function refreshAnnotationPage(pageIndex) {
  const revisionId = state.revision?.id;
  const generation = state.generation;
  if (!revisionId) return [];
  const payload = await api(`/api/revisions/${revisionId}/annotations?page=${pageIndex}`);
  if (state.revision?.id !== revisionId || state.generation !== generation) return [];
  state.annotationData.set(pageIndex, payload.annotations);
  trimPageDataCaches();
  renderAnnotations(pageIndex);
  if (pageIndex === state.currentPage) updateMarksPanel();
  return payload.annotations;
}

function pollSavedExplanationReview(annotationId, pageIndex, attempt) {
  const delays = [450, 900, 1800, 3600, 7200, 15000, 30000, 60000];
  const previous = state.assistantReviewPolls.get(annotationId);
  if (previous) clearTimeout(previous);
  if (attempt >= delays.length) {
    state.assistantReviewPolls.delete(annotationId);
    return;
  }
  const revisionId = state.revision?.id;
  const generation = state.generation;
  const currentReader = () => state.revision?.id === revisionId && state.generation === generation;
  const timer = setTimeout(async () => {
    if (!revisionId || !currentReader()) return;
    state.assistantReviewPolls.delete(annotationId);
    try {
      const values = await refreshAnnotationPage(pageIndex);
      if (!currentReader()) return;
      const annotation = values.find((value) => value.id === annotationId);
      if (annotation?.verification_state === "PENDING") {
        pollSavedExplanationReview(annotationId, pageIndex, attempt + 1);
      }
    } catch (_error) {
      if (currentReader()) pollSavedExplanationReview(annotationId, pageIndex, attempt + 1);
    }
  }, delays[attempt]);
  state.assistantReviewPolls.set(annotationId, timer);
}

async function retrySavedExplanationReview(annotation, button) {
  const revisionId = state.revision?.id;
  const generation = state.generation;
  const currentReader = () => state.revision?.id === revisionId && state.generation === generation;
  if (!revisionId || button.disabled) return;
  button.disabled = true;
  button.textContent = "正在重试…";
  try {
    await api(`/api/revisions/${revisionId}/annotations/${annotation.id}/review`, {
      method: "POST",
    });
    if (!currentReader()) return;
    announce("已重新提交 AI 审查；已保存内容不受影响。");
    pollSavedExplanationReview(annotation.id, annotation.pdf_page_index, 0);
  } catch (error) {
    if (currentReader()) {
      button.disabled = false;
      button.textContent = "重试 AI 审查";
      announce(error.message || "AI 审查未能重试。", true);
    }
  }
}

const overlayRequests = new WeakSet();
async function ensureOverlay(index) {
  const wrapper = elements.pages.children[index];
  if (!wrapper?.querySelector("canvas")) return;
  // Rectangle, textbox and saved-answer layers do not need prepared text.
  renderAnnotations(index);
  if (wrapper.querySelector(".text-overlay") || overlayRequests.has(wrapper)) return;
  const generation = state.generation;
  overlayRequests.add(wrapper);
  try {
    await loadOverlay(index, generation);
  } catch (_error) {
    if (generation !== state.generation || !wrapper.isConnected) return;
    if (!wrapper.querySelector(".overlay-retry")) {
      const retry = document.createElement("button");
      retry.className = "preparation-retry overlay-retry";
      retry.textContent = "文字层读取失败 · 重试";
      retry.onclick = () => { retry.remove(); ensureOverlay(index); };
      wrapper.append(retry);
    }
  } finally {
    overlayRequests.delete(wrapper);
  }
}

async function loadOverlay(index, generation) {
  const status = state.preparation.get(index)?.status;
  if (status && status !== "READY") return;
  const revisionId = state.revision?.id;
  if (!revisionId) return;
  const wrapper = elements.pages.children[index];
  if (!wrapper?.querySelector("canvas") || wrapper.querySelector(".text-overlay")) return;
  let data = state.overlayData.get(index);
  if (!data) {
      const payload = await api(`/api/revisions/${revisionId}/overlay?page=${index}`);
      if (payload.page.status !== "READY" || state.revision?.id !== revisionId || state.generation !== generation) return;
      data = payload.page;
      state.overlayData.set(index, data);
      trimPageDataCaches();
  }
  // Mount selectable text before fetching optional annotations.
  if (!wrapper.querySelector("canvas") || wrapper.querySelector(".text-overlay")) return;
  const overlay = document.createElement("div");
  overlay.className = "text-overlay";
  overlay.setAttribute("aria-label", `PDF 第 ${index + 1} 页可选择文字`);
  overlay.dataset.pageIndex = String(index);
  for (const line of data.lines) {
    const bounds = lineBounds(line);
    const marker = document.createElement("span");
    marker.className = "ocr-line";
    marker.dataset.lineOrdinal = String(line.line_ordinal);
    marker.style.cssText = `left:${bounds.x0 * 100}%;top:${bounds.y0 * 100}%;width:${(bounds.x1 - bounds.x0) * 100}%;height:${(bounds.y1 - bounds.y0) * 100}%`;
    marker.textContent = line.text;
    marker.setAttribute("aria-hidden", "true");
    overlay.append(marker);
  }
  overlay.addEventListener("pointerdown", beginSelection);
  overlay.addEventListener("pointermove", extendSelection);
  overlay.addEventListener("pointerup", finishSelection);
  overlay.addEventListener("pointercancel", finishSelection);
  overlay.addEventListener("contextmenu", openSelectionContextMenu);
  wrapper.append(overlay);
  wrapper.querySelector(".overlay-retry")?.remove();
  renderAnnotations(index, overlay);
  renderSearchMatch(index, overlay);
  if (index === state.currentPage) updateMarksPanel();
  if (!state.annotationData.has(index)) refreshAnnotationPage(index).then(() => {
    if (state.generation === generation && overlay.isConnected) renderAnnotations(index, overlay);
  }).catch(() => {});
}

function renderAnnotations(index) {
  if (!elements.pages.children[index]) return;
  annotationUI.render(index, state.annotationData.get(index) || []);
}

function trimPageDataCaches() {
  // Persisted OCR/notes can be read again. Keep recent pages without retaining
  // the whole book's character cells in a long reading session.
  for (const cache of [state.overlayData, state.annotationData]) {
    for (const index of cache.keys()) {
      if (cache.size <= 24) break;
      if (index !== state.currentPage && index !== state.selection?.pageIndex
          && !state.pageProxies.has(index) && !state.pageRenderRequests.has(index)) cache.delete(index);
    }
  }
}

let marksPanelSnapshot = null;
function updateMarksPanel() {
  const annotations = state.annotationData.get(state.currentPage);
  const open = !elements["marks-panel"].hidden;
  // Scroll updates run every frame. Saved annotations are replaced as a group
  // when they change; keep their existing controls and answer layout otherwise.
  if (marksPanelSnapshot?.generation === state.generation
      && marksPanelSnapshot.pageIndex === state.currentPage
      && marksPanelSnapshot.annotations === annotations
      && marksPanelSnapshot.open === open) return;
  const snapshot = { generation: state.generation, pageIndex: state.currentPage, annotations, open };
  const values = annotations || [];
  elements["marks-count"].textContent = String(values.length);
  elements["marks-count"].hidden = values.length === 0;
  elements["marks-toggle"].setAttribute(
    "aria-label", `本页笔记与标记${values.length ? `（${values.length}）` : ""}`,
  );
  elements["marks-page"].textContent = String(state.currentPage + 1);
  if (!open) { marksPanelSnapshot = snapshot; return; }
  const cards = values.map((annotation) => {
    const card = document.createElement("article");
    const hexStyle = isHexColor(annotation.highlight_style) ? annotation.highlight_style : null;
    card.className = `mark-card annotation-style-${hexStyle ? "yellow" : annotation.highlight_style.toLowerCase()}`;
    if (hexStyle) card.style.borderLeftColor = hexStyle;
    card.dataset.annotationId = annotation.id;
    if (annotation.source_kind === "AI_SAVED") {
      card.classList.add("mark-card-ai-saved");
      const heading = document.createElement("div");
      heading.className = "mark-ai-heading";
      const badge = document.createElement("strong");
      badge.textContent = "AI 保存的解释";
      const verification = document.createElement("span");
      verification.className = `mark-verification mark-verification-${annotation.verification_state.toLowerCase()}`;
      verification.textContent = ({
        PENDING: "待 AI 审查",
        PASS: "已通过 AI 审查",
        FAIL: "AI 审查未通过",
        TECHNICAL_FAILURE: "暂时无法审查 · 可重试",
      })[annotation.verification_state] || "审查状态未知";
      heading.append(badge, verification);
      card.append(heading);
      card.append(memory.control(state.revision.id, 'AI_SAVED', annotation.id));

      const source = document.createElement("section");
      source.className = "mark-ai-section";
      const sourceLabel = document.createElement("strong");
      sourceLabel.textContent = "教材来源（SOURCE）";
      const sourceLocation = document.createElement("button");
      sourceLocation.type = "button";
      sourceLocation.className = "mark-source-location";
      sourceLocation.textContent = `PDF 第 ${annotation.pdf_page_index + 1} 页`;
      sourceLocation.addEventListener("click", () => goToPage(annotation.pdf_page_index));
      const quote = document.createElement("q");
      quote.className = "mark-quote";
      quote.textContent = annotation.quote;
      source.append(sourceLabel, sourceLocation, quote);
      card.append(source);

      const provenance = document.createElement("section");
      provenance.className = "mark-ai-section";
      const provenanceLabel = document.createElement("strong");
      provenanceLabel.textContent = "解释路径（PROVENANCE）";
      const path = document.createElement("p");
      path.className = "mark-provenance";
      path.textContent = (annotation.provenance?.concept_path || []).join(" › ");
      provenance.append(provenanceLabel, path);
      if (annotation.provenance?.answer_question
          && annotation.provenance.answer_question !== annotation.provenance.child_focus
          && annotation.provenance.answer_question !== annotation.provenance.root_focus) {
        const question = document.createElement("p");
        question.className = "mark-provenance-question";
        question.textContent = `当时的问题：${annotation.provenance.answer_question}`;
        provenance.append(question);
      }
      card.append(provenance);

      const content = document.createElement("section");
      content.className = "mark-ai-section";
      const contentLabel = document.createElement("strong");
      contentLabel.textContent = "AI 正文（AI CONTENT）";
      const answer = document.createElement("div");
      answer.className = "mark-ai-content assistant-answer-bubble";
      renderAssistantAnswer(answer, annotation.body);
      content.append(contentLabel, answer);
      card.append(content);

      if (annotation.review_summary) {
        const summary = document.createElement("p");
        summary.className = "mark-review-summary";
        summary.textContent = annotation.review_summary;
        card.append(summary);
      }
      if (annotation.review_provider && annotation.review_model) {
        const reviewer = document.createElement("small");
        reviewer.className = "mark-reviewer";
        reviewer.textContent = `审查模型：${annotation.review_provider} · ${annotation.review_model}`;
        card.append(reviewer);
      }
      if (["PENDING", "TECHNICAL_FAILURE"].includes(annotation.verification_state)) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "mark-review-retry";
        retry.textContent = annotation.verification_state === "PENDING"
          ? "重新提交 AI 审查" : "重试 AI 审查";
        retry.addEventListener("click", () => retrySavedExplanationReview(annotation, retry));
        card.append(retry);
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "mark-remove";
      remove.textContent = "删除这条 AI 笔记";
      remove.addEventListener("click", () => deleteAnnotation(annotation));
      card.append(remove);
      return card;
    }
    const quote = document.createElement("p");
    quote.className = "mark-quote";
    quote.textContent = annotation.quote;
    card.append(quote);
    if (annotation.body) {
      const note = document.createElement("p");
      note.className = "mark-note";
      note.textContent = annotation.body;
      card.append(note);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "mark-remove";
    remove.textContent = "删除标记";
    remove.addEventListener("click", () => deleteAnnotation(annotation));
    card.append(remove);
    return card;
  });
  elements["marks-list"].replaceChildren(...cards);
  elements["marks-empty"].hidden = values.length > 0;
  marksPanelSnapshot = snapshot;
}

async function deleteAnnotation(annotation) {
  const question = annotation.source_kind === "AI_SAVED"
    ? "删除这条已保存的 AI 笔记？" : "删除这条标记及其笔记？";
  if (!window.confirm(question)) return;
  const revisionId = state.revision?.id;
  const generation = state.generation;
  const currentReader = () => state.revision?.id === revisionId && state.generation === generation;
  if (!revisionId) return;
  try {
    await api(`/api/revisions/${revisionId}/annotations/${annotation.id}`, { method: "DELETE" });
    if (!currentReader()) return;
    const pageIndex = annotation.pdf_page_index;
    state.annotationData.set(
      pageIndex,
      (state.annotationData.get(pageIndex) || []).filter((value) => value.id !== annotation.id),
    );
    trimPageDataCaches();
    renderAnnotations(pageIndex);
    updateMarksPanel();
    state.annotationPanel?.remove(annotation.id);
    announce("标记已删除。");
  } catch (_error) {
    if (currentReader()) announce("标记未删除，请稍后重试。", true);
  }
}

// Ctrl+Z 撤销：记录本阅读会话内成功创建的批注（高亮/下划线/笔记/文本框/区域高亮），
// 按创建时间倒序逐条撤销（删除该批注）。
const annotationUndoStack = [];

async function undoLastAnnotation() {
  while (annotationUndoStack.length) {
    const entry = annotationUndoStack[annotationUndoStack.length - 1];
    if (entry.revisionId !== state.revision?.id) {
      annotationUndoStack.pop(); // 已切换书籍/会话，丢弃过期记录
      continue;
    }
    break;
  }
  const entry = annotationUndoStack.pop();
  if (!entry) return;
  const annotation = entry.annotation;
  const revisionId = state.revision.id;
  const generation = state.generation;
  const currentReader = () => state.revision?.id === revisionId && state.generation === generation;
  try {
    await api(`/api/revisions/${revisionId}/annotations/${annotation.id}`, { method: "DELETE" });
    if (!currentReader()) return;
    const pageIndex = annotation.pdf_page_index;
    state.annotationData.set(
      pageIndex,
      (state.annotationData.get(pageIndex) || []).filter((value) => value.id !== annotation.id),
    );
    trimPageDataCaches();
    renderAnnotations(pageIndex);
    updateMarksPanel();
    state.annotationPanel?.remove(annotation.id);
    announce("已撤销上一步批注。");
  } catch (_error) {
    if (currentReader()) announce("批注撤销失败，请稍后重试。", true);
  }
}

function syncAnnotationsBadge() {
  const count = annotationPanel.values.length;
  elements["annotations-count"].textContent = String(count);
  elements["annotations-count"].hidden = count === 0;
  elements["annotations-toggle"].setAttribute(
    "aria-label", `全书批注${count ? `（${count}）` : ""}`,
  );
}

async function exportAnnotationsMarkdown() {
  const revisionId = state.revision?.id;
  if (!revisionId) { announce("请先打开一本教材。", true); return; }
  try {
    await downloadMarkdown({
      url: markdownExportUrl(revisionId, "annotations"),
      token: launchToken, scope: "annotations", announce,
    });
  } catch (error) {
    announce(error.message || "导出失败，请稍后重试。", true);
  }
}

async function exportAssistantMarkdown() {
  const revisionId = state.revision?.id;
  const rootId = state.assistantState.current?.root_id
    || state.assistantState.roots[0]?.root_id || null;
  if (!revisionId || !rootId) {
    announce("先在教材中选择文字问 AI，再导出。", true);
    return;
  }
  try {
    await downloadMarkdown({
      url: markdownExportUrl(revisionId, "assistant_conversation", rootId),
      token: launchToken, scope: "assistant_conversation", announce,
    });
  } catch (error) {
    announce(error.message || "导出失败，请稍后重试。", true);
  }
}

async function syncAllAnnotations({ silent = true } = {}) {
  const revisionId = state.revision?.id;
  if (!revisionId) throw new Error("请先打开一本教材。");
  // 目标（源文件 / 库内副本）由服务端保存的写回设置决定，这里不重复提交。
  const result = await api(`/api/revisions/${revisionId}/annotations/sync-all`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const written = Number.isFinite(result?.written) ? result.written : null;
  const skipped = Array.isArray(result?.skipped) ? result.skipped.length : 0;
  const message = written === null
    ? "批注已写回 PDF。"
    : skipped
      ? `已写回 ${written} 条批注到 PDF，跳过 ${skipped} 条（页码越界、无法渲染或写回失败）。`
      : `已写回 ${written} 条批注到 PDF。`;
  if (!silent) announce(message);
  return message;
}

function updateOcr(ocr) {
  if (!ocr) return;
  latestOcr = ocr;
  ocrBanner.update(ocr);
  settings?.setOcr(ocr);
}

// A saved skill can disappear between selections; say so and fall back instead of
// silently sending a request the service will reject.
function reportSkillFailure(error) {
  if (!(error?.status === 404 || error?.status === 400)) return false;
  const name = skillPicker.value();
  if (!name) return false;
  announce(`技能不可用（${skillStore.label(name)}），已改用内置助手。`, true);
  skillStore.remember(state.book?.id, "");
  skillPicker.sync();
  return true;
}

function assistantSkillField() {
  const name = skillPicker.value();
  return name ? { skill: name } : {};
}

function selectionPoint(event, overlay) {
  const index = Number(overlay.dataset.pageIndex);
  const data = state.overlayData.get(index);
  if (!data?.lines.length) return null;
  const rect = overlay.getBoundingClientRect();
  const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
  const line = nearestLine(data.lines, x, y);
  return {
    pageIndex: index,
    lineOrdinal: line.line_ordinal,
    boundary: nearestCellBoundary(line, x, y),
  };
}

function beginSelection(event) {
  if (event.button !== 0) return;
  const point = selectionPoint(event, event.currentTarget);
  if (!point) return;
  event.preventDefault();
  clearSelection();
  state.selection = { pageIndex: point.pageIndex, anchor: point, focus: point, resolved: [] };
  state.selecting = true;
  event.currentTarget.setPointerCapture(event.pointerId);
  elements.viewer.focus({ preventScroll: true });
}

function extendSelection(event) {
  if (!state.selecting || state.selection?.pageIndex !== Number(event.currentTarget.dataset.pageIndex)) return;
  const point = selectionPoint(event, event.currentTarget);
  if (!point) return;
  state.selection.focus = point;
  renderSelection();
}

function finishSelection(event) {
  if (!state.selecting) return;
  extendSelection(event);
  state.selecting = false;
  // 拖拽成功解析出选区后，自动在松手位置弹出选区操作菜单（高亮/下划线/批注/问 AI），
    // 不再要求用户额外右键；右键触发路径保持不变。
  if (state.selection?.resolved.length) {
    showSelectionActions(event.clientX, event.clientY);
  } else {
    hideSelectionActions();
  }
}

function renderSelection() {
  if (!state.selection) return;
  document.querySelectorAll(".selection-quad").forEach((node) => node.remove());
  const data = state.overlayData.get(state.selection.pageIndex);
  state.selection.resolved = resolveSelection(data.lines, state.selection.anchor, state.selection.focus);
  syncAskEligibility();
  const overlay = elements.pages.children[state.selection.pageIndex]?.querySelector(".text-overlay");
  if (!overlay) return;
  for (const quad of selectionPresentationQuads(state.selection.resolved)) {
    const xs = quad.map(([x]) => x);
    const ys = quad.map(([, y]) => y);
    const marker = document.createElement("span");
    marker.className = "selection-quad";
    marker.style.cssText = `left:${Math.min(...xs) * 100}%;top:${Math.min(...ys) * 100}%;width:${(Math.max(...xs) - Math.min(...xs)) * 100}%;height:${(Math.max(...ys) - Math.min(...ys)) * 100}%`;
    overlay.append(marker);
  }
  syncNativeSelection(overlay);
  hideSelectionActions();
}

let selectionMenuAnchor = null;
function selectionMenuSourceAnchor() {
  if (!state.selection?.resolved.length) return null;
  const marker = elements.pages.children[state.selection.pageIndex]?.querySelector(".selection-quad");
  if (!marker) return null;
  const { left, top, width, height } = marker.getBoundingClientRect();
  return width > 0 && height > 0 ? { selection: state.selection, left, top, width, height } : null;
}

function handleSelectionMenuScroll() {
  if (elements["selection-actions"].hidden) return;
  const current = selectionMenuSourceAnchor();
  // A late learning footer can shift document offsets while browser anchoring
  // keeps the selected words stationary. Dismiss only when their view changes.
  if (selectionMenuAnchor && current && current.selection === selectionMenuAnchor.selection
      && ["left", "top", "width", "height"].every(key => Math.abs(current[key] - selectionMenuAnchor[key]) <= .5)) return;
  hideSelectionActions();
}

function showSelectionActions(clientX, clientY) {
  if (selectionActionsDismissal) {
    const animation = selectionActionsDismissal;
    selectionActionsDismissal = null;
    animation.cancel();
  }
  elements["selection-actions"].style.removeProperty("pointer-events");
  for (const id of ["save-highlight", "save-underline", "add-note", "cancel-selection"]) {
    if (elements[id]) elements[id].hidden = Boolean(state.guideSelection);
  }
  elements["selection-actions"].querySelectorAll(".highlight-styles").forEach((node) => { node.hidden = true; });
  elements["save-highlight"].setAttribute("aria-expanded", "false");
  elements["save-underline"].setAttribute("aria-expanded", "false");
  state.selectionMenuPoint = { x: clientX, y: clientY };
  selectionMenuAnchor = selectionMenuSourceAnchor();
  elements["selection-actions"].hidden = false;
  positionSelectionActions();
}

function positionSelectionActions() {
  if (!(state.selection?.resolved.length || state.guideSelection) || !state.selectionMenuPoint
      || elements["selection-actions"].hidden) return;
  const point = state.selectionMenuPoint;
  const menu = elements["selection-actions"];
  requestAnimationFrame(() => {
    if (menu.hidden || !(state.selection || state.guideSelection)) return;
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    const below = point.y + 5;
    const above = point.y - height - 5;
    const top = below + height <= window.innerHeight - 8 ? below : above;
    menu.style.left = `${Math.max(8, Math.min(window.innerWidth - width - 8, point.x + 5))}px`;
    menu.style.top = `${Math.max(76, Math.min(window.innerHeight - height - 8, top))}px`;
  });
}

function hideSelectionActions({ animate = false } = {}) {
  state.guideSelection = null;
  const menu = elements["selection-actions"];
  const finalize = () => {
    menu.hidden = true;
    menu.style.removeProperty("opacity");
    menu.style.removeProperty("transform");
    menu.style.removeProperty("pointer-events");
  };
  if (selectionActionsDismissal) {
    const previous = selectionActionsDismissal;
    selectionActionsDismissal = null;
    previous.cancel();
  }
  if (animate && !menu.hidden && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    menu.style.pointerEvents = "none";
    const animation = menu.animate([
      { opacity: 1, transform: "translate3d(0, 0, 0)" },
      { opacity: 0, transform: "translate3d(0, 2px, 0)" },
    ], { duration: 90, easing: "cubic-bezier(.2,.72,.22,1)", fill: "both" });
    selectionActionsDismissal = animation;
    animation.finished.catch(() => {}).then(() => {
      if (selectionActionsDismissal !== animation) return;
      selectionActionsDismissal = null;
      animation.cancel();
      finalize();
    });
  } else {
    finalize();
  }
  elements["note-editor"].hidden = true;
  elements["add-note"].setAttribute("aria-expanded", "false");
  elements["annotation-note"].value = "";
  document.querySelector('input[name="highlight-style"][value="YELLOW"]').checked = true;
  state.selectionMenuPoint = null;
  selectionMenuAnchor = null;
}

function syncNativeSelection(overlay) {
  const nativeSelection = window.getSelection();
  nativeSelection.removeAllRanges();
  if (!state.selection?.resolved.length) return;
  const first = state.selection.resolved[0];
  const last = state.selection.resolved.at(-1);
  const start = overlay.querySelector(`.ocr-line[data-line-ordinal="${first.line_ordinal}"]`)?.firstChild;
  const end = overlay.querySelector(`.ocr-line[data-line-ordinal="${last.line_ordinal}"]`)?.firstChild;
  if (!start || !end) return;
  const range = document.createRange();
  range.setStart(start, first.char_start);
  range.setEnd(end, last.char_end);
  nativeSelection.addRange(range);
}

function openSelectionContextMenu(event) {
  if (!state.selection?.resolved.length
      || state.selection.pageIndex !== Number(event.currentTarget.dataset.pageIndex)) {
    hideSelectionActions();
    return;
  }
  const overlay = event.currentTarget;
  const rect = overlay.getBoundingClientRect();
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  const inside = state.selection.resolved.some((range) => range.quads.some((quad) => {
    const xs = quad.map(([value]) => value);
    const ys = quad.map(([, value]) => value);
    return x >= Math.min(...xs) && x <= Math.max(...xs)
      && y >= Math.min(...ys) && y <= Math.max(...ys);
  }));
  if (!inside) {
    hideSelectionActions();
    return;
  }
  syncNativeSelection(overlay);
  event.preventDefault();
  showSelectionActions(event.clientX, event.clientY);
}

function clearSelection({ animateActions = false } = {}) {
  document.querySelectorAll(".selection-quad").forEach((node) => node.remove());
  window.getSelection()?.removeAllRanges();
  state.selection = null;
  state.selecting = false;
  syncAskEligibility();
  hideSelectionActions({ animate: animateActions });
}

function openVisibleSelectionActions(clientX, clientY, text, explain) {
  hideSelectionActions();
  state.selection = null;
  document.querySelectorAll(".selection-quad").forEach((node) => node.remove());
  state.guideSelection = { text, explain };
  syncAskEligibility();
  showSelectionActions(clientX, clientY);
}

function selectedHighlightStyle() {
  return document.querySelector('input[name="highlight-style"]:checked')?.value || "YELLOW";
}

function selectedUnderlineStyle() {
  return document.querySelector('input[name="underline-style"]:checked')?.value || "RED";
}

async function saveAnnotation(body = null, kind = "HIGHLIGHT", colorOverride = null) {
  const selection = state.selection;
  const revisionId = state.revision?.id;
  const generation = state.generation;
  const currentReader = () => state.revision?.id === revisionId && state.generation === generation;
  if (!selection?.resolved.length || !revisionId) return;
  elements["save-highlight"].disabled = true;
  elements["save-note"].disabled = true;
  try {
    const color = colorOverride
      || (kind === "UNDERLINE" ? selectedUnderlineStyle() : selectedHighlightStyle());
    const payload = await api(`/api/revisions/${revisionId}/annotations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // kind/page_index/color are the current contract; the two aliases keep older
      // local services working while the endpoint is upgraded.
      body: JSON.stringify({
        kind,
        page_index: selection.pageIndex,
        pdf_page_index: selection.pageIndex,
        start: {
          line_ordinal: selection.anchor.lineOrdinal,
          boundary: selection.anchor.boundary,
        },
        end: {
          line_ordinal: selection.focus.lineOrdinal,
          boundary: selection.focus.boundary,
        },
        body,
        color,
        highlight_style: color,
      }),
    });
    if (!currentReader()) return;
    const values = state.annotationData.get(selection.pageIndex) || [];
    state.annotationData.set(selection.pageIndex, [...values, payload.annotation]);
    trimPageDataCaches();
    annotationUndoStack.push({ revisionId, annotation: payload.annotation });
    const pageIndex = selection.pageIndex;
    clearSelection();
    renderAnnotations(pageIndex);
    updateMarksPanel();
    announce(payload.annotation.body ? "笔记已保存。"
      : kind === "UNDERLINE" ? "下划线已保存。" : "高亮已保存。");
    state.annotationPanel?.upsert(payload.annotation);
  } catch (_error) {
    if (currentReader()) announce("标记未保存，请稍后重试。", true);
  } finally {
    if (currentReader()) {
      elements["save-highlight"].disabled = false;
      elements["save-note"].disabled = false;
    }
  }
}

async function copySelection() {
  const text = state.guideSelection?.text || resolvedText(state.selection?.resolved || []);
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    hideSelectionActions();
    announce("已复制所选文字。");
  } catch (_error) {
    announce("所选文字未复制，请重试。", true);
  }
}

function moveSelectionFocus(key) {
  if (!state.selection) return;
  const data = state.overlayData.get(state.selection.pageIndex);
  const lines = data.lines;
  let lineIndex = lines.findIndex((line) => line.line_ordinal === state.selection.focus.lineOrdinal);
  let boundary = state.selection.focus.boundary;
  if (key === "ArrowLeft") boundary -= 1;
  if (key === "ArrowRight") boundary += 1;
  if (key === "ArrowUp") lineIndex -= 1;
  if (key === "ArrowDown") lineIndex += 1;
  lineIndex = Math.max(0, Math.min(lines.length - 1, lineIndex));
  boundary = Math.max(0, Math.min(lines[lineIndex].cells.length, boundary));
  state.selection.focus = {
    pageIndex: state.selection.pageIndex,
    lineOrdinal: lines[lineIndex].line_ordinal,
    boundary,
  };
  renderSelection();
}

function initializeKeyboardSelection() {
  const data = state.overlayData.get(state.currentPage);
  const firstLine = data?.lines.find((line) => line.cells.length);
  if (!firstLine) return false;
  const point = {
    pageIndex: state.currentPage,
    lineOrdinal: firstLine.line_ordinal,
    boundary: 0,
  };
  state.selection = {
    pageIndex: state.currentPage,
    anchor: point,
    focus: { ...point },
    resolved: [],
  };
  return true;
}

function nextFrame() {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

elements["import-input"].addEventListener("change", () => {
  const file = elements["import-input"].files[0];
  if (file) importPdf(file);
});
elements.viewer.addEventListener("scroll", scheduleViewportUpdate, { passive: true });
elements["previous-page"].addEventListener("click", () => goToPage(state.currentPage - 1));
elements["next-page"].addEventListener("click", () => goToPage(state.currentPage + 1));
elements["page-number"].addEventListener("change", () => goToPage(Number(elements["page-number"].value) - 1));
elements["page-number"].addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    goToPage(Number(elements["page-number"].value) - 1);
    elements.viewer.focus();
  }
});
elements["zoom-out"].addEventListener("click", () => setZoom(adjacentZoom(-1)));
elements["zoom-in"].addEventListener("click", () => setZoom(adjacentZoom(1)));
elements["reader-more-toggle"].addEventListener("click", () => {
  const open = !elements["reader-more"].classList.contains("open");
  elements["reader-more"].classList.toggle("open", open);
  elements["reader-more-toggle"].setAttribute("aria-expanded", String(open));
});
elements["reader-more"].addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !elements["reader-more"].classList.contains("open")) return;
  event.preventDefault();
  elements["reader-more"].classList.remove("open");
  elements["reader-more-toggle"].setAttribute("aria-expanded", "false");
  elements["reader-more-toggle"].focus();
});
elements["back-to-library"].addEventListener("click", returnToLibrary);
elements["outline-toggle"].addEventListener("click", () => {
  if (elements["outline-panel"].hidden) openDirectory();
  else closeDirectory();
});
elements["outline-close"].addEventListener("click", () => closeDirectory());
elements["outline-panel"].addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  event.preventDefault();
  closeDirectory({ focusViewer: true });
});
elements["search-toggle"].addEventListener("click", () => {
  const opening = elements["search-panel"].hidden;
  elements["search-panel"].hidden = !opening;
  elements["search-toggle"].setAttribute("aria-expanded", String(opening));
  if (opening) {
    chapterEntry.close();
    setAssistantPanelOpen(false);
    closeDirectory();
    elements["marks-panel"].hidden = true;
    elements["marks-toggle"].setAttribute("aria-expanded", "false");
    runSearch();
    elements["search-query"].focus({ preventScroll: true });
  } else {
    state.searchRequest += 1;
    clearSearchMatch();
  }
});
elements["search-close"].addEventListener("click", () => {
  elements["search-panel"].hidden = true;
  elements["search-toggle"].setAttribute("aria-expanded", "false");
  state.searchRequest += 1;
  clearSearchMatch();
});
elements["search-form"].addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch();
});
elements["search-query"].addEventListener("input", () => {
  if (!elements["search-query"].value.trim()) {
    state.searchRequest += 1;
    clearSearchMatch();
    elements["search-results"].replaceChildren();
    elements["search-empty"].textContent = "输入关键词以搜索已准备页面。";
    elements["search-empty"].hidden = false;
  }
});
elements["marks-toggle"].addEventListener("click", async () => {
  const opening = elements["marks-panel"].hidden;
  elements["marks-panel"].hidden = !opening;
  elements["marks-toggle"].setAttribute("aria-expanded", String(opening));
  if (opening) {
    chapterEntry.close();
    setAssistantPanelOpen(false);
    closeDirectory();
    elements["search-panel"].hidden = true;
    elements["search-toggle"].setAttribute("aria-expanded", "false");
    state.searchRequest += 1;
    clearSearchMatch();
    try {
      await refreshAnnotationPage(state.currentPage);
    } catch (_error) {
      announce("标记暂时无法刷新，请稍后重试。", true);
    }
  }
  updateMarksPanel();
});
elements["marks-close"].addEventListener("click", () => {
  elements["marks-panel"].hidden = true;
  elements["marks-toggle"].setAttribute("aria-expanded", "false");
});

/* ---------- 笔记抽屉：内嵌独立笔记页，自动带入当前教材与页码 ---------- */
const NOTES_CONTEXT_MESSAGE = "reader-context";

function notesContextSnapshot() {
  const revision = state.revision;
  if (!revision || elements.reader.hidden) return null;
  return {
    revisionId: revision.id,
    bookTitle: state.book?.title || "",
    page: state.currentPage,
  };
}

// 首次打开就带着上下文，笔记页因此不必等 postMessage 才认识当前教材。
function notesFrameSrc() {
  const query = new URLSearchParams({ embed: "1" });
  if (launchToken) query.set("token", launchToken);
  const context = notesContextSnapshot();
  if (context?.revisionId) query.set("book", context.revisionId);
  if (context?.bookTitle) query.set("bookTitle", context.bookTitle);
  if (Number.isInteger(context?.page)) query.set("page", String(context.page));
  return `/notes.html?${query.toString()}`;
}

// 翻页、换书都会走到这里：只更新抽屉里的草稿预填与来源提示，不动已保存的笔记。
function pushNotesContext() {
  const panel = elements["notes-panel"];
  if (!panel || panel.hidden) return;
  const hint = elements["notes-panel-context"];
  const context = notesContextSnapshot();
  if (!context) {
    if (hint) hint.textContent = "尚未打开教材";
    return;
  }
  if (hint) hint.textContent = `${context.bookTitle || "当前教材"} · 第 ${context.page + 1} 页`;
  const frame = elements["notes-frame"];
  try {
    frame?.contentWindow?.postMessage(
      { type: NOTES_CONTEXT_MESSAGE, ...context },
      location.origin,
    );
  } catch (_error) {
    // 同源窗口不会抛错；万一跨源，下次 load 会重新带上 URL 参数
  }
}

function setNotesPanelOpen(open, { focus = false } = {}) {
  const panel = elements["notes-panel"];
  const toggle = elements["notes-toggle"];
  if (!panel || !toggle) return;
  if (open) {
    const frame = elements["notes-frame"];
    if (frame && !frame.dataset.ready) {
      frame.dataset.ready = "1";
      frame.src = notesFrameSrc();
      frame.addEventListener("load", () => pushNotesContext());
    }
    // 抽屉会把 PDF 列压窄；其他浮层先收起，避免面板互相叠压
    chapterEntry.close();
    closeDirectory();
    guide.close();
    // 问 AI 可以和笔记并排（PDF｜笔记｜AI）；只有 AI 已全屏展开或窗口太窄时才收起它
    if (state.assistantExpanded || elements.reader.clientWidth <= 900) {
      setAssistantPanelOpen(false);
    }
    for (const id of ["search-panel", "marks-panel"]) {
      const overlay = document.getElementById(id);
      if (overlay) overlay.hidden = true;
    }
    elements["marks-toggle"].setAttribute("aria-expanded", "false");
    elements["search-toggle"].setAttribute("aria-expanded", "false");
    state.searchRequest += 1;
    clearSearchMatch();
    panel.hidden = false;
    elements.reader.classList.add("notes-open");
    toggle.setAttribute("aria-expanded", "true");
    pushNotesContext();
    return;
  }
  if (panel.hidden) return;
  panel.hidden = true;
  elements.reader.classList.remove("notes-open");
  toggle.setAttribute("aria-expanded", "false");
  if (focus) toggle.focus({ preventScroll: true });
}

elements["notes-toggle"].addEventListener("click", () => {
  setNotesPanelOpen(elements["notes-panel"].hidden);
});
elements["notes-panel-close"].addEventListener("click", () => setNotesPanelOpen(false, { focus: true }));
elements["copy-selection"].addEventListener("click", copySelection);
elements["ask-selection"].addEventListener("click", stageAssistantSelection);

// 选区里的「词典 / 拼音」不再是写死的两个按钮：它们来自插件注册表
// （plugins/dict、plugins/pinyin 的 plugin.json），停用插件按钮就消失。
function selectedTextForPlugins() {
  return (state.guideSelection?.text || resolvedText(state.selection?.resolved || [])).trim();
}

function currentPluginSelection() {
  return {
    text: selectedTextForPlugins(),
    page: state.selection?.pageIndex ?? state.currentPage ?? null,
    bookId: state.book?.id || null,
    bookTitle: state.book?.title || null,
  };
}

function currentThemeName() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function searchInBookFromPlugin(text) {
  if (!text) return;
  elements["search-panel"].hidden = false;
  elements["search-toggle"].setAttribute("aria-expanded", "true");
  elements["search-query"].value = text;
  chapterEntry.close();
  setAssistantPanelOpen(false);
  closeDirectory();
  elements["marks-panel"].hidden = true;
  elements["marks-toggle"].setAttribute("aria-expanded", "false");
  runSearch();
  elements["search-query"].focus({ preventScroll: true });
}

const pluginHost = createPluginHost({
  request: api,
  announce,
  getContext: currentPluginSelection,
  getTheme: currentThemeName,
  onSearchInBook: searchInBookFromPlugin,
});
pluginBridge.refresh = () => pluginHost.refresh();
pluginBridge.pushTheme = (theme) => pluginHost.pushTheme(theme);
pluginBridge.openWindow = (id) => pluginHost.openWindow(id);
pluginHost.bind();
pluginHost.refresh();
window.pluginHost = pluginHost;
window.themeController = themeController;
elements["assistant-toggle"].addEventListener("click", () => {
  if (!state.assistantDraft && !state.assistantState.roots.length) return;
  const opening = elements["assistant-panel"].hidden || elements["assistant-panel"].classList.contains("master-active");
  if (opening) {
    openAssistantPanel();
  } else {
    setAssistantPanelOpen(false, { focusViewer: true });
  }
});
elements["assistant-expand"].addEventListener("click", () => {
  setAssistantExpanded(!state.assistantExpanded);
});
elements["assistant-resize-handle"].addEventListener("pointerdown", beginAssistantResize);
elements["assistant-resize-handle"].addEventListener("pointermove", continueAssistantResize);
elements["assistant-resize-handle"].addEventListener("pointerup", finishAssistantResize);
elements["assistant-resize-handle"].addEventListener("pointercancel", finishAssistantResize);
elements["assistant-resize-handle"].addEventListener("keydown", (event) => {
  if (state.assistantExpanded) return;
  const { minimum, maximum } = assistantDockLimits();
  let width = state.assistantDockWidth;
  if (event.key === "ArrowLeft") width += 16;
  else if (event.key === "ArrowRight") width -= 16;
  else if (event.key === "Home") width = minimum;
  else if (event.key === "End") width = maximum;
  else return;
  event.preventDefault();
  applyAssistantDockWidth(width);
});
elements["assistant-model"].addEventListener("change", () => {
  if ((state.assistantState.current && !state.assistantDraft) || state.assistantPending) {
    syncModelSelector();
    return;
  }
  state.assistantActiveProvider = elements["assistant-model"].value;
  applySelectedProviderStatus();
  elements["assistant-turns"].querySelectorAll(".assistant-error").forEach((node) => node.remove());
  if (state.assistantDraft) renderAssistantDraft();
  else resetAssistantPanel();
  refreshAssistantStatus();
});
elements["assistant-first-turn"].addEventListener("submit", (event) => {
  event.preventDefault();
  sendAssistantFirstTurn();
});
elements["assistant-root-switcher"].addEventListener("change", () => {
  const rootId = elements["assistant-root-switcher"].value;
  const root = state.assistantState.roots.find((candidate) => candidate.root_id === rootId);
  focusAssistant(rootId, root?.focused_node_id || null);
});
elements["assistant-back"].addEventListener("click", () => {
  const parent = state.assistantState.current?.parent_ref;
  if (parent) focusAssistant(parent.root_id, parent.node_id);
});
elements["assistant-child-list"].addEventListener("click", (event) => {
  const button = event.target.closest("button[data-node-id]");
  if (button) focusAssistant(button.dataset.rootId, button.dataset.nodeId);
});
elements["assistant-breadcrumb"].addEventListener("click", (event) => {
  const button = event.target.closest("button[data-root-id]");
  if (button && !button.disabled) focusAssistant(button.dataset.rootId, button.dataset.nodeId || null);
});
elements["assistant-close-root"].addEventListener("click", closeFocusedAssistantLevel);
document.getElementById('assistant-region-thumbnail').addEventListener('click', openAssistantRegionImage);
document.getElementById('assistant-region-open').addEventListener('click', openAssistantRegionImage);
document.getElementById('assistant-region-dialog-close').addEventListener('click', closeAssistantRegionImage);
document.getElementById('assistant-region-dialog').addEventListener('close', () => {
  document.getElementById('assistant-region-full-image').removeAttribute('src');
});
document.getElementById('assistant-region-dialog').addEventListener('click', event => {
  if (event.target === event.currentTarget) {
    const bounds = event.currentTarget.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right
        || event.clientY < bounds.top || event.clientY > bounds.bottom) closeAssistantRegionImage();
  }
});
elements["assistant-close"].addEventListener("click", () => {
  setAssistantPanelOpen(false, { focusViewer: true });
});
elements["assistant-follow-up"].addEventListener("submit", (event) => {
  event.preventDefault();
  sendAssistantFollowUp();
});
elements["assistant-turns"].addEventListener("pointerup", (event) => {
  hideAssistantAnswerActions();
  // 左键抬起时暂存有效选区：右键按下会把选区塌陷掉（浏览器原生行为），
  // 之后的 contextmenu 靠这份暂存才能弹出「所选内容存为笔记」工具栏。
  if (event.button === 0) {
    const mapped = mapAssistantAnswerSelection({ quiet: true });
    state.assistantAnswerSelectionStash = mapped
      ? { range: mapped.range, data: { ...mapped, range: undefined } }
      : null;
  }
});
elements["assistant-turns"].addEventListener("contextmenu", openAssistantAnswerContextMenu);
elements["assistant-turns"].addEventListener("click", (event) => {
  const save = event.target.closest(".assistant-save-note");
  if (save) saveAssistantTurn(save);
});
elements["assistant-ask-deeper"].addEventListener("click", sendAssistantChild);
elements["assistant-save-excerpt"].addEventListener("click", saveAssistantExcerpt);
elements["assistant-cancel-selection"].addEventListener("click", () => {
  hideAssistantAnswerActions(true);
});
elements["save-highlight"].addEventListener("click", () => {
  const colors = document.getElementById("highlight-colors");
  const underlineColors = document.getElementById("underline-colors");
  if (underlineColors) underlineColors.hidden = true;
  if (colors.hidden) {
    colors.hidden = false;
    elements["save-highlight"].setAttribute("aria-expanded", "true");
    positionSelectionActions();
    return;
  }
  saveAnnotation();
});
elements["save-underline"].addEventListener("click", () => {
  const colors = document.getElementById("underline-colors");
  const highlightColors = document.getElementById("highlight-colors");
  highlightColors.hidden = true;
  elements["save-highlight"].setAttribute("aria-expanded", "false");
  if (colors.hidden) {
    colors.hidden = false;
    elements["save-underline"].setAttribute("aria-expanded", "true");
    positionSelectionActions();
    return;
  }
  saveAnnotation(null, "UNDERLINE");
});
document.querySelectorAll('input[name="underline-style"]').forEach((input) => {
  input.addEventListener("change", () => saveAnnotation(null, "UNDERLINE"));
});
document.querySelectorAll('input[name="highlight-style"]').forEach((input) => {
  input.addEventListener("change", () => saveAnnotation());
});
// 自定义颜色（其他颜色 + 屏幕取色器）。EyeDropper 是 WebView2/Chromium 原生 API。
function bindCustomAnnotationColor({ pickerId, eyedropperId, apply }) {
  const picker = document.getElementById(pickerId);
  if (picker) {
    picker.addEventListener("input", () => {
      if (picker.value) apply(picker.value.toUpperCase());
    });
  }
  const dropper = eyedropperId ? document.getElementById(eyedropperId) : null;
  if (dropper) {
    dropper.addEventListener("click", async () => {
      if (!window.EyeDropper) {
        announce("当前环境不支持取色器，请用左侧色块自定义颜色。", true);
        return;
      }
      try {
        const result = await new window.EyeDropper().open();
        const hex = String(result.sRGBHex || "").toUpperCase();
        if (!isHexColor(hex)) return;
        if (picker) picker.value = hex.toLowerCase();
        apply(hex);
      } catch (_error) {
        // 用户按 Esc 取消取色，无需提示
      }
    });
  }
}
bindCustomAnnotationColor({
  pickerId: "highlight-custom-color",
  eyedropperId: "highlight-eyedropper",
  apply: (hex) => saveAnnotation(null, "HIGHLIGHT", hex),
});
bindCustomAnnotationColor({
  pickerId: "underline-custom-color",
  eyedropperId: "underline-eyedropper",
  apply: (hex) => saveAnnotation(null, "UNDERLINE", hex),
});
bindCustomAnnotationColor({
  pickerId: "annotation-tool-custom-color",
  eyedropperId: null,
  apply: (hex) => annotationUI.setColor(hex),
});
elements["add-note"].addEventListener("click", () => {
  const opening = elements["note-editor"].hidden;
  elements["note-editor"].hidden = !opening;
  elements["add-note"].setAttribute("aria-expanded", String(opening));
  positionSelectionActions();
  if (opening) elements["annotation-note"].focus({ preventScroll: true });
});
elements["note-editor"].addEventListener("submit", (event) => {
  event.preventDefault();
  saveAnnotation(elements["annotation-note"].value);
});
elements["cancel-selection"].addEventListener("click", clearSelection);
function toggleAnnotationsPanel() {
  const opening = elements["annotations-panel"].hidden;
  elements["annotations-panel"].hidden = !opening;
  elements["annotations-toggle"].setAttribute("aria-expanded", String(opening));
  if (!opening) return;
  chapterEntry.close();
  setAssistantPanelOpen(false);
  closeDirectory();
  elements["search-panel"].hidden = true;
  elements["marks-panel"].hidden = true;
  elements["search-toggle"].setAttribute("aria-expanded", "false");
  elements["marks-toggle"].setAttribute("aria-expanded", "false");
  annotationPanel.open();
}
elements["annotations-toggle"].addEventListener("click", toggleAnnotationsPanel);
elements["annotations-close"].addEventListener("click", () => {
  elements["annotations-panel"].hidden = true;
  elements["annotations-toggle"].setAttribute("aria-expanded", "false");
});
elements["annotation-textbox"].addEventListener("click", () => {
  annotationUI.setMode(annotationUI.mode === "TEXTBOX" ? null : "TEXTBOX");
});
elements["annotation-region"].addEventListener("click", () => {
  annotationUI.setMode(annotationUI.mode === "REGION" ? null : "REGION");
});
elements["annotation-sticky"].addEventListener("click", () => {
  annotationUI.setMode(annotationUI.mode === "NOTE" ? null : "NOTE");
});
elements["reader-settings"].addEventListener("click", () => {
  elements["reader-more"].classList.remove("open");
  elements["reader-more-toggle"].setAttribute("aria-expanded", "false");
  settings.open();
});
elements["assistant-export"].addEventListener("click", exportAssistantMarkdown);
elements["assistant-history"].addEventListener("click", () => assistantHistoryUI.open());
elements["outline-import"].addEventListener("click", async () => {
  const revisionId = state.revision?.id;
  const button = elements["outline-import"];
  if (!revisionId || button.disabled) return;
  button.disabled = true;
  button.textContent = "正在导入…";
  try {
    const result = await api(`/api/revisions/${revisionId}/outline/import-bookmarks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    await loadBookMap();
    const imported = Number.isFinite(result?.imported) ? result.imported : null;
    if (imported === 0) announce("这本书的 PDF 没有可导入的书签。");
    else announce(imported === null ? "已导入 PDF 书签。" : `已导入 ${imported} 个 PDF 书签。`);
  } catch (error) {
    announce(error.message || "书签导入失败，请稍后重试。", true);
  } finally {
    button.disabled = false;
    button.textContent = "导入 PDF 书签";
  }
});
elements["selection-actions"].addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    clearSelection();
    elements.viewer.focus({ preventScroll: true });
  }
});
elements.viewer.addEventListener("pointerdown", () => {
  state.pinnedLanding = null;
  elements.viewer.focus({ preventScroll: true });
});
elements.viewer.addEventListener("contextmenu", (event) => {
  if (!event.defaultPrevented) hideSelectionActions();
});
elements.viewer.addEventListener("scroll", handleSelectionMenuScroll, { passive: true });
elements.viewer.addEventListener("wheel", (event) => {
  state.pinnedLanding = null;
  if (!event.ctrlKey) return;
  event.preventDefault();
  const direction = event.deltaY < 0 ? 1 : -1;
  setZoom(adjacentZoom(direction), captureZoomAnchor(event.clientX, event.clientY));
}, { passive: false });
elements.viewer.addEventListener("keydown", (event) => {
  if (visualSelection.active) return;
  if (!event.ctrlKey) state.pinnedLanding = null;
  if (event.key === "Escape" && state.selection) {
    event.preventDefault();
    clearSelection();
    return;
  }
  if (event.key === "Escape" && !elements["outline-panel"].hidden) {
    event.preventDefault();
    closeDirectory();
    return;
  }
  if (event.shiftKey && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
      && (state.selection || initializeKeyboardSelection())) {
    event.preventDefault();
    moveSelectionFocus(event.key);
    return;
  }
  if (!event.ctrlKey) return;
  if (event.key === "+" || event.key === "=") {
    event.preventDefault();
    setZoom(adjacentZoom(1));
  } else if (event.key === "-") {
    event.preventDefault();
    setZoom(adjacentZoom(-1));
  }
});
document.addEventListener("copy", (event) => {
  const native = window.getSelection();
  if (native?.anchorNode?.parentElement?.closest(".guide-text,.inline-text")) return;
  const text = resolvedText(state.selection?.resolved || []);
  if (!text) return;
  event.preventDefault();
  event.clipboardData.setData("text/plain", text);
  announce("已复制所选文字。");
});
document.addEventListener("pointerdown", (event) => {
  if (elements["reader-more"].classList.contains("open") && !elements["reader-more"].contains(event.target)
      && !document.getElementById("reading-display-menu").contains(event.target)) {
    elements["reader-more"].classList.remove("open");
    elements["reader-more-toggle"].setAttribute("aria-expanded", "false");
  }
  if (!elements["selection-actions"].hidden
      && !elements["selection-actions"].contains(event.target)) hideSelectionActions();
  if (!elements["assistant-answer-actions"].hidden
      && !elements["assistant-answer-actions"].contains(event.target)
      && !event.target.closest(".assistant-answer-bubble")) hideAssistantAnswerActions();
}, true);
window.addEventListener("resize", () => {
  applyAssistantDockWidth(state.assistantDockWidth);
  positionSelectionActions();
  evaluateDirectoryMode();
});
document.addEventListener("visibilitychange", () => document.hidden && savePosition());
window.addEventListener("pagehide", () => {
  savePositionKeepalive();
  clearAssistantSession({ keepalive: true }).catch(() => {});
});

function capturePortraitAnchor() {
  const rect = elements.viewer.getBoundingClientRect();
  // A tall window can show several pages. Keep the upper reading point when
  // opening a lower workspace instead of following a different page at center.
  return captureZoomAnchor(undefined, rect.top + Math.min(100, rect.height * .1));
}
function changePortraitLayout(enabled, pinned = null) {
  const anchor = capturePortraitAnchor();
  if (enabled && state.assistantExpanded) setAssistantExpanded(false, {animate:false});
  if (pinned === null && !elements["outline-panel"].hidden) closeDirectory();
  elements.reader.classList.toggle("portrait-mode", enabled);
  if (pinned !== null) elements.reader.classList.toggle("portrait-directory-pinned", pinned);
  if (enabled && !elements["assistant-panel"].hidden) {
    elements["assistant-panel"].classList.remove("assistant-overlay-open");
    elements.reader.classList.add("assistant-dock-open");
  }
  applyAssistantDockWidth(state.assistantDockWidth);
  evaluateDirectoryMode();
  if (state.pdf) relayoutPages(anchor);
}
const portraitLayout = createPortraitLayout({reader:elements.reader, openDirectory, closeDirectory, onChange:changePortraitLayout,
  available:() => !!state.pdf && !elements.reader.hidden});
if (portraitLayout.enabled()) changePortraitLayout(true);
applyAssistantDockWidth(state.assistantDockWidth);
renderAssistantViewportMode();
// Async in-flow reservations (learning footers, panels) shift pages after a
// navigation landing; while the pin holds, re-assert the claimed destination.
new ResizeObserver(() => { if (state.pinnedLanding) scheduleViewportUpdate(); }).observe(elements.pages);
// Dock open/close, pane dragging, and window resize all change the PDF column.
// Rebuild only after its width settles, so pages rendered before and after the
// change use the same scale and their text/interaction layers stay aligned.
new ResizeObserver(() => {
  if (!state.pdf || elements.reader.hidden) return;
  if (Math.abs(elements.viewer.clientWidth - state.viewerLayoutWidth) < 1) return;
  clearTimeout(state.resizeTimer);
  state.resizeTimer = setTimeout(() => {
    if (!state.pdf || elements.reader.hidden
        || Math.abs(elements.viewer.clientWidth - state.viewerLayoutWidth) < 1) return;
    relayoutPages();
  }, 120);
}).observe(elements.viewer);
loadBooks().catch(() => announce("书库加载失败，请刷新后重试。", true));

let readingTargets = [], readingOwner = null, readingTimer, readingBusy = false;
async function refreshReadingProgress() {
  const owner = state.revision?.id; if(!owner || !state.pdf || elements.reader.hidden || readingBusy) return;
  readingBusy = true;
  try {
    if(readingOwner !== owner) { readingTargets = (await api(`/api/revisions/${owner}/section-reading`)).sections; readingOwner = owner; }
    if(owner !== state.revision?.id || elements.reader.hidden) return;
    const view = elements.viewer.getBoundingClientRect();
    for(const target of readingTargets.filter(t => !t.reading_reached_end_at)) {
      const page = elements.pages.children[target.pdf_page_index];
      if(!page?.querySelector('canvas')) continue;
      const rect = page.querySelector('canvas').getBoundingClientRect(), y = rect.top + target.y*rect.height;
      if(y < view.top || y > view.bottom) continue;
      const result = await api(`/api/revisions/${owner}/section-reading`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pdf_page_index:target.pdf_page_index,top:Math.max(0,(view.top-rect.top)/rect.height),bottom:Math.min(1,(view.bottom-rect.top)/rect.height)})});
      if(owner !== state.revision?.id) return; readingTargets = result.sections;
    }
  } catch(error) { announce(`节末阅读记录暂未保存：${error.message}。继续阅读不受影响。`, true); }
  finally { readingBusy = false; }
}
elements.viewer.addEventListener('scroll', () => { clearTimeout(readingTimer); readingTimer = setTimeout(refreshReadingProgress,400); }, {passive:true});

// ---------------------------------------------------------------------------
// 自定义快捷键运行时：把设置里录制的组合键映射到批注 / 问 AI 动作。
// 仅在阅读视图生效；输入框、对话框内、录制过程中不拦截。
document.addEventListener("keydown", (event) => {
  if (shortcutRuntime.recording || event.defaultPrevented) return;
  const map = shortcutRuntime.map;
  if (!map || !map.size) return;
  const target = event.target;
  if (target instanceof HTMLElement) {
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable) return;
  }
  if (document.querySelector("dialog[open]")) return;
  const combo = comboFromEvent(event);
  if (!combo) return;
  let action = null;
  for (const [boundAction, boundCombo] of map) {
    if (boundCombo.toUpperCase() === combo.toUpperCase()) { action = boundAction; break; }
  }
  if (!action) return;
  const reading = Boolean(state.pdf && state.readerSessionId);
  if (!reading) return;
  const needsSelection = action === "highlight" || action === "underline" || action === "note" || action === "ask_ai";
  if (needsSelection && !state.selection?.resolved.length) {
    event.preventDefault();
    announce("请先用鼠标选中一段文字。", true);
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  switch (action) {
    case "highlight": saveAnnotation(null, "HIGHLIGHT"); break;
    case "underline": saveAnnotation(null, "UNDERLINE"); break;
    case "note": elements["add-note"].click(); break;
    case "ask_ai": stageAssistantSelection(); break;
    case "textbox": elements["annotation-textbox"].click(); break;
    case "region": elements["annotation-region"].click(); break;
    case "sticky": elements["annotation-sticky"].click(); break;
    case "toggle_annotations": toggleAnnotationsPanel(); break;
    case "toggle_notes": setNotesPanelOpen(elements["notes-panel"].hidden); break;
    default: return;
  }
}, true);

// Ctrl+Z：撤销上一步批注（固定快捷键，不占用设置里的自定义快捷键）。
document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented) return;
  if (!(event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey)) return;
  if ((event.key || "").toLowerCase() !== "z") return;
  const target = event.target;
  if (target instanceof HTMLElement) {
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable) return;
  }
  if (document.querySelector("dialog[open]")) return;
  if (!state.revision?.id) return;
  if (!annotationUndoStack.some((entry) => entry.revisionId === state.revision.id)) {
    event.preventDefault();
    announce("没有可撤销的批注。", true);
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  undoLastAnnotation();
}, true);

// Ctrl+S：立即把当前教材的全部批注写回 PDF（固定快捷键）。
document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented) return;
  if (!(event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey)) return;
  if ((event.key || "").toLowerCase() !== "s") return;
  const target = event.target;
  if (target instanceof HTMLElement) {
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable) return;
  }
  if (document.querySelector("dialog[open]")) return;
  if (!state.revision?.id) return;
  event.preventDefault();
  event.stopPropagation();
  announce("正在把批注写回 PDF…");
  syncAllAnnotations({ silent: false }).catch((error) => {
    announce(error.message || "批注写回 PDF 失败，请稍后重试。", true);
  });
}, true);
