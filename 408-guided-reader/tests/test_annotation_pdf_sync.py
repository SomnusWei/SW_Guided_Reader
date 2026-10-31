from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Popup, Text
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    RectangleObject,
    TextStringObject,
)

from reader_service.annotation.pdf_sync import (
    AnnotationSyncConfig,
    PdfAnnotationSync,
    READER_MARKER_PREFIX,
)

from conftest import make_pdf
from test_annotations import prepared_services


def make_text_pdf(path: Path, pages: int = 3, *, rotation=None) -> Path:
    """Build a small PDF with a real text layer so comments have something to sit on."""
    writer = PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 24 Tf 72 700 Td (Page {index} bus transaction) Tj ET".encode())
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        page[NameObject("/Rotate")] = NumberObject(rotation or 0)
    with open(path, "wb") as stream:
        writer.write(stream)
    writer.close()
    return path


def five_annotations():
    return [
        {
            "id": "ann-highlight",
            "kind": "HIGHLIGHT",
            "pdf_page_index": 0,
            "quads": [[[0.1, 0.1], [0.5, 0.1], [0.5, 0.15], [0.1, 0.15]]],
            "highlight_style": "YELLOW",
            "body": None,
        },
        {
            "id": "ann-underline",
            "kind": "UNDERLINE",
            "pdf_page_index": 0,
            "quads": [[[0.1, 0.2], [0.5, 0.2], [0.5, 0.25], [0.1, 0.25]]],
            "highlight_style": "GREEN",
            "body": None,
        },
        {
            "id": "ann-textbox",
            "kind": "TEXTBOX",
            "pdf_page_index": 1,
            "rect": [0.1, 0.3, 0.5, 0.4],
            "highlight_style": "BLUE",
            "body": "文本框里的笔记",
        },
        {
            "id": "ann-region",
            "kind": "REGION",
            "pdf_page_index": 1,
            "rect": [0.2, 0.5, 0.8, 0.9],
            "highlight_style": "RED",
            "body": None,
        },
        {
            "id": "ann-ai",
            "kind": "AI_NOTE",
            "pdf_page_index": 2,
            "quads": [[[0.3, 0.6], [0.7, 0.6], [0.7, 0.65], [0.3, 0.65]]],
            "highlight_style": "NONE",
            "payload": {
                "question": "什么是总线事务？",
                "answer": "总线事务由地址、数据与控制信号组成。",
                "quote": "总线事务",
                "page_index": 2,
            },
            "body": "> 什么是总线事务？\n\n总线事务由地址、数据与控制信号组成。",
        },
    ]


def reader_annots(pdf_path: Path):
    reader = PdfReader(str(pdf_path))
    marked = {}
    for page_index, page in enumerate(reader.pages):
        for reference in page.get("/Annots") or []:
            annot = reference.get_object()
            marker = annot.get("/NM")
            if marker is not None and str(marker).startswith(READER_MARKER_PREFIX):
                marked[str(marker)[len(READER_MARKER_PREFIX):]] = annot
    return marked


def test_write_all_five_kinds_and_read_back(tmp_path):
    pdf_path = make_text_pdf(tmp_path / "book.pdf")
    sync = PdfAnnotationSync()
    report = sync.write_annotations(pdf_path, five_annotations())
    assert report.written == 5
    assert report.removed == 0
    assert report.skipped == []

    stored = reader_annots(pdf_path)
    assert set(stored) == {"ann-highlight", "ann-underline", "ann-textbox", "ann-region", "ann-ai"}
    assert str(stored["ann-highlight"]["/Subtype"]) == "/Highlight"
    assert str(stored["ann-underline"]["/Subtype"]) == "/Underline"
    assert str(stored["ann-textbox"]["/Subtype"]) == "/FreeText"
    assert str(stored["ann-region"]["/Subtype"]) == "/Square"
    assert str(stored["ann-ai"]["/Subtype"]) == "/Text"
    assert "问：什么是总线事务？" in str(stored["ann-ai"]["/Contents"])
    assert "答：总线事务由地址、数据与控制信号组成。" in str(stored["ann-ai"]["/Contents"])
    # 便签必须自带外观流：轻量阅读器对无 /AP 的便签只画空框，AI 徽标会“消失”。
    ai_annot = stored["ann-ai"]
    appearance = ai_annot["/AP"]["/N"].get_object()
    assert str(appearance["/Subtype"]) == "/Form"
    bbox = [float(value) for value in appearance["/BBox"]]
    assert bbox == pytest.approx([0.0, 0.0, 18.0, 18.0], abs=1e-6)
    assert b"(AI) Tj" in appearance.get_data()
    assert str(ai_annot["/Name"]) == "/Comment"
    assert int(ai_annot["/F"]) & 4  # Print 标志：打印与导出时保留徽标
    # The region box is a translucent filled square, not an opaque block.
    assert float(stored["ann-region"]["/CA"]) == pytest.approx(0.3)

    restored = {value["id"]: value for value in sync.read_reader_annotations(pdf_path)}
    assert restored["ann-highlight"]["pdf_page_index"] == 0
    assert restored["ann-highlight"]["kind"] == "HIGHLIGHT"
    assert restored["ann-highlight"]["highlight_style"] == "YELLOW"
    assert restored["ann-underline"]["kind"] == "UNDERLINE"
    assert restored["ann-textbox"]["kind"] == "TEXTBOX"
    assert restored["ann-textbox"]["pdf_page_index"] == 1
    assert restored["ann-textbox"]["body"] == "文本框里的笔记"
    assert restored["ann-region"]["kind"] == "REGION"
    assert restored["ann-region"]["highlight_style"] == "RED"
    assert restored["ann-region"]["rect"] == pytest.approx([0.2, 0.5, 0.8, 0.9], abs=1e-6)
    assert restored["ann-ai"]["kind"] == "AI_NOTE"
    quad = restored["ann-highlight"]["quads"][0]
    assert quad[0] == pytest.approx([0.1, 0.1], abs=1e-6)
    assert quad[2] == pytest.approx([0.5, 0.15], abs=1e-6)


def test_ai_excerpt_note_writes_only_selected_text(tmp_path):
    """摘录型 AI 笔记：PDF 注释里只出现选中的片段，不再带问答结构。"""
    pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=1)
    sync = PdfAnnotationSync()
    excerpt = {
        "id": "ann-ai-excerpt",
        "kind": "AI_NOTE",
        "pdf_page_index": 0,
        "quads": [[[0.2, 0.6], [0.6, 0.6], [0.6, 0.65], [0.2, 0.65]]],
        "highlight_style": "NONE",
        "payload": {
            "question": "什么是总线事务？",
            "answer": "总线事务由地址、数据与控制信号组成。",
            "excerpt": "总线事务由地址、数据与控制信号组成。",
            "quote": "总线事务",
            "page_index": 0,
        },
        "body": "> 什么是总线事务？\n\n总线事务由地址、数据与控制信号组成。",
    }
    report = sync.write_annotations(pdf_path, [excerpt])
    assert report.written == 1
    assert report.skipped == []

    stored = reader_annots(pdf_path)
    contents = str(stored["ann-ai-excerpt"]["/Contents"])
    assert contents == "摘录：总线事务由地址、数据与控制信号组成。"
    assert "问：" not in contents
    assert "答：" not in contents


def test_write_note_sticky_and_read_back(tmp_path):
    pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=1)
    sync = PdfAnnotationSync()
    note = {
        "id": "ann-note",
        "kind": "NOTE",
        "pdf_page_index": 0,
        "rect": [0.2, 0.3, 0.45, 0.42],
        "highlight_style": "YELLOW",
        "body": "这一段要背",
    }
    report = sync.write_annotations(pdf_path, [note])
    assert report.written == 1
    assert report.skipped == []

    stored = reader_annots(pdf_path)
    annot = stored["ann-note"]
    assert str(annot["/Subtype"]) == "/Text"
    assert str(annot["/Name"]) == "/Note"  # 与 AI 徽标的 /Comment 区分
    assert int(annot["/F"]) & 4  # Print 标志
    assert str(annot["/Contents"]) == "这一段要背"
    appearance = annot["/AP"]["/N"].get_object()
    assert str(appearance["/Subtype"]) == "/Form"  # 有外观流，轻量阅读器也能显示图标

    restored = {value["id"]: value for value in sync.read_reader_annotations(pdf_path)}
    assert restored["ann-note"]["kind"] == "NOTE"
    assert restored["ann-note"]["body"] == "这一段要背"


def test_resync_reflects_updates_and_deletions_without_duplicates(tmp_path):
    pdf_path = make_text_pdf(tmp_path / "book.pdf")
    sync = PdfAnnotationSync()
    sync.write_annotations(pdf_path, five_annotations())

    annotations = five_annotations()
    annotations[0]["highlight_style"] = "PURPLE"
    annotations[2]["rect"] = [0.3, 0.4, 0.6, 0.5]
    annotations[2]["body"] = "移动后的文本框"
    annotations = [value for value in annotations if value["id"] != "ann-underline"]

    report = sync.write_annotations(pdf_path, annotations)
    assert report.written == 4
    assert report.removed == 5

    stored = reader_annots(pdf_path)
    assert set(stored) == {"ann-highlight", "ann-textbox", "ann-region", "ann-ai"}
    assert "ann-underline" not in stored
    # PURPLE = #9932cc = (0.6, 0.196, 0.8) in PDF color space.
    assert list(stored["ann-highlight"]["/C"]) == pytest.approx([0.6, 0.1961, 0.8], abs=0.01)
    restored = {value["id"]: value for value in sync.read_reader_annotations(pdf_path)}
    assert restored["ann-highlight"]["highlight_style"] == "PURPLE"
    assert restored["ann-textbox"]["rect"] == pytest.approx([0.3, 0.4, 0.6, 0.5], abs=1e-6)
    assert restored["ann-textbox"]["body"] == "移动后的文本框"


def test_export_annotated_copy_never_touches_the_source(tmp_path):
    pdf_path = make_text_pdf(tmp_path / "book.pdf")
    source_before = pdf_path.read_bytes()
    out_path = tmp_path / "annotated-copy.pdf"
    sync = PdfAnnotationSync()

    report = sync.export_annotated_copy(pdf_path, five_annotations(), out_path)
    assert report.written == 5
    assert pdf_path.read_bytes() == source_before
    assert len(reader_annots(out_path)) == 5
    assert reader_annots(pdf_path) == {}

    with pytest.raises(ValueError, match="must not overwrite"):
        sync.export_annotated_copy(pdf_path, five_annotations(), pdf_path)


def test_atomic_write_leaves_no_temporary_files_and_backups_rotate(tmp_path):
    pdf_path = make_text_pdf(tmp_path / "book.pdf")
    sync = PdfAnnotationSync()
    sync.write_annotations(pdf_path, five_annotations())
    assert [path.name for path in tmp_path.iterdir()] == ["book.pdf"]
    assert len(PdfReader(str(pdf_path)).pages) == 3

    backup_dir = tmp_path / "backups"
    for _ in range(4):
        sync.backup_pdf(pdf_path, backup_dir, keep=3)
    backups = sorted(backup_dir.glob("book-*.pdf.bak"))
    assert len(backups) == 3
    assert backups[-1].read_bytes() == pdf_path.read_bytes()


def test_external_annotations_are_never_removed(tmp_path):
    pdf_path = make_text_pdf(tmp_path / "book.pdf")
    writer = PdfWriter(clone_from=str(pdf_path))
    page = writer.pages[0]
    external = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Highlight"),
            NameObject("/Rect"): RectangleObject([10, 10, 40, 40]),
            NameObject("/NM"): TextStringObject("other-tool-mark-1"),
        }
    )
    writer.add_annotation(page_number=0, annotation=external)
    with open(pdf_path, "wb") as stream:
        writer.write(stream)
    writer.close()

    sync = PdfAnnotationSync()
    sync.write_annotations(pdf_path, five_annotations())
    reader = PdfReader(str(pdf_path))
    markers = []
    for page in reader.pages:
        for reference in page.get("/Annots") or []:
            markers.append(str(reference.get_object().get("/NM")))
    assert "other-tool-mark-1" in markers
    assert len([value for value in markers if value.startswith(READER_MARKER_PREFIX)]) == 5

    # A later full sync keeps the external mark and replaces only Reader marks.
    sync.write_annotations(pdf_path, five_annotations()[:1])
    reader = PdfReader(str(pdf_path))
    markers = []
    for page in reader.pages:
        for reference in page.get("/Annots") or []:
            markers.append(str(reference.get_object().get("/NM")))
    assert "other-tool-mark-1" in markers
    assert len([value for value in markers if value.startswith(READER_MARKER_PREFIX)]) == 1


def count_popups(pdf_path: Path) -> int:
    total = 0
    for page in PdfReader(str(pdf_path)).pages:
        for reference in page.get("/Annots") or []:
            if str(reference.get_object().get("/Subtype")) == "/Popup":
                total += 1
    return total


def test_popup_objects_do_not_accumulate_across_resyncs(tmp_path):
    """每轮全量重写都会重建便签，弹出窗若不被清除就会线性累积。"""
    pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=1)
    sync = PdfAnnotationSync()
    note = {
        "id": "ann-note",
        "kind": "NOTE",
        "pdf_page_index": 0,
        "rect": [0.1, 0.2, 0.3, 0.25],
        "body": "反复写回的便签",
        "highlight_style": "YELLOW",
    }

    for _ in range(3):
        sync.write_annotations(pdf_path, [note])
        assert count_popups(pdf_path) == 1
        assert len(reader_annots(pdf_path)) == 1

    # 删除批注后回写：父注释与它的弹出窗一起消失
    sync.write_annotations(pdf_path, [])
    assert reader_annots(pdf_path) == {}
    assert count_popups(pdf_path) == 0


def test_external_popup_survives_reader_resync(tmp_path):
    """别的工具写的便签与其弹出窗不归 Reader 管，全量重写不能碰。"""
    pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=1)
    writer = PdfWriter(clone_from=str(pdf_path))
    external = writer.add_annotation(
        page_number=0, annotation=Text(rect=(40, 40, 60, 60), text="外部便签")
    )
    writer.add_annotation(
        page_number=0, annotation=Popup(rect=(70, 70, 240, 220), parent=external)
    )
    with open(pdf_path, "wb") as stream:
        writer.write(stream)
    writer.close()

    sync = PdfAnnotationSync()
    sync.write_annotations(pdf_path, five_annotations())
    sync.write_annotations(pdf_path, [])

    reader = PdfReader(str(pdf_path))
    kept = [
        reference.get_object()
        for page in reader.pages
        for reference in (page.get("/Annots") or [])
    ]
    assert any(str(item.get("/Subtype")) == "/Text" for item in kept)
    assert sum(1 for item in kept if str(item.get("/Subtype")) == "/Popup") == 1
    assert reader_annots(pdf_path) == {}


@pytest.mark.parametrize("rotation", (90, 180, 270))
def test_rotated_pages_convert_coordinates_round_trip(tmp_path, rotation):
    pdf_path = make_text_pdf(tmp_path / "rotated.pdf", pages=1, rotation=rotation)
    sync = PdfAnnotationSync()
    annotation = {
        "id": "ann-rotated",
        "kind": "REGION",
        "pdf_page_index": 0,
        "rect": [0.2, 0.3, 0.7, 0.6],
        "highlight_style": "BLUE",
        "body": None,
    }
    report = sync.write_annotations(pdf_path, [annotation])
    assert report.written == 1
    assert report.skipped == []

    restored = sync.read_reader_annotations(pdf_path)
    assert len(restored) == 1
    assert restored[0]["rect"] == pytest.approx([0.2, 0.3, 0.7, 0.6], abs=1e-6)


def test_unsupported_rotation_is_reported_as_skipped(tmp_path):
    pdf_path = make_text_pdf(tmp_path / "odd.pdf", pages=1)
    writer = PdfWriter(clone_from=str(pdf_path))
    writer.pages[0][NameObject("/Rotate")] = NumberObject(45)
    with open(pdf_path, "wb") as stream:
        writer.write(stream)
    writer.close()

    sync = PdfAnnotationSync()
    report = sync.write_annotations(
        pdf_path,
        [
            {
                "id": "ann-odd",
                "kind": "REGION",
                "pdf_page_index": 0,
                "rect": [0.2, 0.3, 0.7, 0.6],
                "highlight_style": "BLUE",
                "body": None,
            }
        ],
    )
    assert report.skipped == [{"id": "ann-odd", "reason": "NOT_RENDERABLE"}]
    assert report.written == 0
    assert sync.read_reader_annotations(pdf_path) == []


def test_page_out_of_range_is_skipped_not_fatal(tmp_path):
    pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=1)
    sync = PdfAnnotationSync()
    report = sync.write_annotations(
        pdf_path,
        [
            {
                "id": "ann-far",
                "kind": "REGION",
                "pdf_page_index": 7,
                "rect": [0.1, 0.1, 0.2, 0.2],
                "highlight_style": "RED",
                "body": None,
            }
        ],
    )
    assert report.written == 0
    assert report.skipped == [{"id": "ann-far", "reason": "PAGE_OUT_OF_RANGE"}]


def test_sync_config_reads_environment_and_rejects_unknown_values():
    assert AnnotationSyncConfig.from_environment({}) == AnnotationSyncConfig(
        mode="manual", target="source"
    )
    assert AnnotationSyncConfig.from_environment(
        {"GUIDED_READER_ANNOTATION_PDF_SYNC": "AUTO", "GUIDED_READER_ANNOTATION_PDF_TARGET": "copy"}
    ) == AnnotationSyncConfig(mode="auto", target="copy")
    assert AnnotationSyncConfig.from_environment(
        {"GUIDED_READER_ANNOTATION_PDF_SYNC": "off"}
    ).mode == "off"
    with pytest.raises(ValueError, match="sync mode"):
        AnnotationSyncConfig.from_environment({"GUIDED_READER_ANNOTATION_PDF_SYNC": "sometimes"})
    with pytest.raises(ValueError, match="sync target"):
        AnnotationSyncConfig.from_environment({"GUIDED_READER_ANNOTATION_PDF_TARGET": "elsewhere"})


def test_sync_config_persists_to_data_dir_with_environment_precedence(tmp_path):
    assert AnnotationSyncConfig.load(tmp_path, {}) == AnnotationSyncConfig()
    AnnotationSyncConfig(mode="auto", target="copy").save(tmp_path)
    assert AnnotationSyncConfig.load(tmp_path, {}) == AnnotationSyncConfig(
        mode="auto", target="copy"
    )
    assert AnnotationSyncConfig.load(
        tmp_path, {"GUIDED_READER_ANNOTATION_PDF_SYNC": "off"}
    ) == AnnotationSyncConfig(mode="off", target="copy")
    assert "annotation_pdf_sync" in (tmp_path / "config.json").read_text(encoding="utf-8")


def test_database_annotations_round_trip_through_the_source_pdf(service):
    _, revision, annotations = prepared_services(service)
    selection = {
        "start": {"line_ordinal": 0, "boundary": 1},
        "end": {"line_ordinal": 1, "boundary": 2},
    }
    annotations.create(
        revision["id"], kind="HIGHLIGHT", page_index=0, highlight_style="GREEN", **selection
    )
    annotations.create(
        revision["id"], kind="UNDERLINE", page_index=0, highlight_style="BLUE", **selection
    )
    annotations.create(
        revision["id"], kind="TEXTBOX", page_index=0, rect=[0.1, 0.3, 0.5, 0.4], body="笔记"
    )
    annotations.create(
        revision["id"], kind="REGION", page_index=0, rect=[0.2, 0.5, 0.6, 0.8], highlight_style="RED"
    )

    pdf_path = service.pdf_path(revision["id"])
    sync = PdfAnnotationSync()
    report = sync.write_annotations(pdf_path, annotations.list_all(revision["id"]))
    assert report.written == 4
    assert report.skipped == []

    restored = sync.read_reader_annotations(pdf_path)
    assert sorted(value["kind"] for value in restored) == [
        "HIGHLIGHT", "REGION", "TEXTBOX", "UNDERLINE"
    ]
    assert all(value["pdf_page_index"] == 0 for value in restored)


def test_custom_hex_color_write_and_read_back(tmp_path):
    """自定义颜色：#RRGGBB 写回 PDF 的 /C，回读还原成 #RRGGBB。"""
    pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=1)
    sync = PdfAnnotationSync()
    highlight = {
        "id": "ann-custom",
        "kind": "HIGHLIGHT",
        "pdf_page_index": 0,
        "quads": [[[0.1, 0.1], [0.5, 0.1], [0.5, 0.15], [0.1, 0.15]]],
        "highlight_style": "#33CCFF",
        "body": None,
    }
    report = sync.write_annotations(pdf_path, [highlight])
    assert report.written == 1
    assert report.skipped == []

    stored = reader_annots(pdf_path)["ann-custom"]
    # #33CCFF = (0.2, 0.8, 1.0) in PDF color space.
    assert list(stored["/C"]) == pytest.approx([0.2, 0.8, 1.0], abs=0.01)

    restored = {value["id"]: value for value in sync.read_reader_annotations(pdf_path)}
    assert restored["ann-custom"]["highlight_style"] == "#33CCFF"


def test_note_sticky_writes_color_for_round_trip(tmp_path):
    """便签写回 /C 颜色，回读时能拿到（此前便签颜色丢失）。"""
    pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=1)
    sync = PdfAnnotationSync()
    note = {
        "id": "ann-note",
        "kind": "NOTE",
        "pdf_page_index": 0,
        "rect": [0.2, 0.3, 0.45, 0.42],
        "highlight_style": "#ff8800",
        "body": "这一段要背",
    }
    report = sync.write_annotations(pdf_path, [note])
    assert report.written == 1

    stored = reader_annots(pdf_path)["ann-note"]
    assert list(stored["/C"]) == pytest.approx([1.0, 0.5333, 0.0], abs=0.01)
    restored = {value["id"]: value for value in sync.read_reader_annotations(pdf_path)}
    assert restored["ann-note"]["highlight_style"] == "#FF8800"


def test_markdown_to_plain_text_strips_markup():
    """PDF 便签是纯文本：markdown 符号剥除后仍保留全部可读内容。"""
    from reader_service.annotation.pdf_sync import markdown_to_plain_text

    md = (
        "## 标题\n\n**加粗**文字与*斜体*\n\n"
        "| 通法 | 代表方药 |\n|------|---------|\n| 温通 | 瓜蒌薤白剂 |\n\n"
        "- 列表项一\n- 列表项二\n> 引用一行\n\n"
        "行内`代码`与[链接文字](http://example.com)\n"
    )
    out = markdown_to_plain_text(md)
    for forbidden in ("**", "##", "|", "```", "](", "`"):
        assert forbidden not in out, f"不应残留 {forbidden!r}：{out!r}"
    for expected in ("标题", "加粗文字与斜体", "通法", "代表方药", "温通", "瓜蒌薤白剂",
                     "· 列表项一", "引用一行", "行内代码与链接文字"):
        assert expected in out, f"应保留 {expected!r}：{out!r}"


def test_markdown_to_plain_text_tolerates_empty():
    from reader_service.annotation.pdf_sync import markdown_to_plain_text

    assert markdown_to_plain_text("") == ""
    assert markdown_to_plain_text(None) == ""


def test_markdown_to_plain_text_strips_markup():
    """PDF 便签是纯文本：markdown 符号剥除后仍保留全部可读内容。"""
    from reader_service.annotation.pdf_sync import markdown_to_plain_text

    md = (
        "## 标题\n\n**加粗**文字与*斜体*\n\n"
        "| 通法 | 代表方药 |\n|------|---------|\n| 温通 | 瓜蒌薤白剂 |\n\n"
        "- 列表项一\n- 列表项二\n> 引用一行\n\n"
        "行内`代码`与[链接文字](http://example.com)\n"
    )
    out = markdown_to_plain_text(md)
    for forbidden in ("**", "##", "|", "```", "](", "`"):
        assert forbidden not in out, f"不应残留 {forbidden!r}：{out!r}"
    for expected in ("标题", "加粗文字与斜体", "通法", "代表方药", "温通", "瓜蒌薤白剂",
                     "· 列表项一", "引用一行", "行内代码与链接文字"):
        assert expected in out, f"应保留 {expected!r}：{out!r}"


def test_markdown_to_plain_text_tolerates_empty():
    from reader_service.annotation.pdf_sync import markdown_to_plain_text

    assert markdown_to_plain_text("") == ""
    assert markdown_to_plain_text(None) == ""
