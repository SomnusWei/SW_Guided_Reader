import runpy
import shutil
import sqlite3
from io import BytesIO
from pathlib import Path

import pytest
from conftest import make_pdf

from reader_service.library.database import Database

repair = runpy.run_path(str(Path(__file__).parents[1] / 'tools' / 'repair_book_evidence.py'))


def test_offline_repair_refuses_durable_dependency_and_active_jobs():
    connection = sqlite3.connect(':memory:')
    connection.executescript('''
        CREATE TABLE jobs(status TEXT);
        CREATE TABLE outline_nodes(outline_node_id TEXT PRIMARY KEY, book_source_revision_id TEXT);
        CREATE TABLE annotations(book_source_revision_id TEXT);
        INSERT INTO annotations VALUES ('owned');
    ''')
    with pytest.raises(RuntimeError, match='durable dependent'):
        repair['assert_unowned'](connection, 'owned')
    repair['assert_unowned'](connection, 'unowned')
    connection.execute("INSERT INTO jobs VALUES ('RUNNING')")
    with pytest.raises(RuntimeError, match='active jobs'):
        repair['assert_unowned'](connection, 'unowned')


def test_offline_repair_detects_indirect_outline_foreign_key():
    connection = sqlite3.connect(':memory:')
    connection.executescript('''
        CREATE TABLE jobs(status TEXT);
        CREATE TABLE outline_nodes(outline_node_id TEXT PRIMARY KEY, book_source_revision_id TEXT);
        CREATE TABLE dependent(node TEXT REFERENCES outline_nodes(outline_node_id));
        INSERT INTO outline_nodes VALUES ('chapter', 'owned');
        INSERT INTO dependent VALUES ('chapter');
    ''')
    with pytest.raises(RuntimeError, match='Outline dependent'):
        repair['assert_unowned'](connection, 'owned')


def test_staged_copy_database_keeps_its_files_deletable_while_open(tmp_path):
    """The repair tool deletes its staged working copy, so it must opt out of the
    process-lifetime WAL anchor: Windows refuses to delete an open sqlite file."""

    staged = tmp_path / 'stage'
    staged.mkdir()
    database = Database(staged / 'state.sqlite3', retain_wal=False)
    database.initialize()
    with database.connect() as connection:
        connection.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()

    shutil.rmtree(staged)  # raises PermissionError (WinError 32) if a handle leaked
    assert not staged.exists()


def test_failed_staged_repair_does_not_modify_live_database(service, monkeypatch):
    pdf = make_pdf()
    revision = service.intake(BytesIO(pdf), content_length=len(pdf), filename='empty.pdf')['book']['active_revision']
    with service.database.connect() as connection:
        before = list(connection.iterdump())
    monkeypatch.setattr('sys.argv', ['repair', '--data-dir', str(service.paths.root),
                        '--revision', revision['id'], '--expected-sha256', revision['blob_sha256'], '--apply'])
    with pytest.raises(RuntimeError, match='No validated replacement tree'):
        repair['main']()
    with service.database.connect() as connection:
        assert list(connection.iterdump()) == before
    assert not (service.paths.root / 'before-evidence-repair.sqlite3').exists()
