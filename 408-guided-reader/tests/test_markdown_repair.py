# markdown_repair：问 AI 摘录（所选内容存为批注）截断片段的结构补齐
import pytest

from reader_service.markdown_repair import repair_markdown_fragment


def test_plain_text_untouched():
    text = "这是普通的一段话，没有特殊语法。"
    assert repair_markdown_fragment(text) == text


def test_complete_table_untouched():
    text = "| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |"
    assert repair_markdown_fragment(text) == text


def test_table_rows_without_header_get_blank_header_and_separator():
    text = "| 1 | 2 |\n| 3 | 4 |"
    repaired = repair_markdown_fragment(text)
    lines = repaired.split("\n")
    assert lines[0] == "|  |  |" or lines[0].count("|") == 3
    assert lines[1].replace(" ", "").startswith("|---")
    assert lines[2] == text.split("\n")[0]
    assert len(lines) == 4


def test_table_single_column_lookalike_untouched():
    # 一行里带竖线的普通句子不是表格，不应被包上表头
    text = "价格是 100 | 200 之间"
    assert repair_markdown_fragment(text) == text


def test_selection_ending_at_closing_fence_prepends_opening():
    text = "x = 1\ny = 2\n```"
    repaired = repair_markdown_fragment(text)
    assert repaired.startswith("```")
    assert repaired.count("```") == 2


def test_selection_starting_at_opening_fence_appends_closing():
    text = "```python\nprint(1)"
    repaired = repair_markdown_fragment(text)
    assert repaired.endswith("```")
    assert repaired.count("```") == 2


def test_fence_content_does_not_participate_in_inline_pairing():
    # 代码块里的 ** 和反引号是字面内容，不应触发行内补齐
    text = "```js\nconst a = `x**y`;\n```"
    assert repair_markdown_fragment(text) == text


def test_unclosed_bold_is_closed():
    text = "这一句有 **加粗强调"
    assert repair_markdown_fragment(text).endswith("**")
    assert repair_markdown_fragment(text).count("**") % 2 == 0


def test_unclosed_inline_code_is_closed():
    text = "变量 `x 的含义"
    repaired = repair_markdown_fragment(text)
    assert repaired.endswith("`")
    assert repaired.count("`") % 2 == 0


def test_unclosed_math_delimiter_is_closed():
    text = "公式：$$E=mc^2"
    repaired = repair_markdown_fragment(text)
    assert repaired.endswith("$$")
    assert repaired.count("$$") % 2 == 0


def test_complete_inline_marks_untouched():
    text = "这是 **加粗** 与 `代码` 与 $$a+b$$ 的混合。"
    assert repair_markdown_fragment(text) == text


def test_empty_input_returned_as_is():
    assert repair_markdown_fragment("") == ""
    assert repair_markdown_fragment("   ") == "   "
