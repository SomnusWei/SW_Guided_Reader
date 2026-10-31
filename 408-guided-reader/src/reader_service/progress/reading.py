"""Section-end reading observations. Never a mastery or learning-event writer.

2.0 起这里只负责"读到哪一节末尾"，与知识点/掌握度彻底解耦：
`section_learning_states` 不再参与计算（该表随 KP 功能一起停写，仅作为历史数据保留）。
"""
from math import isfinite

from datetime import datetime, UTC

SCHEMA = """
CREATE TABLE section_reading_progress (
    outline_node_id TEXT PRIMARY KEY REFERENCES outline_nodes(outline_node_id) ON DELETE CASCADE,
    book_source_revision_id TEXT NOT NULL REFERENCES book_source_revisions(id) ON DELETE CASCADE,
    reading_reached_end_at TEXT NOT NULL
);
"""


def targets(connection, revision_id):
    nodes = [dict(r) for r in connection.execute(
        "SELECT * FROM outline_nodes WHERE book_source_revision_id=?", (revision_id,))]
    result = []
    by_id = {n['outline_node_id']: n for n in nodes}
    for section in nodes:
        if section['kind'] != 'SECTION' or section['resolution_state'] != 'RESOLVED':
            continue
        if any(section[k] is None for k in ('start_page', 'start_y', 'end_page', 'end_y')):
            continue
        start = (section['start_page'], section['start_y'])
        end = (section['end_page'], section['end_y'])
        for child in nodes:
            if child['kind'] not in ('EXERCISES', 'ANSWERS') or child['resolution_state'] != 'RESOLVED':
                continue
            if child['start_page'] is None or child['start_y'] is None:
                continue
            parent = child
            seen = set()
            while parent and parent['outline_node_id'] not in seen:
                seen.add(parent['outline_node_id'])
                if parent['outline_node_id'] == section['outline_node_id']:
                    position = (child['start_page'], child['start_y'])
                    if start < position < end:
                        end = position
                    break
                parent = by_id.get(parent['parent_id'])
        result.append({'outline_node_id': section['outline_node_id'], 'title': section['title'],
                       'pdf_page_index': end[0], 'y': end[1]})
    return result


def snapshot(database, revision_id):
    with database.connect() as c:
        rows = {r['outline_node_id']: r['reading_reached_end_at'] for r in c.execute(
            'SELECT * FROM section_reading_progress WHERE book_source_revision_id=?', (revision_id,))}
        for_reading = targets(c, revision_id)
        for item in for_reading:
            item['reading_reached_end_at'] = rows.get(item['outline_node_id'])
        return {'sections': for_reading}


def observe(database, revision_id, page, top, bottom):
    if isinstance(page, bool) or not isinstance(page, int) or not all(isfinite(v) for v in (top, bottom)) or not 0 <= top <= bottom <= 1:
        raise ValueError('可见阅读范围无效。')
    with database.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        revision = c.execute("SELECT page_count FROM book_source_revisions WHERE id=? AND status='ACTIVE'", (revision_id,)).fetchone()
        if revision is None:
            raise LookupError('教材版本不可用。')
        if not 0 <= page < revision['page_count']:
            raise ValueError('PDF 页码无效。')
        # Match only a boundary actually shown on this page, never all earlier sections.
        for item in targets(c, revision_id):
            if item['pdf_page_index'] == page and top <= item['y'] <= bottom:
                c.execute('''INSERT INTO section_reading_progress VALUES (?, ?, ?)
                             ON CONFLICT(outline_node_id) DO NOTHING''',
                          (item['outline_node_id'], revision_id, datetime.now(UTC).isoformat()))
    return snapshot(database, revision_id)
