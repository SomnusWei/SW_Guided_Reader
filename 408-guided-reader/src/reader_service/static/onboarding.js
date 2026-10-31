// 三步首启引导（方案 §6.4）：只在 settings.onboarding.done 为 false 时出现一次。
//
// 设计红线：
//  * 老用户（1.3.1 升级 / 本机已有技能）完全不弹——done 视为 true 由服务端保证；
//  * 资料库一步的文案必须诚实：AI 问答不需要教材文件，挂载只是登记路径不复制；
//  * Esc 可关、不阻塞读书，关掉等价于「跳过」并直接完成引导。
//
// 纯逻辑（步骤推进、候选路径挑选）与 DOM 分离，便于单测。

export function pickCandidate(candidates) {
  if (!Array.isArray(candidates)) return "";
  return (
    candidates.find(
      (item) => typeof item === "string" && item.trim() && !/[\\/]$/.test(item),
    ) || ""
  );
}

/** 步骤机：0 欢迎 → 1 资料库 → 2 完成；任何一步都能直接「跳过」完成引导。 */
export function createOnboardingSteps({ total = 3 } = {}) {
  let index = 0;
  return {
    get index() {
      return index;
    },
    get total() {
      return total;
    },
    next() {
      index = Math.min(index + 1, total - 1);
      return index;
    },
    finish() {
      index = total - 1;
      return index;
    },
    get done() {
      return index === total - 1;
    },
  };
}

export function createOnboarding({ api, announce = () => {}, onDone = () => {} } = {}) {
  const dialog = document.createElement("dialog");
  dialog.id = "onboarding-dialog";
  dialog.className = "onboarding-dialog";
  dialog.setAttribute("aria-labelledby", "onboarding-title");
  document.body.append(dialog);

  const steps = createOnboardingSteps();
  const state = { candidates: [], books: 0, candidate: "", mounted: false };

  function render() {
    const step = steps.index;
    const parts = [];
    if (step === 0) {
      parts.push(`
        <p class="onboarding-eyebrow">欢迎使用</p>
        <h2 id="onboarding-title">启动即书库</h2>
        <p class="onboarding-lead">打开软件就是你的教材书库：选字问 AI、做批注、记笔记，全部保存在这台电脑上。</p>
        ${state.books ? `<p class="onboarding-note">已保留你的 ${state.books} 本书与全部批注、笔记。</p>` : ""}
      `);
    } else if (step === 1) {
      parts.push(`
        <p class="onboarding-eyebrow">可选 · 资料库</p>
        <h2 id="onboarding-title">要不要挂上你的教材目录？</h2>
        <p class="onboarding-lead">AI 问答<strong>不需要</strong>教材文件，内置知识包已经足够开始用。</p>
        <p class="onboarding-note">若你想在本地随时翻教材原文，可以把已有的 yixue-zonghe 目录登记进来——只登记路径，不复制、不占空间。</p>
        <div class="onboarding-candidate" id="onboarding-candidate"></div>
      `);
    } else {
      parts.push(`
        <p class="onboarding-eyebrow">就绪</p>
        <h2 id="onboarding-title">知识包已就绪</h2>
        <p class="onboarding-lead">中医知识包（辨证 / 方剂 / 针灸）已随软件装好，问 AI 时自动生效。以后可以在 设置 → SKILL 里查看与调整。</p>
      `);
    }
    const buttons = [];
    if (step === 1) {
      buttons.push(`<button type="button" class="onboarding-ghost" data-step="detect">自动检测</button>`);
      buttons.push(`<button type="button" class="onboarding-ghost" data-step="browse">选择目录…</button>`);
      buttons.push(`<button type="button" class="onboarding-ghost" data-step="skip-mount">跳过</button>`);
      buttons.push(`<button type="button" class="onboarding-primary" data-step="next" ${state.candidate ? "" : "disabled"}>挂载并继续</button>`);
    } else {
      buttons.push(
        step === 0
          ? `<button type="button" class="onboarding-primary" data-step="next">继续</button>
             <button type="button" class="onboarding-ghost" data-step="skip-mount">跳过引导</button>`
          : `<button type="button" class="onboarding-primary" data-step="finish">进入书库</button>`,
      );
    }
    parts.push(`<div class="onboarding-actions">${buttons.join("")}</div>`);
    dialog.innerHTML = parts.join("");

    if (step === 1) {
      const box = dialog.querySelector("#onboarding-candidate");
      if (state.candidate) {
        const label = document.createElement("p");
        label.className = "onboarding-found";
        label.textContent = `检测到候选目录：${state.candidate}`;
        box.append(label);
      } else if (state.detecting) {
        const label = document.createElement("p");
        label.className = "onboarding-note";
        label.textContent = "正在检测常见位置…";
        box.append(label);
      }
    }

    for (const button of dialog.querySelectorAll("[data-step]")) {
      button.addEventListener("click", () => act(button.dataset.step));
    }
  }

  async function act(action) {
    if (action === "next") {
      if (state.candidate && !state.mounted) {
        try {
          await api("/api/skills/yixue-zonghe/library", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: state.candidate }),
          });
          state.mounted = true;
        } catch (error) {
          announce(error.message || "资料目录登记失败。", true);
        }
      }
      steps.next();
      render();
      return;
    }
    if (action === "detect") {
      state.detecting = true;
      render();
      try {
        const payload = await api("/api/onboarding");
        state.candidate = pickCandidate(payload?.library_candidates);
      } catch (error) {
        state.candidate = "";
      } finally {
        state.detecting = false;
      }
      render();
      return;
    }
    if (action === "browse") {
      const path = window.prompt("输入 yixue-zonghe 技能目录的完整路径（含 SKILL.md）：", "");
      if (!path) return;
      state.candidate = path.trim();
      render();
      return;
    }
    // finish / skip-mount 都等价于完成引导
    try {
      await api("/api/onboarding/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch (error) {
      /* 完成标记写不进去也不该把用户关在引导里 */
    }
    close();
    onDone();
  }

  function close() {
    if (typeof dialog.close === "function" && dialog.open) dialog.close();
    else dialog.removeAttribute("open");
  }

  dialog.addEventListener("cancel", (event) => {
    // Esc = 跳过：老方案要求不阻塞读书，所以关掉就算完成
    event.preventDefault();
    act("finish");
  });

  return {
    element: dialog,
    /** 返回是否弹了引导（false = 老用户或读取失败，静默跳过）。 */
    async maybeShow() {
      let payload = null;
      try {
        payload = await api("/api/onboarding");
      } catch (error) {
        return false;
      }
      if (!payload || payload.done) return false;
      state.books = Number(payload.books) || 0;
      state.candidates = Array.isArray(payload.library_candidates) ? payload.library_candidates : [];
      state.candidate = pickCandidate(state.candidates); // 命中就直接预选，少点一次
      render();
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
      return true;
    },
    close,
    get step() {
      return steps.index;
    },
  };
}
