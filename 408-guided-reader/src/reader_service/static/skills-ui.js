// AI skill selection and management. Skills only steer the Assistant prompt; the
// Reader never executes skill scripts. Selection is remembered per book, then globally.
const GLOBAL_KEY = "reader.skill.global";
const BOOK_PREFIX = "reader.skill.book.";
export const BUILTIN_SKILL_LABEL = "内置助手";

function safeRead(key) {
  try {
    return localStorage.getItem(key);
  } catch (_error) {
    return null;
  }
}

function safeWrite(key, value) {
  try {
    if (value === null || value === undefined || value === "") localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch (_error) { /* private mode — selection simply stays in memory */ }
}

export function normalizeSkill(payload) {
  const list = Array.isArray(payload) ? payload : (payload?.skills || []);
  return list.filter((item) => item && typeof item.name === "string").map((item) => ({
    name: item.name,
    displayName: item.display_name || item.displayName || item.name,
    description: item.description || "",
    enabled: item.enabled !== false,
    builtin: item.name === "assistant-explanation" || item.builtin === true,
    // 随包种子：可恢复、可挂资料库；用户自己装的技能没有这些
    seeded: item.seeded === true,
    sizeHuman: item.size_human || "",
    library: item.library || null,
  }));
}

export function createSkillStore({api, announce}) {
  let skills = [];
  let loading = null;
  let error = null;

  async function refresh() {
    if (loading) return loading;
    loading = (async () => {
      try {
        skills = normalizeSkill(await api("/api/skills"));
        error = null;
      } catch (failure) {
        skills = [];
        error = failure;
      } finally {
        loading = null;
      }
      return skills;
    })();
    return loading;
  }

  function bookKey(bookId) {
    return `${BOOK_PREFIX}${bookId || "unknown"}`;
  }

  function selected(bookId) {
    const remembered = safeRead(bookKey(bookId)) || safeRead(GLOBAL_KEY) || "";
    if (remembered) {
      const match = skills.find((skill) => skill.name === remembered && skill.enabled);
      if (match) return match.name;
      // 记忆的技能已被删除/停用：继续走默认勾选回落，而不是退回内置助手
    }
    // 没有记忆时与设置页的默认勾选保持一致：随包技能默认启用，问AI默认就选它，
    // 而不是回落到内置助手（真机反馈：默认技能应与设置勾选一致）。
    const seeded = skills.find((skill) => skill.seeded && skill.enabled);
    return seeded ? seeded.name : "";
  }

  function remember(bookId, name) {
    safeWrite(bookKey(bookId), name);
    safeWrite(GLOBAL_KEY, name);
  }

  function label(name) {
    if (!name) return BUILTIN_SKILL_LABEL;
    return skills.find((skill) => skill.name === name)?.displayName || name;
  }

  function options() {
    return [{name: "", displayName: BUILTIN_SKILL_LABEL, description: "使用 Reader 内置解释提示词", enabled: true,
      builtin: true}, ...skills.filter((skill) => skill.enabled)];
  }

  async function setEnabled(name, enabled) {
    await api(`/api/skills/${encodeURIComponent(name)}/enabled`, {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled}),
    });
    await refresh();
  }

  async function install(path) {
    await api("/api/skills/install", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({source_dir: path}),
    });
    await refresh();
  }

  async function remove(name) {
    await api(`/api/skills/${encodeURIComponent(name)}`, {method: "DELETE"});
    await refresh();
  }

  /** 用随包种子覆盖恢复（旧目录会被挪到 skills-trash，不会直接丢）。 */
  async function restore(name) {
    await api(`/api/skills/${encodeURIComponent(name)}/restore`, {method: "POST"});
    await refresh();
  }

  /** 资料库只登记路径，不复制文件；不挂也能正常用技能。 */
  async function mountLibrary(name, path) {
    await api(`/api/skills/${encodeURIComponent(name)}/library`, {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path}),
    });
    await refresh();
  }

  async function unmountLibrary(name) {
    await api(`/api/skills/${encodeURIComponent(name)}/library`, {method: "DELETE"});
    await refresh();
  }

  return {
    refresh, selected, remember, label, options, setEnabled, install, remove,
    restore, mountLibrary, unmountLibrary,
    get skills() { return skills; },
    get error() { return error; },
  };
}

export function createSkillPicker({store, bookId, onChange = () => {}}) {
  const bar = document.createElement("div");
  bar.className = "assistant-skill-bar";
  const label = document.createElement("label");
  label.className = "assistant-skill";
  const caption = document.createElement("span");
  caption.className = "assistant-skill-caption";
  caption.textContent = "技能";
  const select = document.createElement("select");
  select.id = "assistant-skill";
  select.setAttribute("aria-label", "回答技能");
  caption.htmlFor = select.id;
  label.append(caption, select);
  const note = document.createElement("span");
  note.className = "assistant-skill-note";
  note.hidden = true;
  bar.append(label, note);

  function renderOptions() {
    const current = store.selected(bookId()) || "";
    const options = store.options();
    select.replaceChildren(...options.map((skill) => {
      const option = document.createElement("option");
      option.value = skill.name;
      option.textContent = skill.displayName;
      option.title = skill.description || skill.displayName;
      return option;
    }));
    select.value = options.some((skill) => skill.name === current) ? current : "";
    const chosen = options.find((skill) => skill.name === select.value);
    bar.title = chosen?.description || "";
    note.hidden = !store.error;
    note.textContent = store.error ? "技能列表不可用，使用内置助手" : "";
  }

  select.addEventListener("change", () => {
    store.remember(bookId(), select.value);
    renderOptions();
    onChange(select.value);
  });

  renderOptions();
  return {
    element: bar,
    sync: renderOptions,
    value: () => select.value || "",
    async load() {
      await store.refresh();
      renderOptions();
    },
  };
}

export function createSkillsSettings({store, announce, onChange = () => {}}) {
  const section = document.createElement("section");
  section.className = "settings-section";
  const heading = document.createElement("h3");
  heading.textContent = "SKILL 管理";
  const hint = document.createElement("p");
  hint.className = "settings-hint";
  hint.textContent = "技能只影响问 AI 时的提示词，不会执行技能中的脚本。每个技能可单独启用或停用。";
  const list = document.createElement("div");
  list.className = "skills-list";
  const install = document.createElement("button");
  install.type = "button";
  install.className = "settings-action";
  install.textContent = "安装本地技能…";
  const status = document.createElement("p");
  status.className = "settings-status";
  status.setAttribute("role", "status");
  section.append(heading, hint, list, install, status);

  // 资料库一段：只登记路径，不复制文件（§6.5）。2.0 里它是状态标记 + 2.1 的登记点。
  function libraryBlock(skill) {
    const library = skill.library || {mounted: false};
    const box = document.createElement("div");
    box.className = "skill-library";
    const label = document.createElement("span");
    label.className = "skill-library-label";
    label.textContent = "资料库";
    const value = document.createElement("span");
    value.className = "skill-library-value";
    if (!library.mounted) {
      value.textContent = "未挂载（可选）";
    } else if (!library.reachable) {
      value.textContent = `${library.path}（路径不可达，不影响使用）`;
      value.classList.add("is-warning");
    } else {
      const books = (library.textbooks || [])
        .map((item) => `${item.name} ${item.files} 份`)
        .join(" · ");
      value.textContent = `${library.path}${books ? `　${books}` : ""}`;
    }
    const actions = document.createElement("span");
    actions.className = "skill-library-actions";

    const mount = document.createElement("button");
    mount.type = "button";
    mount.className = "settings-action";
    mount.textContent = library.mounted ? "改路径…" : "挂载资料目录…";
    mount.addEventListener("click", async () => {
      const path = window.prompt(
        "资料目录（含 SKILL.md 的技能目录，例如存放「中医教材」的上级目录）：",
        library.path || ""
      );
      if (!path) return;
      mount.disabled = true;
      try {
        await store.mountLibrary(skill.name, path.trim());
        status.textContent = "资料目录已登记（不会复制任何文件）。";
        await render();
        onChange();
      } catch (error) {
        status.textContent = error.message || "资料目录未登记。";
      } finally {
        mount.disabled = false;
      }
    });
    actions.append(mount);

    if (library.mounted) {
      const unmount = document.createElement("button");
      unmount.type = "button";
      unmount.className = "settings-action";
      unmount.textContent = "取消挂载";
      unmount.addEventListener("click", async () => {
        unmount.disabled = true;
        try {
          await store.unmountLibrary(skill.name);
          status.textContent = "已取消资料目录挂载。";
          await render();
          onChange();
        } catch (error) {
          status.textContent = error.message || "未取消挂载。";
        } finally {
          unmount.disabled = false;
        }
      });
      actions.append(unmount);
    }

    box.append(label, value, actions);
    return box;
  }

  async function render() {
    list.replaceChildren();
    await store.refresh();
    if (!store.skills.length) {
      const empty = document.createElement("p");
      empty.className = "settings-hint";
      empty.textContent = store.error ? `技能列表暂时不可用：${store.error.message}` : "还没有安装额外技能。";
      list.append(empty);
      return;
    }
    for (const skill of store.skills) {
      const row = document.createElement("div");
      row.className = "skill-row";
      row.dataset.skillName = skill.name;
      const info = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = skill.displayName;
      const meta = document.createElement("span");
      meta.className = "skill-meta";
      meta.textContent = [skill.seeded ? "随包内置" : "", skill.sizeHuman !== "0B" ? skill.sizeHuman : ""]
        .filter(Boolean).join(" · ");
      const description = document.createElement("p");
      description.className = "settings-hint";
      description.textContent = skill.description || skill.name;
      info.append(name);
      if (meta.textContent) info.append(meta);
      info.append(description);
      if (skill.seeded) info.append(libraryBlock(skill));

      const toggle = document.createElement("label");
      toggle.className = "skill-toggle";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = skill.enabled;
      input.setAttribute("aria-label", `启用 ${skill.displayName}`);
      if (skill.builtin) {
        input.disabled = true;
        input.title = "内置技能不可停用";
      }
      input.addEventListener("change", async () => {
        input.disabled = true;
        try {
          await store.setEnabled(skill.name, input.checked);
          status.textContent = `${skill.displayName} 已${input.checked ? "启用" : "停用"}。`;
          await render();
          onChange();
        } catch (error) {
          input.checked = !input.checked;
          status.textContent = error.message || "技能状态未保存。";
        } finally {
          input.disabled = false;
        }
      });
      toggle.append(input);
      const actions = document.createElement("div");
      actions.className = "skill-actions";
      actions.append(toggle);
      if (skill.seeded) {
        const restore = document.createElement("button");
        restore.type = "button";
        restore.className = "settings-action";
        restore.textContent = "恢复内置";
        restore.title = "用随包版本覆盖当前技能文件；现有版本会移到 skills-trash，可找回";
        restore.addEventListener("click", async () => {
          if (!window.confirm(`用随包版本恢复「${skill.displayName}」？当前的改动会被移到 skills-trash。`)) return;
          restore.disabled = true;
          try {
            await store.restore(skill.name);
            status.textContent = "已从随包版本恢复。";
            await render();
            onChange();
          } catch (error) {
            status.textContent = error.message || "恢复失败。";
          } finally {
            restore.disabled = false;
          }
        });
        actions.append(restore);
      }
      if (!skill.builtin) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "settings-action";
        remove.textContent = "删除";
        remove.addEventListener("click", async () => {
          if (!window.confirm(`删除技能「${skill.displayName}」？`)) return;
          try {
            await store.remove(skill.name);
            status.textContent = "技能已删除。";
            await render();
            onChange();
          } catch (error) {
            status.textContent = error.message || "技能未删除。";
          }
        });
        actions.append(remove);
      }
      row.append(info, actions);
      list.append(row);
    }
  }

  install.addEventListener("click", async () => {
    const path = window.prompt("输入本地技能文件夹路径（包含 SKILL.md）：");
    if (!path) return;
    install.disabled = true;
    try {
      await store.install(path.trim());
      status.textContent = "技能已安装。";
      await render();
      onChange();
      announce?.("技能已安装。");
    } catch (error) {
      status.textContent = error.message || "技能未安装。";
      announce?.(error.message || "技能未安装。", true);
    } finally {
      install.disabled = false;
    }
  });

  render();
  return {element: section, refresh: render};
}
