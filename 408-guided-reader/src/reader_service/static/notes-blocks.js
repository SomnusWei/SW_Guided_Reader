// 笔记块编辑器的正文分段逻辑（纯函数，供单元测试）。
// 模型：正文 = 若干语块，块与块之间以空行分隔；围栏代码块内部的空行不切断。

function fenceMarker(line) {
  const match = line.match(/^\s*(`{3,}|~{3,})(.*)$/);
  if (!match) return null;
  return { char: match[1][0], len: match[1].length, rest: match[2].trim() };
}

/** 把正文切成语块数组；丢弃首尾空块，至少返回一个块 */
export function parseBlocks(text) {
  const raw = String(text ?? "").replace(/\r\n?/g, "\n");
  const blocks = [];
  let current = [];
  let fence = null; // 打开的围栏标记 {char, len}
  const flush = () => {
    blocks.push(current.join("\n"));
    current = [];
  };
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (fence) {
      current.push(line);
      const marker = fenceMarker(line);
      if (marker && marker.char === fence.char && marker.len >= fence.len && marker.rest === "") {
        fence = null;
      }
      continue;
    }
    const marker = fenceMarker(line);
    if (marker) {
      current.push(line);
      fence = marker;
      continue;
    }
    if (trimmed === "") {
      if (current.length) flush();
      continue;
    }
    current.push(line);
  }
  if (current.length) flush();

  const filled = blocks.filter((block) => block.trim() !== "");
  return filled.length ? filled : [""];
}

/** 语块合并回正文；跳过首尾空块，保证 parseBlocks(joinBlocks(x)) 稳定 */
export function joinBlocks(blocks) {
  const list = (blocks || []).filter((block) => typeof block === "string" && block.trim() !== "");
  if (!list.length) return "";
  return list.join("\n\n");
}

/** 光标前的文本里是否存在未闭合的围栏（决定 Enter 是否在代码块内换行） */
export function insideFence(text) {
  let fence = null;
  for (const line of String(text ?? "").split("\n")) {
    if (fence) {
      const marker = fenceMarker(line);
      if (marker && marker.char === fence.char && marker.len >= fence.len && marker.rest === "") {
        fence = null;
      }
      continue;
    }
    const marker = fenceMarker(line);
    if (marker) fence = marker;
  }
  return fence !== null;
}

const CONTINUE_RE = /^\s*(?:[-*+]\s+\S|\d{1,9}[.)]\s+\S|>\s*\S|\|)/;

/**
 * 当前行是否属于"回车继续写"的行级结构：列表项、引用行、表格行。
 * 这类行按 Enter 时在块内换行继续；空列表项 / 空引用行（如只打了 "- "）
 * 不算继续，按 Enter 提交整块并退出该结构（与 Typora 行为一致）。
 */
export function continuesOnEnter(line) {
  return CONTINUE_RE.test(String(line ?? ""));
}
