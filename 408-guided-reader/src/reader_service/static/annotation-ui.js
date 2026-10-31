// Five-kind annotation layer over the original PDF page.
// Everything is positioned in normalized page coordinates, so the layer follows
// the existing PDF.js render, zoom and relayout without its own geometry cache.
import {normalizedRegion} from "/visual-selection.js";
import {renderAssistantAnswer} from "/assistant-render.js";
import {
  ANNOTATION_KIND_LABELS, annotationBadgeLabel, annotationBodyText, annotationKind,
  annotationPageIndex, annotationJumpTarget, isExcerptNote,
} from "/annotation-display.js";

// 展示判定统一放在 annotation-display.js（纯逻辑、可单测），这里保留再导出，
// 现有 import 位置不必改动。
export { ANNOTATION_KIND_LABELS, annotationBadgeLabel, annotationBodyText, annotationKind, isExcerptNote };
export { annotationPageIndex, annotationJumpTarget };

export const ANNOTATION_COLORS = Object.freeze([
  {name: "YELLOW", label: "黄色"},
  {name: "GREEN", label: "绿色"},
  {name: "BLUE", label: "蓝色"},
  {name: "RED", label: "红色"},
  {name: "PURPLE", label: "紫色"},
]);

export const ANNOTATION_COLOR_LABELS = Object.freeze(Object.fromEntries(
  ANNOTATION_COLORS.map((color) => [color.name, color.label]),
));

// 固定 hex 预设色（radio value 直接用 #RRGGBB），给它们正式的名字。
const HEX_COLOR_LABELS = Object.freeze({"#BFBFBF": "灰色"});

const RECT_KINDS = new Set(["TEXTBOX", "REGION", "NOTE"]);

export function annotationColor(annotation) {
  const value = String(annotation?.color || annotation?.highlight_style || "YELLOW").toUpperCase();
  if (isHexColor(value)) return value;
  return ANNOTATION_COLOR_LABELS[value] ? value : "YELLOW";
}

// 颜色的展示名：枚举名查表；固定 hex 预设有正式名字；其余 hex 显示色值本身。
export function colorLabel(value) {
  const tone = String(value || "").toUpperCase();
  if (!isHexColor(tone)) return ANNOTATION_COLOR_LABELS[tone] || "颜色";
  return HEX_COLOR_LABELS[tone] || tone;
}

// 自定义颜色：#RRGGBB 形式，存储与渲染都用它；内置色仍是 YELLOW 等枚举名。
export function isHexColor(value) {
  return /^#[0-9a-f]{6}$/i.test(String(value || ""));
}

export function hexToRgba(hex, alpha) {
  const raw = String(hex).replace("#", "");
  const red = parseInt(raw.slice(0, 2), 16);
  const green = parseInt(raw.slice(2, 4), 16);
  const blue = parseInt(raw.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

export function isRectAnnotation(annotation) {
  return RECT_KINDS.has(annotationKind(annotation));
}

function quadBounds(quad) {
  const xs = quad.map(([x]) => x);
  const ys = quad.map(([, y]) => y);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

// OCR 行框是按整页归一化坐标存的，但部分页的 OCR 框会比扫描字形偏上/偏矮
// （字底露出高亮外）。这里读取该页 text-overlay 的行框，用相邻行之间的空隙
// 把批注 quad 纵向校正到真实字形范围。只影响显示，不改动存储数据。
function pageLineBands(page) {
  if (!page) return [];
  const bands = [];
  for (const line of page.querySelectorAll(":scope > .text-overlay > .ocr-line")) {
    const style = line.style;
    const top = parseFloat(style.top);
    const height = parseFloat(style.height);
    if (!Number.isFinite(top) || !Number.isFinite(height)) continue;
    bands.push({ y0: top / 100, y1: (top + height) / 100 });
  }
  bands.sort((a, b) => a.y0 - b.y0);
  return bands;
}

function expandedQuad(quad, bands, { padTop = true } = {}) {
  const [x0, y0, x1, y1] = quadBounds(quad);
  const height = y1 - y0;
  if (!bands.length || height <= 0) return quad;
  // 找到与该 quad 垂直重叠最大的 OCR 行
  let best = -1;
  let bestOverlap = -1;
  for (let i = 0; i < bands.length; i += 1) {
    const overlap = Math.min(y1, bands[i].y1) - Math.max(y0, bands[i].y0);
    if (overlap > bestOverlap) { bestOverlap = overlap; best = i; }
  }
  if (best < 0) return quad;
  const band = bands[best];
  const prev = bands[best - 1];
  const next = bands[best + 1];
  const gapAbove = prev && y0 - prev.y1 <= 3 * height ? y0 - prev.y1 : null;
  const gapBelow = next && next.y0 - y1 <= 3 * height ? next.y0 - y1 : null;
  const fallbackGap = 0.5 * height;
  const pad = 0.0015;
  // 字形顶约在行框顶下方 25% 行间隙处，字形底约在行框底下方 25% 行间隙处。
  const top = y0 + 0.25 * (gapAbove ?? fallbackGap) - (padTop ? pad : 0);
  const bottom = y1 + 0.25 * (gapBelow ?? fallbackGap) + pad;
  return [[x0, top], [x1, top], [x1, bottom], [x0, bottom]];
}

function expandQuadsToGlyphs(quads, page, options) {
  const source = Array.isArray(quads) ? quads : [];
  if (!source.length) return source;
  const bands = pageLineBands(page);
  if (!bands.length) return source;
  return source.map((quad) => expandedQuad(quad, bands, options));
}

function percent(value) {
  return `${(value * 100).toFixed(3)}%`;
}

function boxStyle([x0, y0, x1, y1]) {
  return `left:${percent(x0)};top:${percent(y0)};width:${percent(x1 - x0)};height:${percent(y1 - y0)}`;
}

export function annotationAnchorRect(annotation) {
  if (Array.isArray(annotation?.rect)) return [...annotation.rect];
  const quads = annotation?.quads || [];
  if (!quads.length) return [0.5, 0.5, 0.5, 0.5];
  return quads.map(quadBounds).reduce((acc, value) => [
    Math.min(acc[0], value[0]), Math.min(acc[1], value[1]),
    Math.max(acc[2], value[2]), Math.max(acc[3], value[3]),
  ], [1, 1, 0, 0]);
}

export function createAnnotationUI({pages, viewer, api, revisionId, announce, onChange}) {
  let mode = null;
  let color = "YELLOW";
  let drag = null;
  let editor = null;
  let card = null;
  let hint = null;
  const draftByPage = new Map();

  function pageOf(index) {
    return pages.children[index] || null;
  }

  function layerOf(index) {
    const page = pageOf(index);
    if (!page) return null;
    let layer = page.querySelector(":scope > .annotation-layer");
    if (!layer) {
      layer = document.createElement("div");
      layer.className = "annotation-layer";
      layer.setAttribute("aria-hidden", "false");
      page.append(layer);
    }
    return layer;
  }

  function noteElements(annotation, page) {
    const nodes = [];
    const kind = annotationKind(annotation);
    const rawColor = annotationColor(annotation);
    // 自定义颜色（#RRGGBB）用内联样式渲染；内置色继续走 annotation-style-* 类。
    const hex = isHexColor(rawColor) ? rawColor : null;
    const tone = hex ? "" : rawColor.toLowerCase();
    if (kind === "AI_NOTE") {
      for (const quad of expandQuadsToGlyphs(annotation.quads, page)) {
        const box = document.createElement("span");
        box.className = `annotation-quad annotation-style-${tone} annotation-saved`;
        box.dataset.annotationId = annotation.id;
        box.style.cssText = boxStyle(quadBounds(quad));
        nodes.push(box);
      }
      const [x0, y0] = annotationAnchorRect(annotation);
      const marker = document.createElement("button");
      marker.type = "button";
      marker.className = "annotation-note-marker";
      marker.dataset.annotationId = annotation.id;
      marker.style.cssText = `left:${percent(x0)};top:${percent(y0)}`;
      marker.textContent = "AI";
      marker.title = "查看保存的 AI 解答";
      marker.setAttribute("aria-label", "查看保存的 AI 解答");
      marker.addEventListener("pointerdown", (event) => event.stopPropagation());
      marker.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        openNote(annotation, marker);
      });
      nodes.push(marker);
      return nodes;
    }
    if (kind === "HIGHLIGHT") {
      for (const quad of expandQuadsToGlyphs(annotation.quads, page)) {
        const box = document.createElement("span");
        box.className = `annotation-quad${hex ? "" : ` annotation-style-${tone}`}`;
        box.dataset.annotationId = annotation.id;
        box.style.cssText = boxStyle(quadBounds(quad))
          + (hex ? `;background:${hexToRgba(hex, 0.3)};border-bottom-color:${hexToRgba(hex, 0.45)}` : "");
        nodes.push(box);
      }
      return nodes;
    }
    if (kind === "UNDERLINE") {
      for (const quad of expandQuadsToGlyphs(annotation.quads, page, { padTop: false })) {
        const bounds = quadBounds(quad);
        const line = document.createElement("span");
        line.className = `annotation-underline${hex ? "" : ` annotation-style-${tone}`}`;
        line.dataset.annotationId = annotation.id;
        line.style.cssText = `left:${percent(bounds[0])};top:${percent(bounds[3])};width:${percent(bounds[2] - bounds[0])}`
          + (hex ? `;background:${hex}` : "");
        nodes.push(line);
      }
      return nodes;
    }
    if (kind === "REGION" && Array.isArray(annotation.rect)) {
      const box = document.createElement("div");
      box.className = `annotation-region${hex ? "" : ` annotation-style-${tone}`}`;
      box.dataset.annotationId = annotation.id;
      box.style.cssText = boxStyle(annotation.rect)
        + (hex ? `;background:${hexToRgba(hex, 0.2)};border-color:${hexToRgba(hex, 0.45)}` : "");
      if (annotation.body) box.title = annotation.body;
      nodes.push(box);
      return nodes;
    }
    if ((kind === "TEXTBOX" || kind === "NOTE") && Array.isArray(annotation.rect)) {
      const sticky = kind === "NOTE";
      const box = document.createElement("div");
      const toneClass = hex ? "" : ` annotation-style-${tone}`;
      box.className = sticky
        ? `annotation-box annotation-sticky${toneClass}`
        : `annotation-box${toneClass}`;
      box.dataset.annotationId = annotation.id;
      box.style.cssText = boxStyle(annotation.rect)
        + (hex ? `;background:${hexToRgba(hex, sticky ? 0.94 : 0.88)};border-color:${hexToRgba(hex, 0.55)}` : "");
      const text = document.createElement("span");
      text.className = "annotation-box-text";
      text.textContent = annotation.body || (sticky ? "双击输入便签内容" : "双击输入文字");
      box.append(text);
      box.title = "拖动移动；拖动边角调整大小；双击编辑文字";
      box.addEventListener("pointerdown", (event) => beginMove(event, annotation, box));
      box.addEventListener("dblclick", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (event.target.closest(".annotation-resize-handle")) return;
        openEditor(annotation, box);
      });
      appendResizeHandles(annotation, box);
      nodes.push(box);
      return nodes;
    }
    return nodes;
  }

  function render(index, values = []) {
    const layer = layerOf(index);
    if (!layer) return;
    layer.dataset.pageIndex = String(index);
    const page = layer.parentElement;
    const nodes = [];
    for (const annotation of values) nodes.push(...noteElements(annotation, page));
    layer.replaceChildren(...nodes);
  }

  function refresh(valuesByPage) {
    for (let index = 0; index < pages.children.length; index += 1) {
      const page = pageOf(index);
      if (!page?.querySelector("canvas")) continue;
      render(index, valuesByPage(index) || []);
    }
  }

  function clear(index) {
    pageOf(index)?.querySelector(":scope > .annotation-layer")?.replaceChildren();
  }

  function flash(annotationId) {
    for (const node of document.querySelectorAll(`[data-annotation-id="${annotationId}"]`)) {
      node.classList.remove("annotation-flash");
      void node.offsetWidth;
      node.classList.add("annotation-flash");
      setTimeout(() => node.classList.remove("annotation-flash"), 1600);
    }
  }

  // -- rectangle tools -------------------------------------------------------

  function ensureHint() {
    if (hint) return hint;
    hint = document.createElement("div");
    hint.className = "annotation-hint";
    hint.setAttribute("role", "status");
    hint.hidden = true;
    const text = document.createElement("span");
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "取消（Esc）";
    cancel.addEventListener("click", () => setMode(null));
    hint.append(text, cancel);
    document.body.append(hint);
    return hint;
  }

  function syncModeUi() {
    document.getElementById("annotation-textbox")?.setAttribute("aria-pressed", String(mode === "TEXTBOX"));
    document.getElementById("annotation-region")?.setAttribute("aria-pressed", String(mode === "REGION"));
    document.getElementById("annotation-sticky")?.setAttribute("aria-pressed", String(mode === "NOTE"));
    viewer.classList.toggle("annotation-tool-active", Boolean(mode));
    const bar = ensureHint();
    bar.hidden = !mode;
    if (mode) {
      bar.firstChild.textContent = mode === "TEXTBOX"
        ? "拖拽画一个文本框；画完可直接输入文字"
        : mode === "NOTE" ? "拖拽放一张便签；松手后双击输入内容" : "拖拽画一个区域高亮";
    }
    if (!mode) {
      cancelDrag();
      for (const page of pages.children) page.querySelector(".annotation-draft")?.remove();
      draftByPage.clear();
    }
  }

  function setMode(next) {
    mode = next;
    syncModeUi();
    if (mode) viewer.focus({preventScroll: true});
  }

  function setColor(next) {
    const value = String(next || "").toUpperCase();
    if (ANNOTATION_COLOR_LABELS[value] || isHexColor(value)) color = value;
    else color = "YELLOW";
    for (const input of document.querySelectorAll('input[name="annotation-tool-color"]')) {
      input.checked = !isHexColor(color) && input.value === color;
    }
  }

  function cancelDrag() {
    const pointerId = drag?.pointerId;
    drag = null;
    if (pointerId !== undefined && viewer.hasPointerCapture?.(pointerId)) viewer.releasePointerCapture(pointerId);
    viewer.style.cursor = "";
  }

  function pointIn(page, event) {
    const bounds = page.getBoundingClientRect();
    return {
      x: (event.clientX - bounds.left) / bounds.width,
      y: (event.clientY - bounds.top) / bounds.height,
    };
  }

  function drawDraft(index, rect) {
    const page = pageOf(index);
    if (!page) return;
    let draft = page.querySelector(".annotation-draft");
    if (!draft) {
      draft = document.createElement("div");
      draft.className = "annotation-draft";
      page.append(draft);
    }
    draft.style.cssText = boxStyle(rect);
  }

  function beginDraw(event) {
    if (!mode || event.button !== 0) return;
    const page = event.target.closest(".page");
    if (!page || page.querySelector("canvas") === null) return;
    const start = pointIn(page, event);
    if (start.x < 0 || start.x > 1 || start.y < 0 || start.y > 1) return;
    event.preventDefault();
    event.stopPropagation();
    drag = {
      pointerId: event.pointerId, pageIndex: Number(page.dataset.index), page,
      start, rect: normalizedRegion(start, start), action: "draw",
    };
    viewer.style.cursor = "crosshair";
    viewer.setPointerCapture(event.pointerId);
    drawDraft(drag.pageIndex, drag.rect);
  }

  async function finishDraw() {
    const current = drag;
    cancelDrag();
    if (!current) return;
    const page = pageOf(current.pageIndex);
    page?.querySelector(".annotation-draft")?.remove();
    const [x0, y0, x1, y1] = current.rect;
    const pageWidth = page?.clientWidth || 0;
    const pageHeight = page?.clientHeight || 0;
    if ((x1 - x0) * pageWidth < 10 || (y1 - y0) * pageHeight < 10) return;
    const kind = mode;
    setMode(null);
    await createRect(kind, current.pageIndex, current.rect);
  }

  async function createRect(kind, pageIndex, rect) {
    const revision = revisionId();
    if (!revision) return null;
    try {
      const payload = await api(`/api/revisions/${revision}/annotations`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          kind, page_index: pageIndex, pdf_page_index: pageIndex,
          rect, color: color, highlight_style: color,
        }),
      });
      const annotation = payload?.annotation || payload;
      if (!annotation?.id) throw new Error("批注未保存。");
      announce(kind === "TEXTBOX" ? "文本框已创建；双击可编辑文字。"
        : kind === "NOTE" ? "便签已创建；双击可编辑内容。" : "区域高亮已保存。");
      onChange?.(annotation, {created: true});
      return annotation;
    } catch (error) {
      announce(error.message || "批注未保存，请稍后重试。", true);
      return null;
    }
  }

  // -- textbox drag / resize / edit -------------------------------------------

  let lastClick = {id: null, time: 0};

  // 八个方向的缩放手柄：四边 + 四角，全部放在框内（框 overflow: hidden 不裁剪）。
  const RESIZE_HANDLES = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

  function appendResizeHandles(annotation, box) {
    for (const direction of RESIZE_HANDLES) {
      const handle = document.createElement("span");
      handle.className = `annotation-resize-handle annotation-resize-${direction}`;
      handle.dataset.annotationId = annotation.id;
      handle.addEventListener("pointerdown", (event) => beginResize(event, direction, annotation, box));
      box.append(handle);
    }
  }

  function beginResize(event, direction, annotation, box) {
    if (event.button !== 0 || mode) return;
    const page = box.closest(".page");
    if (!page) return;
    event.preventDefault();
    event.stopPropagation();
    drag = {
      pointerId: event.pointerId, pageIndex: Number(page.dataset.index), page,
      action: "resize", annotation, box, direction,
      start: pointIn(page, event), orig: [...annotation.rect],
      rect: [...annotation.rect], moved: false,
    };
    viewer.setPointerCapture(event.pointerId);
  }

  function beginMove(event, annotation, box) {
    if (event.button !== 0 || mode) return;
    const page = box.closest(".page");
    if (!page) return;
    event.preventDefault();
    event.stopPropagation();
    // Pointer capture redirects the compatibility click to the viewer, so the
    // double-click that opens the editor is detected here instead of on dblclick.
    const now = performance.now();
    if (lastClick.id === annotation.id && now - lastClick.time < 350) {
      lastClick = {id: null, time: 0};
      openEditor(annotation, box);
      return;
    }
    lastClick = {id: annotation.id, time: now};
    const start = pointIn(page, event);
    drag = {
      pointerId: event.pointerId, pageIndex: Number(page.dataset.index), page,
      start, action: "move", annotation, box, rect: [...annotation.rect], moved: false,
    };
    viewer.setPointerCapture(event.pointerId);
  }

  async function saveRect(annotation, rect) {
    const revision = revisionId();
    if (!revision) return;
    try {
      await api(`/api/revisions/${revision}/annotations/${annotation.id}`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({rect}),
      });
      annotation.rect = rect;
      onChange?.({...annotation, rect: [...rect]});
    } catch (error) {
      announce(error.message || "批注位置未保存。", true);
      onChange?.(annotation);
    }
  }

  async function finishMove() {
    const current = drag;
    cancelDrag();
    if (!current || current.action !== "move") return;
    if (!current.moved) return;
    await saveRect(current.annotation, current.rect);
  }

  async function finishResize() {
    const current = drag;
    cancelDrag();
    if (!current || current.action !== "resize") return;
    if (!current.moved) return;
    await saveRect(current.annotation, current.rect);
  }

  function closeEditor() {
    editor?.element.remove();
    editor = null;
  }

  function openEditor(annotation, box) {
    closeEditor();
    const rect = box.getBoundingClientRect();
    const element = document.createElement("form");
    element.className = "annotation-editor";
    element.style.cssText = `left:${Math.max(8, rect.left)}px;top:${Math.min(window.innerHeight - 172, rect.top + 8)}px;width:${Math.max(220, Math.min(360, rect.width + 60))}px`;
    const area = document.createElement("textarea");
    area.value = annotation.body || "";
    area.maxLength = 1000;
    area.rows = 4;
    const sticky = annotationKind(annotation) === "NOTE";
    area.placeholder = sticky ? "输入便签内容……" : "输入这段框选的文字……";
    area.setAttribute("aria-label", sticky ? "便签内容" : "文本框内容");
    const save = document.createElement("button");
    save.type = "submit";
    save.textContent = "保存";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "取消";
    const row = document.createElement("div");
    row.className = "annotation-editor-actions";
    row.append(save, cancel);
    element.append(area, row);
    document.body.append(element);
    editor = {annotation, element};
    area.focus({preventScroll: true});
    area.select();
    cancel.addEventListener("click", closeEditor);
    element.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.stopPropagation(); closeEditor(); }
    });
    element.addEventListener("submit", async (event) => {
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
        const updated = payload?.annotation || payload;
        annotation.body = updated?.body ?? (area.value.trim() || null);
        closeEditor();
        onChange?.({...annotation, body: annotation.body});
        announce(`${ANNOTATION_KIND_LABELS[annotationKind(annotation)] || "批注"}已更新。`);
      } catch (error) {
        announce(error.message || "批注未保存。", true);
        save.disabled = false;
      }
    });
  }

  // -- AI answer card --------------------------------------------------------

  function closeNote() {
    card?.remove();
    card = null;
  }

  function openNote(annotation, anchor) {
    closeNote();
    const element = document.createElement("section");
    element.className = "annotation-note-card";
    element.setAttribute("role", "dialog");
    element.setAttribute("aria-label", "保存的 AI 解答");
    const heading = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = "保存的 AI 解答";
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "×";
    close.setAttribute("aria-label", "关闭 AI 解答");
    close.addEventListener("click", closeNote);
    heading.append(title, close);
    const question = annotation.payload?.question
      || annotation.provenance?.answer_question || "";
    if (question) {
      const line = document.createElement("p");
      line.className = "annotation-note-question";
      line.textContent = question;
      element.append(heading, line);
    } else {
      element.append(heading);
    }
    const answer = document.createElement("div");
    answer.className = "annotation-note-answer assistant-answer-bubble";
    renderAssistantAnswer(answer, annotation.payload?.answer || annotation.body || "");
    element.append(answer);
    const meta = document.createElement("p");
    meta.className = "annotation-note-meta";
    const annotationTone = annotationColor(annotation);

    meta.textContent = `PDF 第 ${annotationPageIndex(annotation) + 1} 页 · ${colorLabel(annotationTone)}`;
    element.append(meta);
    document.body.append(element);
    const bounds = anchor.getBoundingClientRect();
    const width = Math.min(380, window.innerWidth - 24);
    element.style.width = `${width}px`;
    const left = Math.min(window.innerWidth - width - 12, Math.max(12, bounds.left - 12));
    const height = element.offsetHeight || 240;
    const below = bounds.bottom + 10;
    const top = below + height < window.innerHeight - 12 ? below : Math.max(12, bounds.top - height - 10);
    element.style.left = `${left}px`;
    element.style.top = `${top}px`;
    card = element;
    element.querySelector("button")?.focus({preventScroll: true});
  }

  // -- global listeners ------------------------------------------------------

  viewer.addEventListener("pointerdown", (event) => {
    if (mode) { beginDraw(event); return; }
  }, true);

  viewer.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (!drag.page?.isConnected) { cancelDrag(); return; }
    event.preventDefault();
    event.stopPropagation();
    const point = pointIn(drag.page, event);
    if (drag.action === "draw") {
      drag.rect = normalizedRegion(drag.start, point);
      drawDraft(drag.pageIndex, drag.rect);
      return;
    }
    if (drag.action === "resize") {
      const page = drag.page;
      const minW = 10 / Math.max(page.clientWidth, 1);
      const minH = 10 / Math.max(page.clientHeight, 1);
      const [x0, y0, x1, y1] = drag.orig;
      const dx = point.x - drag.start.x;
      const dy = point.y - drag.start.y;
      let left = x0, top = y0, right = x1, bottom = y1;
      const horizontal = drag.direction.includes("e") ? 1 : drag.direction.includes("w") ? -1 : 0;
      const vertical = drag.direction.includes("s") ? 1 : drag.direction.includes("n") ? -1 : 0;
      if (horizontal < 0) left = Math.max(0, Math.min(x0 + dx, x1 - minW));
      if (horizontal > 0) right = Math.min(1, Math.max(x1 + dx, x0 + minW));
      if (vertical < 0) top = Math.max(0, Math.min(y0 + dy, y1 - minH));
      if (vertical > 0) bottom = Math.min(1, Math.max(y1 + dy, y0 + minH));
      drag.rect = [left, top, right, bottom];
      if (Math.abs(left - x0) > 0.0005 || Math.abs(right - x1) > 0.0005
        || Math.abs(top - y0) > 0.0005 || Math.abs(bottom - y1) > 0.0005) drag.moved = true;
      if (drag.box?.isConnected) drag.box.style.cssText = boxStyle(drag.rect);
      return;
    }
    const [x0, y0, x1, y1] = drag.annotation.rect;
    const width = x1 - x0, height = y1 - y0;
    const dx = Math.max(-x0, Math.min(1 - x1, point.x - drag.start.x));
    const dy = Math.max(-y0, Math.min(1 - y1, point.y - drag.start.y));
    if (Math.abs(dx) > 0.001 || Math.abs(dy) > 0.001) drag.moved = true;
    drag.rect = [x0 + dx, y0 + dy, x0 + dx + width, y0 + dy + height];
    const box = drag.page.querySelector(`.annotation-box[data-annotation-id="${drag.annotation.id}"]`);
    if (box) box.style.cssText = boxStyle(drag.rect);
  }, true);

  viewer.addEventListener("pointerup", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    if (drag.action === "draw") finishDraw();
    else if (drag.action === "resize") finishResize();
    else finishMove();
  }, true);

  viewer.addEventListener("pointercancel", () => {
    if (!drag) return;
    const pageIndex = drag.pageIndex;
    cancelDrag();
    pageOf(pageIndex)?.querySelector(".annotation-draft")?.remove();
    if (mode) syncModeUi();
  }, true);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (editor || card) { closeEditor(); closeNote(); return; }
    if (mode) { event.preventDefault(); event.stopPropagation(); setMode(null); }
  }, true);

  document.addEventListener("pointerdown", (event) => {
    if (!card) return;
    if (!card.contains(event.target)) closeNote();
  }, true);

  for (const input of document.querySelectorAll('input[name="annotation-tool-color"]')) {
    input.addEventListener("change", () => setColor(input.value));
  }

  return {
    render, refresh, clear, flash, setMode, setColor, closeNote, closeEditor,
    cancel: () => { setMode(null); closeEditor(); closeNote(); },
    get mode() { return mode; },
    get color() { return color; },
    activeColor: () => color,
  };
}
