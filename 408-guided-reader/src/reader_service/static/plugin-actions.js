// 插件选区动作的纯逻辑（无 DOM）：
//   * 与后端 `plugins/registry.py::clean_selection_text` 同一口径地清洗选区文本；
//   * 把注册表返回的动作按 order 排好、剔掉停用的、拼成可渲染的按钮描述。
// 拆出来是为了能单测——DOM 部分都在 plugin-host.js。

// 只削"裹在词外面"的标点，不删词中间的顿号/逗号：
// 「太阳病，脉浮」点「词典」时要查整条，不能变成「太阳病脉浮」。
export const EDGE_PUNCTUATION = " \t\r\n\u3000.,;:!?，。；：！？、·…—－-~～\"'“”‘’()（）[]【】《》<>〈〉{}「」『』*#`";

export const DEFAULT_MAX_CHARS = 60;
export const MAX_MAX_CHARS = 2000;

export function cleanSelectionText(text, { maxChars = DEFAULT_MAX_CHARS, stripPunctuation = false } = {}) {
  if (typeof text !== "string") return "";
  let value = text.replace(/\s+/g, " ").trim();
  if (stripPunctuation) {
    // 剥两遍即可覆盖「《「本虚标实」》」这类嵌套外层
    value = trimEdges(value);
    value = trimEdges(value);
  }
  const limit = Math.max(1, Math.min(Number(maxChars) || DEFAULT_MAX_CHARS, MAX_MAX_CHARS));
  return value.slice(0, limit);
}

function trimEdges(value) {
  const chars = [...EDGE_PUNCTUATION];
  let start = 0;
  let end = value.length;
  while (start < end && chars.includes(value[start])) start += 1;
  while (end > start && chars.includes(value[end - 1])) end -= 1;
  return value.slice(start, end);
}

// 停用插件、损坏插件的动作不出现；其余按 (order, plugin, id) 稳定排序。
export function orderPluginActions(actions) {
  if (!Array.isArray(actions)) return [];
  return actions
    .filter((action) => action && action.enabled !== false && action.plugin && action.id)
    .map((action) => ({
      key: `${action.plugin}:${action.id}`,
      plugin: action.plugin,
      pluginName: action.plugin_name || action.plugin,
      action: action.id,
      label: action.label || action.id,
      title: action.title || "",
      order: Number.isFinite(Number(action.order)) ? Number(action.order) : 100,
      maxChars: Number(action.maxChars) || DEFAULT_MAX_CHARS,
      stripPunctuation: action.stripPunctuation === true,
    }))
    .sort((left, right) => (
      left.order - right.order
      || left.plugin.localeCompare(right.plugin)
      || left.action.localeCompare(right.action)
    ));
}

// Reader → 插件的上下文（方案 §3.2 的 lt-plugin-context）
export function buildPluginContext({ action, text, selection = {}, theme = "light", locale = "zh-CN" } = {}) {
  return {
    type: "lt-plugin-context",
    action: action || null,
    text: text || "",
    selection: {
      page: Number.isFinite(Number(selection.page)) ? Number(selection.page) : null,
      bookId: selection.bookId || null,
      bookTitle: selection.bookTitle || null,
    },
    theme: theme === "dark" ? "dark" : "light",
    locale: locale || "zh-CN",
  };
}

// 插件地址：action/text 走 query（首屏即出结果），theme 也带上免得闪一下白底
export function pluginUrl(entryUrl, { action, text, theme = "light" } = {}) {
  const params = new URLSearchParams();
  if (action) params.set("action", action);
  if (text) params.set("text", text);
  params.set("theme", theme === "dark" ? "dark" : "light");
  const separator = entryUrl.includes("?") ? "&" : "?";
  return `${entryUrl}${separator}${params.toString()}`;
}
