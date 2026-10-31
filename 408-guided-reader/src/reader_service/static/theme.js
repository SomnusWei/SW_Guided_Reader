/**
 * 主题：偏好（light/dark/system）→ 实际主题（light/dark）的唯一实现。
 *
 * 三个约束决定了这里的形状：
 *  1. **首屏不能闪**：index.html / notes.html 的 <head> 里有一段内联脚本，在样式表
 *     之前就把 data-theme 打到 <html> 上；那段脚本只认 data-theme-pref 与 localStorage，
 *     所以控制器每次换肤都要把偏好镜像回 localStorage，否则下次首屏会猜错。
 *  2. **服务端是权威**：偏好存在 settings.json 里（Reader 与笔记小窗共享），
 *     localStorage 只是给首屏脚本用的缓存。加载时以 /api/settings/appearance 为准。
 *  3. **跟随系统要真的跟随**：system 时监听 matchMedia 变化即时重绘；
 *     用户显式选了浅/深色就不再响应系统变化（否则会在系统切换时莫名跳色）。
 */

export const THEME_PREFS = ["light", "dark", "system"];
export const THEME_STORAGE_KEY = "lt-theme";
export const THEME_MEDIA_QUERY = "(prefers-color-scheme: dark)";

export function normalizeThemePref(value) {
  const pref = typeof value === "string" ? value.trim().toLowerCase() : "";
  return THEME_PREFS.includes(pref) ? pref : "system";
}

export function resolveTheme(pref, prefersDark) {
  const normalized = normalizeThemePref(pref);
  if (normalized === "light") return "light";
  if (normalized === "dark") return "dark";
  return prefersDark ? "dark" : "light";
}

function safeStorage() {
  try {
    const probe = window.localStorage;
    probe.getItem(THEME_STORAGE_KEY);
    return probe;
  } catch (error) {
    return null; // 隐私模式 / 禁用存储：退化成"每次按 system 判断"，不影响使用
  }
}

function safeMedia() {
  try {
    return typeof window.matchMedia === "function" ? window.matchMedia(THEME_MEDIA_QUERY) : null;
  } catch (error) {
    return null;
  }
}

function prefersDark(media) {
  return Boolean(media && media.matches);
}

/**
 * @param {object} options
 * @param {HTMLElement} [options.root]     打属性的元素，默认 <html>
 * @param {Function}    [options.request]  (path, options) => Promise，用于读写设置
 * @param {Storage}     [options.storage]  默认 localStorage
 * @param {MediaQueryList} [options.media] 默认 matchMedia("(prefers-color-scheme: dark)")
 * @param {Function}    [options.onChange] (theme, pref) => void，用于把新主题推给插件 iframe
 */
export function createThemeController(options = {}) {
  const root = options.root || document.documentElement;
  const request = options.request;
  const storage = options.storage === undefined ? safeStorage() : options.storage;
  const media = options.media === undefined ? safeMedia() : options.media;
  const onChange = options.onChange;

  let pref = normalizeThemePref(
    (root && root.getAttribute && root.getAttribute("data-theme-pref")) ||
      (storage && storage.getItem(THEME_STORAGE_KEY))
  );
  let theme = resolveTheme(pref, prefersDark(media));

  function apply() {
    theme = resolveTheme(pref, prefersDark(media));
    if (root && root.setAttribute) {
      root.setAttribute("data-theme", theme);
      root.setAttribute("data-theme-pref", pref);
    }
    if (storage) {
      try {
        storage.setItem(THEME_STORAGE_KEY, pref);
      } catch (error) {
        /* 配额满或被禁用：首屏会退回 system，不影响当前这一帧 */
      }
    }
    if (typeof onChange === "function") onChange(theme, pref);
    return theme;
  }

  function watch() {
    if (!media || typeof media.addEventListener !== "function") return;
    media.addEventListener("change", () => {
      if (pref !== "system") return; // 显式选择浅/深色时不跟随系统
      apply();
    });
  }

  async function load() {
    if (typeof request === "function") {
      try {
        const payload = await request("/api/settings/appearance");
        if (payload && typeof payload.theme === "string") {
          pref = normalizeThemePref(payload.theme);
        }
      } catch (error) {
        /* 读不到设置就沿用注入值/镜像值，设置接口不该阻断首屏 */
      }
    }
    apply();
    watch();
    return { theme, pref };
  }

  async function set(next, { persist = true } = {}) {
    pref = normalizeThemePref(next);
    const applied = apply();
    if (persist && typeof request === "function") {
      try {
        await request("/api/settings/appearance", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ theme: pref }),
        });
      } catch (error) {
        return { theme: applied, pref, saved: false, error };
      }
    }
    return { theme: applied, pref, saved: true };
  }

  return {
    load,
    set,
    apply,
    get pref() {
      return pref;
    },
    get theme() {
      return theme;
    },
  };
}
