// AI provider key management inside the settings dialog. Keys are stored in
// the Windows Credential Manager under the standard
// `408-guided-reader-{provider}` targets; nothing is sent anywhere except the
// provider itself when the user asks the AI a question.
export function createAiKeysSettings({api, announce}) {
  const section = document.createElement("section");
  section.className = "settings-section";
  const heading = document.createElement("h3");
  heading.textContent = "AI 模型密钥";
  const hint = document.createElement("p");
  hint.className = "settings-hint";
  hint.textContent = "为需要的模型厂商保存 API Key（存入本机凭据管理器，不上传）。保存后即可在问 AI 时使用对应厂商；豆包/自定义还需按 .env.example 配置模型接入点。";
  const list = document.createElement("div");
  list.className = "ai-keys-list";
  const status = document.createElement("p");
  status.className = "settings-status";
  status.setAttribute("role", "status");
  section.append(heading, hint, list, status);

  const rows = new Map(); // provider -> {input, badge, saveBtn, clearBtn}

  function setStatus(text, isError = false) {
    status.className = "settings-status" + (isError ? " settings-status-error" : "");
    status.textContent = text;
  }

  function renderRow(provider) {
    const row = document.createElement("div");
    row.className = "skill-row";
    row.dataset.provider = provider.name;

    const info = document.createElement("div");
    const nameLine = document.createElement("strong");
    nameLine.textContent = provider.display_name;
    const badge = document.createElement("span");
    badge.className = "ai-key-badge";
    info.append(nameLine, badge);
    const endpoint = document.createElement("p");
    endpoint.className = "settings-hint";
    endpoint.textContent = provider.default_model
      ? `${provider.name} · 默认模型 ${provider.default_model}`
      : `${provider.name} · 需自行配置模型`;
    info.append(endpoint);

    const actions = document.createElement("div");
    actions.className = "skill-actions ai-key-actions";

    const input = document.createElement("input");
    input.type = "password";
    input.className = "ai-key-input";
    input.placeholder = provider.configured ? "已配置 · 输入新值可覆盖" : "输入 API Key";
    input.setAttribute("autocomplete", "off");
    input.setAttribute("aria-label", `${provider.display_name} API Key`);

    const save = document.createElement("button");
    save.type = "button";
    save.className = "settings-action";
    save.textContent = "保存";

    const clear = document.createElement("button");
    clear.type = "button";
    save.className = "settings-action";
    clear.textContent = "清除";
    clear.disabled = !provider.configured;

    actions.append(input, save, clear);
    row.append(info, actions);
    list.append(row);
    rows.set(provider.name, {input, badge, save, clear, provider});

    save.addEventListener("click", async () => {
      const value = input.value.trim();
      if (!value) {
        setStatus(`${provider.display_name}：请先输入 API Key。`, true);
        return;
      }
      save.disabled = true;
      try {
        await api(`/api/settings/providers/${encodeURIComponent(provider.name)}/key`, {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({api_key: value}),
        });
        input.value = "";
        input.placeholder = "已配置 · 输入新值可覆盖";
        badge.textContent = "已配置";
        badge.classList.add("on");
        clear.disabled = false;
        setStatus(`${provider.display_name} 密钥已保存。`);
        announce?.(`${provider.display_name} 密钥已保存。`);
      } catch (error) {
        setStatus(`${provider.display_name}：${error.message || "密钥未保存。"}`, true);
      } finally {
        save.disabled = false;
      }
    });

    clear.addEventListener("click", async () => {
      clear.disabled = true;
      try {
        await api(`/api/settings/providers/${encodeURIComponent(provider.name)}/key`, {
          method: "DELETE",
        });
        badge.textContent = "未配置";
        badge.classList.remove("on");
        input.placeholder = "输入 API Key";
        setStatus(`${provider.display_name} 密钥已清除。`);
      } catch (error) {
        setStatus(`${provider.display_name}：${error.message || "密钥未清除。"}`, true);
      } finally {
        clear.disabled = !badge.classList.contains("on");
      }
    });
  }

  function paintBadge(row, configured) {
    row.badge.textContent = configured ? "已配置" : "未配置";
    row.badge.classList.toggle("on", Boolean(configured));
    row.clear.disabled = !configured;
  }

  async function refresh() {
    list.replaceChildren();
    rows.clear();
    let catalog = [];
    try {
      const payload = await api("/api/providers");
      catalog = Array.isArray(payload?.providers) ? payload.providers : [];
    } catch (error) {
      const empty = document.createElement("p");
      empty.className = "settings-hint";
      empty.textContent = error.message || "模型列表暂时不可用。";
      list.append(empty);
      return;
    }
    for (const provider of catalog) {
      renderRow(provider);
      paintBadge(rows.get(provider.name), provider.configured);
    }
  }

  refresh();
  return {element: section, refresh: refresh};
}
