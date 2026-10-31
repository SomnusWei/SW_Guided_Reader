// Book-wide annotation list. Reads the full list from the service, groups it by
// PDF page and keeps edits/deletes in sync with the page overlay through callbacks.
import {
  annotationBadgeLabel, annotationBodyText, annotationKind, isExcerptNote,
} from "/annotation-display.js";
import {
  ANNOTATION_COLOR_LABELS, annotationColor, annotationJumpTarget, annotationPageIndex, colorLabel, isHexColor,
} from "/annotation-ui.js";

export function createAnnotationPanel({
  api, revisionId, goToPage, announce, annotationUI,
  onChanged = () => {}, onDeleted = () => {},
  exportMarkdown = null, syncAll = null, syncVisible = () => false, onRender = () => {},
}) {
  const panel = document.getElementById("annotations-panel");
  const list = document.getElementById("annotations-list");
  const empty = document.getElementById("annotations-empty");
  const status = document.getElementById("annotations-status");
  const exportButton = document.getElementById("annotations-export");
  const syncButton = document.getElementById("annotations-sync");
  let values = [];
  let loadedFor = null;
  let loading = false;

  exportButton?.addEventListener("click", () => exportMarkdown?.());
  syncButton?.addEventListener("click", async () => {
    if (!syncAll) return;
    syncButton.disabled = true;
    try {
      await syncAll();
    } finally {
      syncButton.disabled = false;
    }
  });

  function sortAnnotations(items) {
    return [...items].sort((left, right) => annotationPageIndex(left) - annotationPageIndex(right)
      || String(left.created_at || "").localeCompare(String(right.created_at || ""))
      || String(left.id).localeCompare(String(right.id)));
  }

  async function load({force = false} = {}) {
    const revision = revisionId();
    if (!revision || loading) return;
    if (!force && loadedFor === revision && values.length) { render(); return; }
    loading = true;
    status.hidden = false;
    status.textContent = "正在读取批注…";
    try {
      const payload = await api(`/api/revisions/${revision}/annotations`);
      values = sortAnnotations(payload.annotations || []);
      loadedFor = revision;
      status.hidden = true;
    } catch (_error) {
      // Older services only expose the per-page list; show the current page instead
      // of an empty panel and say so plainly.
      try {
        const page = Number(document.getElementById("page-number")?.value || 1) - 1;
        const payload = await api(`/api/revisions/${revision}/annotations?page=${page}`);
        values = sortAnnotations(payload.annotations || []);
        loadedFor = revision;
        status.hidden = false;
        status.textContent = "当前服务只提供本页批注列表。";
      } catch (error) {
        values = [];
        status.hidden = false;
        status.textContent = error.message || "批注暂时无法读取。";
      }
    } finally {
      loading = false;
      render();
    }
  }

  function setValues(next) {
    values = sortAnnotations(next);
    render();
  }

  function upsert(annotation) {
    const index = values.findIndex((value) => value.id === annotation.id);
    const next = [...values];
    if (index >= 0) next[index] = {...next[index], ...annotation};
    else next.push(annotation);
    values = sortAnnotations(next);
    render();
  }

  function remove(annotationId) {
    values = values.filter((value) => value.id !== annotationId);
    render();
  }

  function actionButton(label, run, className = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.addEventListener("click", run);
    return button;
  }

  function isExcerpt(annotation) {
    return isExcerptNote(annotation);
  }

  function bodyText(annotation) {
    return annotationBodyText(annotation);
  }

  // 跳到批注所在位置并高亮闪烁。页面渲染是异步的，先多试几次 flash，
  // 确保翻页后新渲染出来的标记也能闪到。
  function jumpToAnnotation(annotation) {
    const {page, y} = annotationJumpTarget(annotation);
    goToPage(page, y);
    for (let attempt = 0; attempt < 6; attempt += 1) {
      setTimeout(() => annotationUI?.flash(annotation.id), attempt * 250);
    }
  }

  function renderItem(annotation) {
    const item = document.createElement("article");
    item.className = "annotation-item";
    item.dataset.annotationId = annotation.id;
    // 整条内容可点击跳转；键盘用户用 Enter/空格同样触发。
    item.tabIndex = 0;
    item.addEventListener("click", (event) => {
      if (event.target.closest("button, a, input, textarea, select, form")) return;
      jumpToAnnotation(annotation);
    });
    item.addEventListener("keydown", (event) => {
      if ((event.key === "Enter" || event.key === " ") && event.target === item) {
        event.preventDefault();
        jumpToAnnotation(annotation);
      }
    });
    const kind = annotationKind(annotation);
    item.dataset.kind = kind;
    const excerptItem = isExcerpt(annotation);
    if (excerptItem) item.dataset.excerpt = "true";
    const head = document.createElement("div");
    head.className = "annotation-item-head";
    const badge = document.createElement("span");
    badge.className = `annotation-badge annotation-badge-${kind.toLowerCase()}`;
    badge.textContent = annotationBadgeLabel(annotation);
    const swatch = document.createElement("span");
    const annotationTone = annotationColor(annotation);
    swatch.className = `annotation-swatch annotation-style-${isHexColor(annotationTone) ? "yellow" : annotationTone.toLowerCase()}`;
    if (isHexColor(annotationTone)) swatch.style.background = annotationTone;
    swatch.title = colorLabel(annotationTone);
    head.append(badge, swatch);
    if (annotation.source_kind === "AI_SAVED") {
      const saved = document.createElement("span");
      saved.className = "annotation-badge annotation-badge-ai";
      saved.textContent = "AI";
      head.append(saved);
    }
    const jump = actionButton(`PDF 第 ${annotationPageIndex(annotation) + 1} 页`, () => {
      jumpToAnnotation(annotation);
    }, "annotation-jump");
    head.append(jump);
    item.append(head);

    if (annotation.quote && kind !== "AI_NOTE") {
      const quote = document.createElement("q");
      quote.className = "annotation-quote";
      quote.textContent = annotation.quote;
      item.append(quote);
    }
    const body = document.createElement("p");
    body.className = "annotation-body";
    const text = bodyText(annotation);
    body.textContent = text || (kind === "TEXTBOX" ? "（空文本框）" : "（无文字）");
    item.append(body);

    const actions = document.createElement("div");
    actions.className = "annotation-item-actions";
    if (kind !== "AI_NOTE") {
      actions.append(actionButton("编辑", () => beginEdit(annotation, body)));
      if (["HIGHLIGHT", "UNDERLINE"].includes(kind)) {
        actions.append(actionButton("改颜色", () => cycleColor(annotation)));
      }
    }
    actions.append(actionButton("删除", () => deleteAnnotation(annotation), "annotation-delete"));
    item.append(actions);
    return item;
  }

  function beginEdit(annotation, target) {
    if (target.dataset.editing === "true") return;
    target.dataset.editing = "true";
    const form = document.createElement("form");
    form.className = "annotation-edit-form";
    const area = document.createElement("textarea");
    area.rows = 3;
    area.maxLength = 1000;
    area.value = annotation.body || "";
    area.setAttribute("aria-label", "批注内容");
    const row = document.createElement("div");
    row.className = "annotation-edit-actions";
    const save = document.createElement("button");
    save.type = "submit";
    save.textContent = "保存";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "取消";
    row.append(save, cancel);
    form.append(area, row);
    target.replaceWith(form);
    area.focus({preventScroll: true});
    cancel.addEventListener("click", () => {
      target.dataset.editing = "false";
      form.replaceWith(target);
    });
    form.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.stopPropagation(); cancel.click(); }
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const revision = revisionId();
      if (!revision) return;
      save.disabled = true;
      try {
        const payload = await api(`/api/revisions/${revision}/annotations/${annotation.id}`, {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({body: area.value.trim() || null}),
        });
        const updated = payload?.annotation || payload || {...annotation, body: area.value.trim() || null};
        upsert(updated);
        onChanged(updated);
        announce("批注已更新。");
      } catch (error) {
        announce(error.message || "批注未更新。", true);
        save.disabled = false;
      }
    });
  }

  async function cycleColor(annotation) {
    const revision = revisionId();
    if (!revision) return;
    const order = Object.keys(ANNOTATION_COLOR_LABELS);
    const next = order[(order.indexOf(annotationColor(annotation)) + 1) % order.length];
    try {
      const payload = await api(`/api/revisions/${revision}/annotations/${annotation.id}`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({color: next}),
      });
      const updated = payload?.annotation || payload || {...annotation, color: next};
      upsert(updated);
      onChanged(updated);
      announce(`颜色已改为${ANNOTATION_COLOR_LABELS[next]}。`);
    } catch (error) {
      announce(error.message || "颜色未更新。", true);
    }
  }

  async function deleteAnnotation(annotation) {
    const question = annotation.source_kind === "AI_SAVED"
      ? "删除这条已保存的 AI 笔记？" : "删除这条批注？";
    if (!window.confirm(question)) return;
    const revision = revisionId();
    if (!revision) return;
    try {
      await api(`/api/revisions/${revision}/annotations/${annotation.id}`, {method: "DELETE"});
      remove(annotation.id);
      onDeleted(annotation);
      announce("批注已删除。");
    } catch (error) {
      announce(error.message || "批注未删除。", true);
    }
  }

  function render() {
    if (!panel) return;
    if (syncButton) {
      const visible = syncVisible();
      syncButton.hidden = !visible;
    }
    if (!values.length) {
      list.replaceChildren();
      empty.hidden = false;
      onRender();
      return;
    }
    empty.hidden = true;
    const groups = new Map();
    for (const annotation of values) {
      const page = annotationPageIndex(annotation);
      if (!groups.has(page)) groups.set(page, []);
      groups.get(page).push(annotation);
    }
    const nodes = [];
    for (const [page, items] of [...groups.entries()].sort((left, right) => left[0] - right[0])) {
      const heading = document.createElement("p");
      heading.className = "annotation-page-heading";
      heading.textContent = `第 ${page + 1} 页 · ${items.length} 条`;
      nodes.push(heading);
      for (const item of items) nodes.push(renderItem(item));
    }
    list.replaceChildren(...nodes);
    onRender();
  }

  function open() {
    if (!panel) return;
    panel.hidden = false;
    load();
  }

  function close() {
    if (panel) panel.hidden = true;
  }

  function reset() {
    values = [];
    loadedFor = null;
    loading = false;
    list?.replaceChildren();
    if (status) { status.hidden = false; status.textContent = ""; }
    if (empty) empty.hidden = false;
    close();
  }

  return {
    open, close, reset, load, render, upsert, remove, setValues,
    get visible() { return Boolean(panel && !panel.hidden); },
    get values() { return values; },
  };
}
