// 插件宿主：拉注册表 → 渲染选区动作 → 打开弹窗 iframe → 注入上下文 → 收插件事件。
// 纯逻辑在 plugin-actions.js，这里只碰 DOM。
//
// 安全边界（方案 §3.4）：插件页与 Reader 同源，但拿不到 launch token——所以
// 插件页只能读 `/plugins/<id>/…` 的静态资源，所有 `/api/*` 都进不去。
import {
  buildPluginContext,
  cleanSelectionText,
  orderPluginActions,
  pluginUrl,
} from "/plugin-actions.js";

const DIALOG_ID = "plugin-dialog";
const FRAME_ID = "plugin-frame";
const TITLE_ID = "plugin-dialog-title";
const CLOSE_ID = "plugin-dialog-close";
const CONTAINER_ID = "plugin-selection-actions";

export function createPluginHost({
  request,
  announce = () => {},
  getContext = () => ({}),
  getTheme = () => "light",
  onSearchInBook = null,
} = {}) {
  const state = {
    plugins: new Map(),
    actions: [],
    loaded: false,
    pending: null,
    lastContext: null,
  };

  function dialog() {
    return document.getElementById(DIALOG_ID);
  }

  function frame() {
    return document.getElementById(FRAME_ID);
  }

  async function refresh() {
    try {
      const [plugins, actions] = await Promise.all([
        request("/api/plugins"),
        request("/api/selection-actions"),
      ]);
      state.plugins = new Map((plugins?.plugins || []).map((item) => [item.id, item]));
      state.actions = orderPluginActions(actions?.actions || []);
      state.loaded = true;
    } catch {
      // 插件服务不可用不该影响读书：静默降级成"没有插件动作"
      state.plugins = new Map();
      state.actions = [];
      state.loaded = false;
    }
    renderSelectionActions();
    return state.actions;
  }

  function windowPlugins() {
    return [...state.plugins.values()].filter(
      (plugin) => !plugin.problem && (plugin.surfaces || []).includes("window"),
    );
  }

  // 选区菜单里的按钮：插在「问 AI」与「高亮」之间（order 30–59 的区间）
  function renderSelectionActions() {
    const container = document.getElementById(CONTAINER_ID);
    if (!container) return;
    container.textContent = "";
    for (const action of state.actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.plugin = action.plugin;
      button.dataset.pluginAction = action.action;
      button.textContent = action.label;
      button.title = action.title || `${action.pluginName} · ${action.label}`;
      button.addEventListener("click", () => {
        triggerSelectionAction(action);
      });
      container.append(button);
    }
  }

  function triggerSelectionAction(action) {
    const selection = getContext();
    const text = cleanSelectionText(selection.text, {
      maxChars: action.maxChars,
      stripPunctuation: action.stripPunctuation,
    });
    if (!text) {
      announce("请先选中一段文字。", true);
      return;
    }
    open(action.plugin, { action: action.action, text });
  }

  // window 型插件的入口在设置的「插件」标签里（工具栏在 1024px 下已经放不下
  // 额外按钮），这里只暴露列表，渲染交给 plugins-ui。

  function ensureDialog() {
    return dialog();
  }

  function open(pluginId, { action = null, text = "" } = {}) {
    const element = ensureDialog();
    const plugin = state.plugins.get(pluginId);
    if (!element || !plugin) {
      announce("插件不可用。", true);
      return null;
    }
    if (plugin.problem) {
      announce(`插件损坏：${plugin.problem}`, true);
      return null;
    }
    const theme = getTheme();
    const context = buildPluginContext({
      action,
      text,
      selection: getContext(),
      theme,
    });
    state.lastContext = context;
    const title = document.getElementById(TITLE_ID);
    if (title) title.textContent = plugin.window?.title || plugin.name || plugin.id;
    const target = frame();
    if (target) {
      target.title = title?.textContent || plugin.name || "插件";
      target.dataset.plugin = pluginId;
      // 首次 load 后才 postMessage：iframe 还没 ready 时消息会丢
      target.onload = () => {
        try {
          target.contentWindow?.postMessage(context, "*");
        } catch {
          // 插件页自己会从 query 里读一次，postMessage 失败不影响首屏
        }
      };
      target.src = pluginUrl(plugin.url, { action, text, theme });
    }
    if (typeof element.showModal === "function" && !element.open) {
      element.showModal();
    } else if (!element.open) {
      element.setAttribute("open", "");
    }
    return context;
  }

  function openWindow(pluginId) {
    return open(pluginId, { action: null, text: "" });
  }

  function close() {
    const element = dialog();
    const target = frame();
    if (target) {
      target.onload = null;
      target.src = "about:blank";
      target.removeAttribute("data-plugin");
    }
    if (!element) return;
    if (typeof element.close === "function" && element.open) element.close();
    else element.removeAttribute("open");
  }

  // 换肤时把主题推给正打开的插件页：插件页只认 lt-plugin-context 里的 theme
  // （它们自己不会再读一次 Reader 的 data-theme），不发这一条就会停在旧配色。
  function pushTheme(theme) {
    const target = frame();
    if (!target || !target.dataset.plugin || !state.lastContext) return;
    state.lastContext = { ...state.lastContext, theme };
    try {
      target.contentWindow?.postMessage(state.lastContext, "*");
    } catch {
      // 插件页已经走了就别追了
    }
  }

  // 插件 → Reader 的可选反向事件（§3.2）
  function onMessage(event) {
    const data = event?.data;
    if (!data || data.type !== "lt-plugin-event") return;
    if (data.event === "toast") {
      announce(String(data.message || ""));
      return;
    }
    if (data.event === "search-in-book") {
      if (typeof onSearchInBook === "function" && data.text) {
        onSearchInBook(String(data.text));
      }
      return;
    }
    if (data.event === "close") close();
  }

  function bind() {
    window.addEventListener("message", onMessage);
    const host = dialog();
    if (!host) return;
    document.getElementById(CLOSE_ID)?.addEventListener("click", close);
    host.addEventListener("click", (event) => {
      // 点遮罩关闭：dialog 的 padding 区域就是遮罩
      if (event.target === host) close();
    });
    host.addEventListener("cancel", (event) => {
      event.preventDefault();
      close();
    });
    // 换一段文字再触发同一个插件时，重建 iframe 的 query 会把上下文带过去
    host.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    });
  }

  return {
    bind,
    refresh,
    open,
    openWindow,
    close,
    pushTheme,
    renderSelectionActions,
    windowPlugins,
    get actions() { return state.actions; },
    get plugins() { return [...state.plugins.values()]; },
    get loaded() { return state.loaded; },
  };
}
