from __future__ import annotations

from io import BytesIO

from conftest import make_pdf
from pypdf import PdfWriter
from pypdf.generic import NameObject, TextStringObject

from reader_service.foundation import PageLabelRepository, PageLabelService
from reader_service.outline import BookmarkImporter, OutlineRepository, OutlineService


def bookmarked_pdf_bytes() -> bytes:
    writer = PdfWriter()
    for _ in range(6):
        writer.add_blank_page(width=612, height=792)
    chapter = writer.add_outline_item("第一章 计算机系统", 0)
    section = writer.add_outline_item("1.1 存储层次", 2, parent=chapter)
    writer.add_outline_item("1.1.1 高速缓存", 3, parent=section)
    writer.add_outline_item("第二章 指令系统", 4)
    # GBK 字节被按 Latin-1 误读后的乱码标题："第三章 输入输出"
    writer.add_outline_item("µÚÈýÕÂ ÊäÈëÊä³ö", 5)
    bad = writer.add_outline_item("坏书签", 0)
    bad.get_object()[NameObject("/A")][NameObject("/D")] = TextStringObject(
        "不存在的命名目标"
    )
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def outline_service(service) -> OutlineService:
    return OutlineService(
        service,
        OutlineRepository(service.database),
        PageLabelService(service, PageLabelRepository(service.database)),
    )


def intake_revision(service, pdf: bytes) -> str:
    imported = service.intake(BytesIO(pdf), content_length=len(pdf), filename="book.pdf")
    return imported["book"]["active_revision"]["id"]


# ---------------------------------------------------------------- importer


def test_has_bookmarks_and_empty_outline(tmp_path):
    plain = tmp_path / "plain.pdf"
    plain.write_bytes(make_pdf(((612, 792),) * 3))
    importer = BookmarkImporter()
    assert importer.has_bookmarks(plain) is False
    assert importer.import_bookmarks(plain) == []

    bookmarked = tmp_path / "bookmarked.pdf"
    bookmarked.write_bytes(bookmarked_pdf_bytes())
    assert importer.has_bookmarks(bookmarked) is True


def test_import_bookmarks_parses_levels_pages_and_titles(tmp_path):
    pdf = tmp_path / "bookmarked.pdf"
    pdf.write_bytes(bookmarked_pdf_bytes())
    nodes = BookmarkImporter().import_bookmarks(pdf)

    assert [node["level"] for node in nodes] == [0, 1, 2, 0, 0, 0]
    assert [node["page_index"] for node in nodes] == [0, 2, 3, 4, 5, None]
    assert nodes[0]["title"] == "第一章 计算机系统"
    assert nodes[0]["path"] == (0,)
    assert nodes[1]["path"] == (0, 0)
    assert nodes[2]["path"] == (0, 0, 0)
    assert nodes[2]["level"] == 2
    assert all(node["page_index"] is None or node["page_index"] >= 0 for node in nodes)


def test_mojibake_title_is_repaired_and_flagged(tmp_path):
    pdf = tmp_path / "bookmarked.pdf"
    pdf.write_bytes(bookmarked_pdf_bytes())
    nodes = BookmarkImporter().import_bookmarks(pdf)

    repaired = nodes[4]
    assert repaired["title"] == "第三章 输入输出"
    assert repaired["needs_review"] is True
    # 正常中文标题不误报
    assert nodes[0]["needs_review"] is False


def test_invalid_destination_is_flagged_without_aborting_import(tmp_path):
    pdf = tmp_path / "bookmarked.pdf"
    pdf.write_bytes(bookmarked_pdf_bytes())
    nodes = BookmarkImporter().import_bookmarks(pdf)

    broken = nodes[-1]
    assert broken["title"] == "坏书签"
    assert broken["page_index"] is None
    assert broken["needs_review"] is True
    # 其余书签不受影响
    assert len(nodes) == 6


def test_unresolvable_bookmark_is_recorded_as_skipped(tmp_path):
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=612, height=792)
    writer.add_outline_item("正常书签", 0)
    empty = writer.add_outline_item("占位", 1)
    empty.get_object()[NameObject("/Title")] = TextStringObject("   ")
    output = BytesIO()
    writer.write(output)
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(output.getvalue())

    importer = BookmarkImporter()
    nodes = importer.import_bookmarks(pdf)
    assert [node["title"] for node in nodes] == ["正常书签"]
    assert len(importer.skipped) == 1
    assert importer.skipped[0]["path"] == [1]


# ------------------------------------------------- service + repository


def test_import_pdf_bookmarks_writes_sourced_tree_and_is_idempotent(service):
    revision_id = intake_revision(service, bookmarked_pdf_bytes())
    outline = outline_service(service)

    first = outline.import_pdf_bookmarks(revision_id)
    assert first["imported"] == 6
    assert first["max_depth"] == 2
    assert first["needs_review"] == 2  # 乱码修复 + 无效目标
    assert first["skipped"] == 0

    by_title = {node["title"]: node for node in first["nodes"]}
    assert by_title["1.1.1 高速缓存"]["start_page"] == 3
    assert by_title["1.1.1 高速缓存"]["parent_id"] == by_title["1.1 存储层次"]["outline_node_id"]
    assert all(node["source"] == "PDF_BOOKMARK" for node in first["nodes"])
    assert by_title["坏书签"]["start_page"] is None
    assert by_title["坏书签"]["resolution_state"] == "UNRESOLVED"

    second = outline.import_pdf_bookmarks(revision_id)
    assert first["nodes"] == second["nodes"]
    with service.database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM outline_nodes WHERE book_source_revision_id = ?",
            (revision_id,),
        ).fetchone()[0]
    assert count == 6


def test_list_published_exposes_source_and_click_targets(service):
    revision_id = intake_revision(service, bookmarked_pdf_bytes())
    outline = outline_service(service)
    outline.import_pdf_bookmarks(revision_id)

    published = outline.list_published(revision_id)
    assert published["bookmark_count"] == 6
    targets = {
        node["title"]: (node["start_page"], node["start_y"])
        for node in published["nodes"]
    }
    assert targets["第一章 计算机系统"][0] == 0
    assert targets["第二章 指令系统"][0] == 4
    # 页内 Y 偏移缺省为 None：前端按页首跳转
    assert targets["第一章 计算机系统"][1] is None


def test_bootstrap_after_bookmark_import_does_not_report_identity_conflict(service):
    revision_id = intake_revision(service, bookmarked_pdf_bytes())
    outline = outline_service(service)
    outline.import_pdf_bookmarks(revision_id)

    result = outline.bootstrap(revision_id)
    assert result["identity_conflict"] is False
    assert result["evidence_source"] == "BOOKMARK"
    stored = {node["title"]: node for node in result["nodes"]}
    assert stored["1.1.1 高速缓存"]["parent_id"] == stored["1.1 存储层次"]["outline_node_id"]


def test_import_on_bookmark_free_revision_returns_empty_result(service, tmp_path, monkeypatch):
    plain = tmp_path / "plain.pdf"
    plain.write_bytes(make_pdf(((612, 792),) * 3))
    revision_id = intake_revision(service, bookmarked_pdf_bytes())
    outline = outline_service(service)
    assert outline.import_pdf_bookmarks(revision_id)["imported"] == 6

    # 同一 revision 的 PDF 换成无书签来源后再导入：旧书签数据被清空，返回空结果
    monkeypatch.setattr(outline.library, "pdf_path", lambda _revision_id: plain)
    result = outline.import_pdf_bookmarks(revision_id)
    assert result == {
        "imported": 0, "max_depth": 0, "needs_review": 0, "skipped": 0, "nodes": []
    }
    published = outline.list_published(revision_id)
    assert published["nodes"] == []
    assert published["bookmark_count"] == 0


def test_write_bookmarks_keeps_foreign_bootstrap_record(service):
    revision_id = intake_revision(service, bookmarked_pdf_bytes())
    outline = outline_service(service)
    with service.database.connect() as connection:
        connection.execute(
            """
            INSERT INTO outline_bootstrap_records(
                book_source_revision_id, parser_version, evidence_source,
                evidence_digest, structure_digest, last_conflict_digest, committed_at
            ) VALUES (?, 'ocr-evidence-v9', 'TOC', 'digest-a', 'digest-b', NULL, '2026-01-01T00:00:00+00:00')
            """,
            (revision_id,),
        )

    outline.import_pdf_bookmarks(revision_id)

    with service.database.connect() as connection:
        record = dict(
            connection.execute(
                "SELECT * FROM outline_bootstrap_records WHERE book_source_revision_id = ?",
                (revision_id,),
            ).fetchone()
        )
    assert record["evidence_source"] == "TOC"
    assert record["evidence_digest"] == "digest-a"
    nodes = outline.list_published(revision_id)["nodes"]
    assert nodes and all(node["source"] == "PDF_BOOKMARK" for node in nodes)
