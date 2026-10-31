// Reader 设置：六个标签（通用 / 插件 / SKILL / AI / 快捷键 / 关于）。
// 全部是本机设置，不上传任何东西。各段内容由其它模块提供（插件管理、技能管理、
// AI 密钥、快捷键），这里只负责装标签、切面板、把打开时才需要的数据拉一遍。
const OCR_DISMISSED_KEY = "reader.ocr-banner.dismissed";

const SYNC_MODES = Object.freeze([
  {value: "OFF", label: "关闭", hint: "只保存到 Reader，不改动 PDF。"},
  {value: "MANUAL", label: "手动导出", hint: "需要时点「立即写回 PDF」。"},
  {value: "AUTO", label: "自动写回", hint: "保存批注后自动写入 PDF。"},
]);

const SYNC_TARGETS = Object.freeze([
  {value: "SOURCE", label: "源文件"},
  {value: "COPY", label: "库内副本"},
]);

const THEME_CHOICES = Object.freeze([
  {value: "light", label: "浅色", hint: "原来的纸墨配色。"},
  {value: "dark", label: "深色", hint: "夜间阅读，PDF 只压暗不反色。"},
  {value: "system", label: "跟随系统", hint: "系统切换到深色时自动跟着切。"},
]);

function normalizeMode(value) {
  const upper = String(value || "").toUpperCase();
  return SYNC_MODES.some((mode) => mode.value === upper) ? upper : "MANUAL";
}

function normalizeTarget(value) {
  const upper = String(value || "").toUpperCase();
  return SYNC_TARGETS.some((target) => target.value === upper) ? upper : "SOURCE";
}

function section(title, hint) {
  const element = document.createElement("section");
  element.className = "settings-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  element.append(heading);
  if (hint) {
    const paragraph = document.createElement("p");
    paragraph.className = "settings-hint";
    paragraph.textContent = hint;
    element.append(paragraph);
  }
  return element;
}

export function createOcrBanner() {
  const banner = document.createElement("div");
  banner.id = "ocr-banner";
  banner.className = "ocr-banner";
  banner.hidden = true;
  banner.setAttribute("role", "status");
  const text = document.createElement("p");
  text.textContent = "OCR 服务（Umi-OCR）未启动，扫描件选字/搜索不可用；文字版 PDF 不受影响。";
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "×";
  close.setAttribute("aria-label", "关闭提示");
  close.addEventListener("click", () => {
    banner.hidden = true;
    try { sessionStorage.setItem(OCR_DISMISSED_KEY, "1"); } catch (_error) { /* ignore */ }
  });
  banner.append(text, close);
  let dismissed = false;
  try { dismissed = sessionStorage.getItem(OCR_DISMISSED_KEY) === "1"; } catch (_error) { /* ignore */ }

  return {
    element: banner,
    update(ocr) {
      const unavailable = ocr && ocr.available === false;
      banner.hidden = !unavailable || dismissed;
    },
    hide() { banner.hidden = true; },
  };
}

export function createSettingsUI({
  api, revisionId, announce, skillsSection, aiKeysSection, shortcutsSection, pluginsSection,
  themeController, onSyncNow, onSyncStateChange,
}) {
  const dialog = document.createElement("dialog");
  dialog.id = "settings-dialog";
  dialog.className = "settings-dialog";
  dialog.setAttribute("aria-labelledby", "settings-title");
  dialog.innerHTML = `
    <header class="settings-heading">
      <h2 id="settings-title">设置</h2>
      <button type="button" id="settings-close" aria-label="关闭设置">关闭 ×</button>
    </header>
    <div class="settings-tabs" role="tablist" aria-label="设置分类"></div>
    <div class="settings-body"></div>`;
  document.body.append(dialog);
  const body = dialog.querySelector(".settings-body");
  const tabStrip = dialog.querySelector(".settings-tabs");
  dialog.querySelector("#settings-close").addEventListener("click", () => dialog.close());

  const panels = new Map();
  const tabs = [];
  const tabButtons = new Map();

  function addTab(id, label, build) {
    const panel = document.createElement("div");
    panel.className = "settings-panel";
    panel.dataset.tab = id;
    panel.setAttribute("role", "tabpanel");
    panel.id = `settings-panel-${id}`;
    panel.hidden = true;
    build(panel);
    body.append(panel);
    panels.set(id, panel);
    tabs.push({id, label});
  }

  function selectTab(id, {focus = false} = {}) {
    for (const [key, panel] of panels) panel.hidden = key !== id;
    for (const [key, button] of tabButtons) {
      const active = key === id;
      button.setAttribute("aria-selected", active ? "true" : "false");
      button.classList.toggle("is-active", active);
      button.tabIndex = active ? 0 : -1;
      if (active && focus) button.focus({preventScroll: true});
    }
    onTabShown(id);
  }

  function onTabShown(id) {
    if (id === "plugins") pluginsSection?.refresh?.();
    if (id === "skills") skillsSection?.refresh?.();
    if (id === "ai") aiKeysSection?.refresh?.();
    if (id === "shortcuts") shortcutsSection?.refresh?.();
    if (id === "general") load();
    if (id === "about") loadAbout();
  }

  // -- 通用：外观 ------------------------------------------------------------
  const appearanceSection = section("外观", "深色只改界面配色；PDF 页面只压暗，不会反色。");
  const themeGroup = document.createElement("fieldset");
  themeGroup.className = "settings-fieldset";
  themeGroup.innerHTML = "<legend>主题</legend>";
  const themeInputs = new Map();
  for (const choice of THEME_CHOICES) {
    const label = document.createElement("label");
    label.className = "settings-radio";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "theme";
    input.value = choice.value;
    const text = document.createElement("span");
    text.innerHTML = `<strong></strong><small></small>`;
    text.querySelector("strong").textContent = choice.label;
    text.querySelector("small").textContent = choice.hint;
    label.append(input, text);
    themeGroup.append(label);
    themeInputs.set(choice.value, input);
  }
  const themeStatus = document.createElement("p");
  themeStatus.className = "settings-status";
  themeStatus.setAttribute("role", "status");
  appearanceSection.append(themeGroup, themeStatus);

  function syncThemeInputs() {
    const pref = themeController?.pref || "system";
    for (const [value, input] of themeInputs) input.checked = value === pref;
  }

  for (const [value, input] of themeInputs) {
    input.addEventListener("change", async () => {
      if (!input.checked || !themeController) return;
      const result = await themeController.set(value);
      syncThemeInputs();
      themeStatus.textContent = result.saved === false
        ? "主题已切换，但没保存住（下次启动可能回退）。"
        : "主题已保存。";
    });
  }

  // -- 通用：批注写回 --------------------------------------------------------
  const syncSection = section(
    "批注写回 PDF",
    "写回会把 Reader 中的批注画进 PDF。「源文件」会修改导入的原文件；「库内副本」只改库内保存的那份。"
  );
  const modeGroup = document.createElement("fieldset");
  modeGroup.className = "settings-fieldset";
  modeGroup.innerHTML = "<legend>写回方式</legend>";
  const modeInputs = new Map();
  for (const mode of SYNC_MODES) {
    const label = document.createElement("label");
    label.className = "settings-radio";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "annotation-sync-mode";
    input.value = mode.value;
    input.checked = mode.value === "MANUAL";
    const text = document.createElement("span");
    text.innerHTML = `<strong></strong><small></small>`;
    text.querySelector("strong").textContent = mode.label;
    text.querySelector("small").textContent = mode.hint;
    label.append(input, text);
    modeGroup.append(label);
    modeInputs.set(mode.value, input);
  }
  const targetGroup = document.createElement("fieldset");
  targetGroup.className = "settings-fieldset";
  targetGroup.innerHTML = "<legend>写回目标</legend>";
  const targetInputs = new Map();
  for (const target of SYNC_TARGETS) {
    const label = document.createElement("label");
    label.className = "settings-radio";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "annotation-sync-target";
    input.value = target.value;
    input.checked = target.value === "SOURCE";
    const text = document.createElement("span");
    text.textContent = target.label;
    label.append(input, text);
    targetGroup.append(label);
    targetInputs.set(target.value, input);
  }
  const syncRow = document.createElement("div");
  syncRow.className = "settings-row";
  const syncNow = document.createElement("button");
  syncNow.type = "button";
  syncNow.className = "settings-action";
  syncNow.textContent = "立即写回 PDF";
  const syncStatus = document.createElement("p");
  syncStatus.className = "settings-status";
  syncStatus.setAttribute("role", "status");
  syncRow.append(syncNow);
  syncSection.append(modeGroup, targetGroup, syncRow, syncStatus);

  // -- 通用：存储与服务状态 ---------------------------------------------------
  const storageSection = section("存储", "所有书、笔记与设置都只存在这台电脑上。");
  const libraryStatus = document.createElement("p");
  libraryStatus.className = "settings-hint";
  libraryStatus.textContent = "书库位置读取中…";
  const libraryRow = document.createElement("div");
  libraryRow.className = "settings-row";
  const libraryChange = document.createElement("button");
  libraryChange.type = "button";
  libraryChange.className = "settings-action";
  libraryChange.textContent = "更换书库目录…";
  const libraryMigrate = document.createElement("button");
  libraryMigrate.type = "button";
  libraryMigrate.className = "settings-action";
  libraryMigrate.textContent = "迁移已有文件";
  libraryMigrate.hidden = true;
  libraryRow.append(libraryChange, libraryMigrate);
  const libraryNote = document.createElement("p");
  libraryNote.className = "settings-status";
  libraryNote.setAttribute("role", "status");
  const ocrStatus = document.createElement("p");
  ocrStatus.className = "settings-hint";
  ocrStatus.textContent = "OCR 状态读取中…";
  storageSection.append(libraryStatus, libraryRow, libraryNote, ocrStatus);

  let libraryRootIsDefault = true;

  async function refreshLibraryMigrate() {
    libraryMigrate.hidden = true;
    try {
      const status = await api("/api/settings/library/migrate");
      if (status.target && status.pending > 0) {
        libraryMigrate.hidden = false;
        libraryMigrate.textContent = `迁移已有文件（${status.pending} 个）`;
      }
    } catch (error) { /* 没换过目录就没有迁移一说 */ }
  }

  libraryChange.addEventListener("click", async () => {
    const path = window.prompt(
      "新的书库目录（之后导入的 PDF 会存到这里；已有文件不动）：",
      ""
    );
    if (!path) return;
    libraryChange.disabled = true;
    try {
      const stats = await api("/api/settings/library", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path.trim() }),
      });
      libraryRootIsDefault = stats.is_default !== false;
      announce?.("书库目录已更新，之后导入的新书会存到新目录。");
      await loadLibrary();
    } catch (error) {
      libraryNote.textContent = error.message || "书库目录未保存。";
      announce?.(error.message || "书库目录未保存。", true);
    } finally {
      libraryChange.disabled = false;
    }
  });

  libraryMigrate.addEventListener("click", async () => {
    if (!window.confirm("把旧目录里的 PDF 文件搬到新书库目录？同名文件会跳过，不会覆盖。")) return;
    libraryMigrate.disabled = true;
    libraryNote.textContent = "正在迁移…";
    try {
      const result = await api("/api/settings/library/migrate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dryRun: false }),
      });
      const failed = result.failed?.length ? `，${result.failed.length} 个失败` : "";
      libraryNote.textContent = `已迁移 ${result.moved} 个文件${failed}。原有书籍记录不受影响。`;
      announce?.(`已迁移 ${result.moved} 个文件。`);
      await loadLibrary();
    } catch (error) {
      libraryNote.textContent = error.message || "迁移失败。";
    } finally {
      libraryMigrate.disabled = false;
    }
  });

  async function loadLibrary() {
    try {
      const library = await api("/api/settings/library");
      const parts = [`书库：${library.books ?? "—"} 本`];
      if (library.files) parts.push(`${library.files} 个文件`);
      if (library.root) parts.push(library.root);
      libraryStatus.textContent = parts.join(" · ");
      libraryRootIsDefault = Boolean(library.is_default);
      libraryChange.textContent = libraryRootIsDefault ? "更换书库目录…" : "书库目录不是默认位置";
      await refreshLibraryMigrate();
    } catch (error) {
      libraryStatus.textContent = error.message || "书库信息暂时无法读取。";
    }
  }

  addTab("general", "通用", (panel) => {
    panel.append(appearanceSection, syncSection, storageSection);
  });
  addTab("plugins", "插件", (panel) => {
    if (pluginsSection) panel.append(pluginsSection.element);
  });
  addTab("skills", "SKILL", (panel) => {
    if (skillsSection) panel.append(skillsSection.element);
  });
  addTab("ai", "AI 设置", (panel) => {
    if (aiKeysSection) panel.append(aiKeysSection.element);
  });
  addTab("shortcuts", "快捷键", (panel) => {
    if (shortcutsSection) panel.append(shortcutsSection.element);
  });

  // -- 关于 ------------------------------------------------------------------
  const aboutSection = section("关于", "");
  const aboutList = document.createElement("dl");
  aboutList.className = "settings-about";
  const aboutStatus = document.createElement("p");
  aboutStatus.className = "settings-status";
  aboutStatus.setAttribute("role", "status");
  aboutSection.append(aboutList, aboutStatus);
  addTab("about", "关于", (panel) => panel.append(aboutSection));

  async function loadAbout() {
    aboutStatus.textContent = "";
    try {
      const info = await api("/api/about");
      const rows = [
        ["版本", info.version || "—"],
        ["构建时间", info.build_time || "—"],
        ["运行方式", info.frozen ? "安装版" : "源码 / 开发"],
        ["数据目录", info.data_dir || "—"],
        ["技能目录", info.skills_dir || "—"],
        ["插件目录（随包）", info.plugin_roots?.bundled || "—"],
        ["插件目录（自装）", info.plugin_roots?.user || "—"],
        ["日志目录", info.log_dir || "—"],
      ];
      aboutList.replaceChildren(...rows.flatMap(([term, value]) => {
        const dt = document.createElement("dt");
        dt.textContent = term;
        const dd = document.createElement("dd");
        dd.textContent = value;
        dd.title = value;
        return [dt, dd];
      }));
    } catch (error) {
      aboutStatus.textContent = error.message || "版本信息暂时无法读取。";
    }
  }

  // 标签按钮
  for (const tab of tabs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "settings-tab";
    button.textContent = tab.label;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", `settings-panel-${tab.id}`);
    button.addEventListener("click", () => selectTab(tab.id));
    tabStrip.append(button);
    tabButtons.set(tab.id, button);
  }
  // 左右方向键切标签（WAI-ARIA tabs 惯例）
  tabStrip.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const order = tabs.map((tab) => tab.id);
    const current = order.findIndex((id) => tabButtons.get(id).getAttribute("aria-selected") === "true");
    const delta = event.key === "ArrowRight" ? 1 : -1;
    const next = order[(current + delta + order.length) % order.length];
    event.preventDefault();
    selectTab(next, {focus: true});
  });

  const state = {mode: "MANUAL", target: "SOURCE", available: true};

  function applyState(next) {
    state.mode = normalizeMode(next?.mode);
    state.target = normalizeTarget(next?.target ?? next?.sync_target);
    if (typeof next?.available === "boolean") state.available = next.available;
    modeInputs.get(state.mode).checked = true;
    targetInputs.get(state.target).checked = true;
    syncNow.hidden = state.mode !== "MANUAL" || !state.available;
    syncNow.disabled = !state.available;
    for (const input of [...modeInputs.values(), ...targetInputs.values()]) {
      input.disabled = !state.available;
    }
    if (!state.available) {
      syncStatus.textContent = "当前服务未启用 PDF 写回（缺少 PDF 同步组件）。";
    }
    onSyncStateChange?.(state);
  }

  async function load() {
    syncThemeInputs();
    try {
      applyState(await api("/api/settings/annotation-sync"));
    } catch (error) {
      syncStatus.textContent = error.status === 404
        ? "当前服务未提供批注写回设置。" : (error.message || "写回设置暂时无法读取。");
    }
    await loadLibrary();
  }

  async function save(patch) {
    const previous = {...state};
    applyState({...state, ...patch});
    syncStatus.textContent = "正在保存…";
    try {
      const saved = await api("/api/settings/annotation-sync", {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        // 服务端按小写词表存储（off / manual / auto，source / copy）。
        body: JSON.stringify({mode: state.mode.toLowerCase(), target: state.target.toLowerCase()}),
      });
      applyState(saved || state);
      syncStatus.textContent = "设置已保存。";
    } catch (error) {
      applyState(previous);
      syncStatus.textContent = error.message || "设置未保存。";
    }
  }

  for (const [value, input] of modeInputs) {
    input.addEventListener("change", () => { if (input.checked) save({mode: value}); });
  }
  for (const [value, input] of targetInputs) {
    input.addEventListener("change", () => { if (input.checked) save({target: value}); });
  }

  syncNow.addEventListener("click", async () => {
    if (!onSyncNow) return;
    syncNow.disabled = true;
    syncStatus.textContent = "正在写回…";
    try {
      const result = await onSyncNow();
      syncStatus.textContent = result || "已写回 PDF。";
      announce?.("批注已写回 PDF。");
    } catch (error) {
      syncStatus.textContent = error.message || "写回失败。";
      announce?.(error.message || "写回失败。", true);
    } finally {
      syncNow.disabled = false;
    }
  });

  selectTab("general");

  return {
    element: dialog,
    open(tab = "general") {
      if (panels.has(tab)) selectTab(tab);
      else onTabShown("general");
      syncThemeInputs();
      if (!dialog.open) dialog.showModal();
    },
    close() { if (dialog.open) dialog.close(); },
    load,
    selectTab,
    setOcr(ocr) {
      const unavailable = ocr && ocr.available === false;
      ocrStatus.textContent = unavailable
        ? "OCR 服务（Umi-OCR）未启动：扫描件无法选字与搜索，文字版 PDF 不受影响。"
        : ocr && ocr.available
          ? "OCR 服务可用：扫描件可选字与搜索。"
          : "OCR 状态暂不可用；文字版 PDF 不受影响。";
    },
    get state() { return {...state}; },
    get wantsSyncButton() { return state.mode === "MANUAL" && state.available; },
  };
}
