// AI 对话历史：浏览、回看与删除本地归档的临时解释会话。
// 会话本体仍由 AssistantService 在内存中驱动；这里只读取落库的只读副本，
// 让用户在关闭 Reader 后仍能查看旧问答，避免对同一问题重复提问。

export function createAssistantHistoryUI({ api, revisionId, announce, launchToken, onExport }) {
  let dialog = null;
  let body = null;

  function ensureDialog() {
    if (dialog) return;
    dialog = document.createElement("dialog");
    dialog.className = "assistant-history-dialog";
    dialog.setAttribute("aria-labelledby", "assistant-history-title");
    const heading = document.createElement("div");
    heading.className = "assistant-history-heading";
    const title = document.createElement("strong");
    title.id = "assistant-history-title";
    title.textContent = "AI 对话记录";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "assistant-history-close";
    close.setAttribute("aria-label", "关闭对话记录");
    close.textContent = "×";
    close.addEventListener("click", () => dialog.close());
    heading.append(title, close);
    body = document.createElement("div");
    body.className = "assistant-history-body";
    dialog.append(heading, body);
    document.body.append(dialog);
    dialog.addEventListener("cancel", (event) => event.preventDefault());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }

  function open() {
    ensureDialog();
    dialog.showModal();
    loadList();
  }

  async function loadList() {
    body.replaceChildren(loadingNote("正在加载对话记录 ..."));
    try {
      const payload = await api(`/api/revisions/${revisionId()}/assistant/history`);
      renderList(payload.history || []);
    } catch (error) {
      body.replaceChildren(errorNote(error.message || "对话记录加载失败。"));
    }
  }

  function renderList(items) {
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "assistant-history-empty";
      empty.textContent = "还没有本地保存的 AI 对话。每次向 AI 提问完成后会自动归档到这里。";
      body.replaceChildren(empty);
      return;
    }
    const list = document.createElement("div");
    list.className = "assistant-history-list";
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "assistant-history-item";
      const openButton = document.createElement("button");
      openButton.type = "button";
      openButton.className = "assistant-history-item-open";
      const label = document.createElement("span");
      label.className = "assistant-history-item-label";
      label.textContent = item.label || "未命名解释";
      const meta = document.createElement("span");
      meta.className = "assistant-history-item-meta";
      meta.textContent = metaLine(item);
      openButton.append(label, meta);
      openButton.addEventListener("click", () => loadDetail(item.root_id));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "assistant-history-item-delete";
      remove.setAttribute("aria-label", `删除对话记录：${item.label || ""}`);
      remove.title = "删除这条记录";
      remove.textContent = "删除";
      remove.addEventListener("click", () => removeItem(item, loadList));
      row.append(openButton, remove);
      list.append(row);
    }
    body.replaceChildren(list);
  }

  async function loadDetail(rootId) {
    body.replaceChildren(loadingNote("正在加载对话内容 ..."));
    let conversation;
    try {
      const payload = await api(`/api/revisions/${revisionId()}/assistant/history/${rootId}`);
      conversation = payload.conversation;
    } catch (error) {
      body.replaceChildren(errorNote(error.message || "对话内容加载失败。"));
      return;
    }
    renderDetail(conversation || {});
  }

  function renderDetail(conversation) {
    const fragment = document.createDocumentFragment();
    const toolbar = document.createElement("div");
    toolbar.className = "assistant-history-toolbar";
    const back = document.createElement("button");
    back.type = "button";
    back.className = "assistant-history-back";
    back.textContent = "← 返回列表";
    back.addEventListener("click", loadList);
    const exportButton = document.createElement("button");
    exportButton.type = "button";
    exportButton.className = "assistant-history-export";
    exportButton.textContent = "导出 MD";
    exportButton.addEventListener("click", () => {
      if (onExport) onExport(conversation.root_id);
    });
    toolbar.append(back, exportButton);

    const title = document.createElement("h3");
    title.className = "assistant-history-detail-title";
    title.textContent = conversation.label || "未命名解释";

    const scopeLine = document.createElement("p");
    scopeLine.className = "assistant-history-detail-scope";
    scopeLine.textContent = scopeLineText(conversation);

    const content = document.createElement("div");
    content.className = "assistant-history-detail";
    for (const turn of conversation.turns || []) {
      content.append(turnBlock("提问", turn.question), turnBlock("回答", turn.answer));
    }
    for (const node of conversation.nodes || []) {
      if (!(node.turns || []).length) continue;
      const heading = document.createElement("h4");
      heading.className = "assistant-history-node-title";
      heading.textContent = `第 ${node.depth} 层 · ${node.label || ""}`;
      content.append(heading);
      for (const turn of node.turns || []) {
        content.append(turnBlock("提问", turn.question), turnBlock("回答", turn.answer));
      }
    }
    if (!content.childNodes.length) {
      const empty = document.createElement("p");
      empty.className = "assistant-history-empty";
      empty.textContent = "这条会话没有已完成的问答。";
      content.append(empty);
    }
    fragment.append(toolbar, title, scopeLine, content);
    body.replaceChildren(fragment);
  }

  async function removeItem(item, refresh) {
    if (!window.confirm(`删除这条 AI 对话记录？（${item.label || "未命名解释"}）`)) return;
    try {
      await api(`/api/revisions/${revisionId()}/assistant/history/${item.root_id}`, { method: "DELETE" });
      announce?.("对话记录已删除。");
      refresh?.();
    } catch (error) {
      announce?.(error.message || "对话记录删除失败。", true);
    }
  }

  function turnBlock(role, text) {
    const block = document.createElement("div");
    block.className = `assistant-history-turn assistant-history-turn-${role === "提问" ? "q" : "a"}`;
    const roleTag = document.createElement("span");
    roleTag.className = "assistant-history-turn-role";
    roleTag.textContent = role;
    const content = document.createElement("div");
    content.className = "assistant-history-turn-content";
    content.textContent = text || "";
    block.append(roleTag, content);
    return block;
  }

  function metaLine(item) {
    const parts = [];
    if (typeof item.pdf_page_index === "number") parts.push(`第 ${item.pdf_page_index + 1} 页`);
    parts.push(`${item.turn_count || 0} 轮问答`);
    if (item.updated_at) parts.push(String(item.updated_at).replace("T", " "));
    return parts.join(" · ");
  }

  function scopeLineText(conversation) {
    const scope = conversation.scope || {};
    const parts = [];
    if (typeof scope.pdf_page_index === "number") parts.push(`PDF 第 ${scope.pdf_page_index + 1} 页`);
    if (scope.chapter_title) parts.push(scope.chapter_title);
    if (scope.section_title) parts.push(scope.section_title);
    const models = [conversation.provider, conversation.model].filter(Boolean).join(" / ");
    if (models) parts.push(models);
    return parts.join(" · ") || "来源信息缺失";
  }

  function loadingNote(text) {
    const note = document.createElement("p");
    note.className = "assistant-history-empty";
    note.textContent = text;
    return note;
  }

  function errorNote(text) {
    const note = document.createElement("p");
    note.className = "assistant-history-empty assistant-history-error";
    note.textContent = text;
    return note;
  }

  return { open };
}
