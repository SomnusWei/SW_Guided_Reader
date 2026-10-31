// Markdown export download. The service returns an attachment; the browser never
// navigates away, so an open Reader keeps its PDF session and reading position.
export function filenameFromDisposition(header, fallback) {
  if (!header) return fallback;
  const extended = /filename\*\s*=\s*([^']*)''([^;]+)/i.exec(header);
  if (extended) {
    try {
      return decodeURIComponent(extended[2].trim().replace(/^"|"$/g, ""));
    } catch (_error) {
      return fallback;
    }
  }
  const plain = /filename\s*=\s*("([^"]+)"|([^;]+))/i.exec(header);
  const value = plain?.[2] || plain?.[3];
  return value ? value.trim() : fallback;
}

export function exportFilename(scope, date = new Date()) {
  const stamp = `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, "0")}${String(date.getDate()).padStart(2, "0")}`;
  const label = {
    assistant_conversation: "选段问答",
    master_topic: "知识点对话",
    annotations: "批注汇总",
  }[scope] || "导出";
  return `${label}_${stamp}.md`;
}

export function markdownExportUrl(revisionId, scope, id = null) {
  const query = new URLSearchParams({revision: revisionId, scope, download: "1"});
  if (id) query.set("id", id);
  return `/api/export/markdown?${query}`;
}

// 桌面外壳（Tauri/WebView2）不会接管 <a download> 的 blob 下载——点击后既无文件
// 也无报错。检测到 Tauri 时改走服务端 save=1：由本地服务直接落盘。dialog=1 会让
// 服务端先弹系统「另存为」对话框，用户可自选保存位置（下载文件夹可能被移走）。
function isDesktopShell() {
  return typeof window !== "undefined" && Boolean(window.__TAURI_INTERNALS__ || window.__TAURI__);
}

export async function downloadMarkdown({url, token, scope, announce}) {
  const saveToDisk = isDesktopShell();
  if (saveToDisk) {
    url = `${url}${url.includes("?") ? "&" : "?"}save=1&dialog=1`;
  }
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: {"X-Reader-Token": token || "", "Accept": "text/markdown, application/json"},
  });
  if (!response.ok) {
    let message = `导出失败（${response.status}）`;
    try {
      const payload = await response.json();
      message = payload?.error || message;
    } catch (_error) { /* keep the status message */ }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  if (saveToDisk) {
    const payload = await response.json().catch(() => null);
    if (payload?.cancelled) {
      announce?.("已取消导出");
      return null;
    }
    const filename = payload?.filename || exportFilename(scope);
    const where = payload?.path ? `（保存到：${payload.path}）` : "";
    announce?.(`已导出 ${filename}${where}`);
    return filename;
  }
  const blob = await response.blob();
  const filename = filenameFromDisposition(
    response.headers.get("Content-Disposition"), exportFilename(scope),
  );
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(href), 4000);
  announce?.(`已导出 ${filename}`);
  return filename;
}
