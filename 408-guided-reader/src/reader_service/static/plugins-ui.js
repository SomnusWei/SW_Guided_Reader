// 「插件管理」设置页：列出随包插件与自装插件，可启停、可卸载、可装 zip。
//
// 界面只做三件事，判定全在服务端（registry.py）：启停 = PUT enabled，
// 卸载 = DELETE（随包插件服务端会拒，这里按 `removable` 提前禁掉按钮），
// 安装 = 把 zip 原样 POST 过去，由服务端校验 manifest / 后缀 / 大小 / 路径穿越。

const SOURCE_LABEL = { bundled: "随包", user: "自装" };

export function createPluginsSettings({ api, announce = () => {}, onOpen = null, onChanged = () => {} } = {}) {
  const section = document.createElement("section");
  section.className = "settings-section";
  const heading = document.createElement("h3");
  heading.textContent = "插件";
  const hint = document.createElement("p");
  hint.className = "settings-hint";
  hint.textContent =
    "插件是独立的小工具页面：选中文字时出现在选区菜单里，或从「更多」菜单单独打开。插件读不到你的书和 AI 密钥。";

  const toolbar = document.createElement("div");
  toolbar.className = "plugins-toolbar";
  const install = document.createElement("button");
  install.type = "button";
  install.className = "settings-action";
  install.textContent = "安装插件包（.zip）…";
  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = ".zip,application/zip";
  fileInput.hidden = true;
  toolbar.append(install, fileInput);

  const list = document.createElement("div");
  list.className = "plugins-list";
  const status = document.createElement("p");
  status.className = "settings-status";
  status.setAttribute("role", "status");

  section.append(heading, hint, toolbar, list, status);

  let plugins = [];
  let roots = {};

  function row(plugin) {
    const item = document.createElement("div");
    item.className = "plugin-row";
    item.dataset.pluginId = plugin.id;
    if (plugin.problem) item.classList.add("is-broken");

    const icon = document.createElement("span");
    icon.className = "plugin-icon";
    if (plugin.icon_url) {
      const img = document.createElement("img");
      img.src = plugin.icon_url;
      img.alt = "";
      img.width = 22;
      img.height = 22;
      icon.append(img);
    } else {
      icon.textContent = (plugin.name || plugin.id).slice(0, 1);
    }

    const info = document.createElement("div");
    info.className = "plugin-info";
    const title = document.createElement("strong");
    title.textContent = plugin.name || plugin.id;
    const meta = document.createElement("span");
    meta.className = "plugin-meta";
    const bits = [SOURCE_LABEL[plugin.source] || plugin.source || ""];
    if (plugin.version) bits.push(`v${plugin.version}`);
    if (plugin.category) bits.push(plugin.category);
    meta.textContent = bits.filter(Boolean).join(" · ");
    const description = document.createElement("p");
    description.className = "settings-hint";
    description.textContent = plugin.problem
      ? `不可用：${plugin.problem}`
      : plugin.description || (plugin.actions || []).map((action) => action.label).join(" / ");
    info.append(title, meta, description);

    const actions = document.createElement("div");
    actions.className = "plugin-actions-cell";

    // window 型插件可以不带选区直接打开（比如当辞典用）；
    // 入口放在这里而不是工具栏——工具栏在窄屏下已经放不下额外按钮。
    if ((plugin.surfaces || []).includes("window") && typeof onOpen === "function") {
      const open = document.createElement("button");
      open.type = "button";
      open.className = "settings-action";
      open.textContent = "打开";
      open.title = plugin.window?.title ? `打开「${plugin.window.title}」` : "打开这个插件";
      open.addEventListener("click", () => onOpen(plugin.id));
      actions.append(open);
    }

    const toggleLabel = document.createElement("label");
    toggleLabel.className = "skill-toggle";
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = plugin.enabled !== false && !plugin.problem;
    toggle.disabled = Boolean(plugin.problem);
    toggle.setAttribute("aria-label", `启用 ${plugin.name || plugin.id}`);
    toggle.addEventListener("change", async () => {
      toggle.disabled = true;
      try {
        await api(`/api/plugins/${encodeURIComponent(plugin.id)}/enabled`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: toggle.checked }),
        });
        status.textContent = `${plugin.name || plugin.id} 已${toggle.checked ? "启用" : "停用"}。`;
        await refresh();
        onChanged();
      } catch (error) {
        toggle.checked = !toggle.checked;
        status.textContent = error.message || "插件状态未保存。";
      } finally {
        toggle.disabled = Boolean(plugin.problem);
      }
    });
    toggleLabel.append(toggle);
    actions.append(toggleLabel);

    if (plugin.removable) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "settings-action";
      remove.textContent = "卸载";
      remove.addEventListener("click", async () => {
        if (!window.confirm(`卸载插件「${plugin.name || plugin.id}」？插件文件会移到回收站。`)) return;
        remove.disabled = true;
        try {
          await api(`/api/plugins/${encodeURIComponent(plugin.id)}`, { method: "DELETE" });
          status.textContent = "插件已卸载（可在数据目录的 plugins-trash 里找回）。";
          await refresh();
          onChanged();
        } catch (error) {
          remove.disabled = false;
          status.textContent = error.message || "插件未卸载。";
        }
      });
      actions.append(remove);
    } else {
      const locked = document.createElement("span");
      locked.className = "plugin-locked";
      locked.textContent = "随包";
      locked.title = "随包插件不可卸载，可以停用";
      actions.append(locked);
    }

    item.append(icon, info, actions);
    return item;
  }

  async function render() {
    list.replaceChildren();
    if (!plugins.length) {
      const empty = document.createElement("p");
      empty.className = "settings-hint";
      empty.textContent = "还没有插件。可以安装一个 .zip 插件包试试。";
      list.append(empty);
      return;
    }
    for (const plugin of plugins) list.append(row(plugin));
    const footer = document.createElement("p");
    footer.className = "settings-hint plugins-roots";
    footer.textContent = roots.user ? `自装插件目录：${roots.user}` : "";
    if (footer.textContent) list.append(footer);
  }

  async function refresh() {
    try {
      const payload = await api("/api/plugins");
      plugins = payload?.plugins || [];
      roots = payload?.roots || {};
      const broken = plugins.filter((plugin) => plugin.problem).length;
      status.textContent = broken
        ? `共 ${plugins.length} 个插件，其中 ${broken} 个无法加载（见下方说明）。`
        : `共 ${plugins.length} 个插件。`;
    } catch (error) {
      plugins = [];
      status.textContent = error.message || "插件列表暂时无法读取。";
    }
    await render();
  }

  install.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    fileInput.value = "";
    if (!file) return;
    install.disabled = true;
    status.textContent = `正在安装 ${file.name}…`;
    try {
      const payload = await api("/api/plugins/install", { method: "POST", body: file });
      status.textContent = `已安装「${payload?.plugin?.name || file.name}」。`;
      announce?.(`插件「${payload?.plugin?.name || file.name}」已安装。`);
      await refresh();
      onChanged();
    } catch (error) {
      status.textContent = error.message || "插件未安装。";
      announce?.(error.message || "插件未安装。", true);
    } finally {
      install.disabled = false;
    }
  });

  return {
    element: section,
    refresh,
    get plugins() {
      return plugins;
    },
  };
}
