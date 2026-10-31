"""选取截断后的 Markdown 片段自动补齐。

问 AI 的「所选内容存为批注」允许用户在回答里只框选一段文字。选区可能从
表格中间、代码围栏中间或某个加粗词的内部开始/结束，逐段切片拼出来的
Markdown 因此缺失结构性语法（表格头、围栏闭合、成对的强调/行内代码/数学
定界符），在批注里渲染成纯文本或错乱版式。

这里只做保守的结构性修复：不增删词语、不改写内容，只补齐能让片段恢复
正确渲染所需的最少语法。
"""

from __future__ import annotations

import re

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
# GFM 表格分隔行：| --- | :---: | 这类
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{0,}:?\s*\|?\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _table_columns(row: str) -> int:
    """按竖线切出单元格数；容许首尾省略竖线的行。"""
    stripped = row.strip().strip("|")
    if not stripped:
        return 0
    return len(stripped.split("|"))


def _repair_table_blocks(lines: list[str]) -> None:
    """就地补齐缺少表头/分隔行的连续表格行块。"""
    index = 0
    while index < len(lines):
        if not _TABLE_ROW_RE.match(lines[index]):
            index += 1
            continue
        start = index
        while index < len(lines) and _TABLE_ROW_RE.match(lines[index]):
            index += 1
        end = index
        run = lines[start:end]
        if any(_TABLE_SEPARATOR_RE.match(line) for line in run):
            continue
        columns = max((_table_columns(line) for line in run), default=0)
        if columns < 2:
            # 单列伪表格（例如一行内多个竖线的普通句子）不动，避免误伤正文
            continue
        header = "| " + " | ".join([" "] * columns) + " |"
        separator = "|" + "---|" * columns
        lines[start:end] = [header, separator, *run]
        # 插入两行后原数据行整体后移；index 直接跳过本块，避免把同一块反复修复
        index = end + 2


def repair_markdown_fragment(text: str) -> str:
    """补齐截断片段缺失的 Markdown 结构语法。"""
    if not text or not text.strip():
        return text
    lines = text.split("\n")
    fence_lines = [i for i, line in enumerate(lines) if _FENCE_RE.match(line)]
    inside_fence = len(fence_lines) % 2 == 1

    def _inline_text() -> str:
        """围栏内的内容属于代码原样，不参与行内标记配对统计。"""
        keep = []
        open_fences = 0
        for line in lines:
            if _FENCE_RE.match(line):
                open_fences = 1 - open_fences
                continue
            if not open_fences:
                keep.append(line)
        return "\n".join(keep)

    inline = _inline_text()

    # 1) 代码围栏：奇数个围栏说明片段从块中间开始或结束。
    #    片段以围栏结尾 → 选区从块内开始、缺 Opening；
    #    片段以围栏开头 → 选区在块内结束、缺 Closing；
    #    围栏在中间（回答常见「如下例：```lang\n代码」被切中）→ 按 Opening
    #    处理在末尾补齐；反向情形极罕见，接受误判。
    if inside_fence:
        if lines and _FENCE_RE.match(lines[-1]):
            lines.insert(0, "```")
        else:
            lines.append("```")

    # 2) 表格块缺表头/分隔行 → 插入空表头与分隔行
    _repair_table_blocks(lines)

    repaired_inline = _inline_text()

    # 3) 数学定界符 $$：奇数个补一个（围栏内的已剔除）
    if repaired_inline.count("$$") % 2 == 1:
        lines.append("$$")

    # 4) 行内代码反引号与加粗 **：奇数个在末尾补齐
    repaired_inline = _inline_text()
    if repaired_inline.count("`") % 2 == 1:
        lines[-1] = lines[-1].rstrip() + "`" if lines else "`"
    repaired_inline = _inline_text()
    if repaired_inline.count("**") % 2 == 1:
        lines[-1] = lines[-1].rstrip() + "**" if lines else "**"

    return "\n".join(lines)


__all__ = ["repair_markdown_fragment"]
