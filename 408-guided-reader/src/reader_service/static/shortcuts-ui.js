// Reader 自定义快捷键：设置页录制区 + 通用组合键工具。
// 录制方式：点击「录制」→ 按下任意组合键（Esc 取消），组合串如 "Ctrl+Alt+H"。
// 绑定持久化在后端 /api/settings/shortcuts（数据目录 shortcuts.json）。

export const SHORTCUT_ACTIONS = [
  {id: "highlight", label: "高亮选中文本", hint: "对当前选中的文字做高亮"},
  {id: "underline", label: "下划线", hint: "对当前选中的文字加下划线"},
  {id: "note", label: "添加笔记", hint: "为当前选中的文字添加笔记"},
  {id: "ask_ai", label: "问 AI", hint: "把当前选中内容交给 AI 解答"},
  {id: "textbox", label: "文本框模式", hint: "在页面上拖拽添加文本框"},
  {id: "region", label: "区域高亮模式", hint: "在页面上拖拽框选区域"},
  {id: "sticky", label: "便签模式", hint: "在页面上拖拽添加便签"},
  {id: "toggle_annotations", label: "全书批注面板", hint: "打开或关闭左侧全书批注列表"},
  {id: "toggle_notes", label: "笔记抽屉", hint: "打开或关闭笔记抽屉，自动带入当前教材与页码"},
];

const MODIFIER_KEYS = new Set(["Control", "Alt", "Shift", "Meta"]);

export function comboFromEvent(event) {
  if (!event || MODIFIER_KEYS.has(event.key)) return null;
  let main;
  if (event.key.length === 1) main = event.key.toUpperCase();
  else main = event.key; // "F2"、"ArrowLeft"、"Enter" 等保持原名
  const parts = [];
  if (event.ctrlKey) parts.push("Ctrl");
  if (event.altKey) parts.push("Alt");
  if (event.shiftKey) parts.push("Shift");
  if (event.metaKey) parts.push("Win");
  parts.push(main);
  return parts.join("+");
}

export function createShortcutsSection({api, announce, runtime}) {
  const section = document.createElement("section");
  section.className = "settings-section shortcuts-section";
  const heading = document.createElement("h3");
  heading.textContent = "快捷键";
  const hint = document.createElement("p");
  hint.className = "settings-hint";
  hint.textContent = "点击「录制」后按下想要的组合键（Esc 取消，清除按钮解绑）。快捷键在阅读界面生效。";
  section.append(heading, hint);

  const rows = new Map();
  let recordingAction = null;

  for (const action of SHORTCUT_ACTIONS) {
    const row = document.createElement("div");
    row.className = "shortcut-row";
    const label = document.createElement("label");
    label.className = "shortcut-label";
    const strong = document.createElement("strong");
    strong.textContent = action.label;
    const small = document.createElement("small");
    small.textContent = action.hint;
    label.append(strong, small);
    const combo = document.createElement("button");
    combo.type = "button";
    combo.className = "shortcut-combo";
    combo.title = "点击录制新快捷键";
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "shortcut-clear";
    clear.textContent = "清除";
    clear.title = "解绑此快捷键";
    row.append(label, combo, clear);
    section.append(row);
    rows.set(action.id, {combo, clear});
  }

  function render(map) {
    for (const [action, parts] of rows) {
      const bound = map.get(action) || "";
      parts.combo.textContent = bound || "未设置";
      parts.combo.classList.toggle("unset", !bound);
      parts.combo.classList.toggle("recording", recordingAction === action);
      if (recordingAction === action) parts.combo.textContent = "按下组合键…";
      parts.clear.hidden = !bound;
    }
  }

  async function persist(map) {
    const payload = {};
    for (const [key, value] of map) payload[key] = value;
    const result = await api("/api/settings/shortcuts", {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({shortcuts: payload}),
    });
    const saved = new Map(Object.entries(result?.shortcuts || {}));
    runtime.map = saved;
    onChangeSafe(saved);
    return saved;
  }

  function onChangeSafe(map) {
    try { runtime.onChange?.(map); } catch (_error) { /* 通知失败不影响保存 */ }
  }

  function onKeydown(event) {
    if (!recordingAction) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "Escape") {
      stopRecording();
      return;
    }
    const combo = comboFromEvent(event);
    if (!combo) return;
    const next = new Map(runtime.map);
    // 同一组合键绑定到多个动作时，取消其他动作上的旧绑定，保持一键一用
    for (const [key, value] of next) if (value.toUpperCase() === combo.toUpperCase()) next.delete(key);
    next.set(recordingAction, combo);
    recordingAction = null;
    runtime.recording = false;
    render(next);
    persist(next).then(() => announce?.(`快捷键已保存：${combo}`)).catch((error) => {
      announce?.(error.message || "快捷键保存失败。", true);
      refresh();
    });
  }

  function startRecording(action) {
    recordingAction = action;
    runtime.recording = true;
    render(runtime.map);
  }

  function stopRecording() {
    recordingAction = null;
    runtime.recording = false;
    render(runtime.map);
  }

  for (const [action, parts] of rows) {
    parts.combo.addEventListener("click", () => {
      if (recordingAction === action) stopRecording();
      else startRecording(action);
    });
    parts.clear.addEventListener("click", () => {
      const next = new Map(runtime.map);
      next.delete(action);
      render(next);
      persist(next).then(() => announce?.("已清除该快捷键。")).catch((error) => {
        announce?.(error.message || "快捷键保存失败。", true);
        refresh();
      });
    });
  }

  document.addEventListener("keydown", onKeydown, true);

  async function refresh() {
    try {
      const result = await api("/api/settings/shortcuts");
      runtime.map = new Map(Object.entries(result?.shortcuts || {}));
      onChangeSafe(runtime.map);
    } catch (_error) {
      runtime.map = new Map();
    }
    render(runtime.map);
  }

  render(runtime.map);
  return {
    element: section,
    refresh,
    stop: stopRecording,
  };
}
