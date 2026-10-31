import test from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeContext, readContextFromQuery, contextSeed, contextLabel, sameContext, isEmbedded,
} from '../src/reader_service/static/notes-context.js';

test('normalize keeps a reader snapshot with revision and page', () => {
  assert.deepEqual(
    normalizeContext({ revisionId: 'rev-1', bookTitle: '中医基础理论', page: 12 }),
    { revisionId: 'rev-1', bookTitle: '中医基础理论', page: 12 },
  );
});

test('normalize accepts 0-based page zero instead of treating it as empty', () => {
  assert.deepEqual(
    normalizeContext({ revisionId: 'rev-1', page: 0 }),
    { revisionId: 'rev-1', bookTitle: '', page: 0 },
  );
});

test('normalize drops unusable pages and snapshots with nothing left', () => {
  for (const page of [-1, 1.5, 'abc', '', null, undefined, NaN]) {
    assert.equal(normalizeContext({ revisionId: 'rev-1', page }).page, null);
    // 页码无效且没有教材时整条上下文作废，避免笔记页拿到半截来源信息
    assert.equal(normalizeContext({ page }), null);
  }
  assert.equal(normalizeContext({}), null);
  assert.equal(normalizeContext(null), null);
  assert.equal(normalizeContext('rev-1'), null);
});

test('normalize trims titles and rejects non-string ids', () => {
  assert.deepEqual(
    normalizeContext({ revisionId: '  rev-2  ', bookTitle: '  针灸学  ', page: '7' }),
    { revisionId: 'rev-2', bookTitle: '针灸学', page: 7 },
  );
  assert.equal(normalizeContext({ revisionId: 42, page: 1 }).revisionId, null);
});

test('query parsing maps the reader URL parameters onto the same shape', () => {
  assert.deepEqual(
    readContextFromQuery('?embed=1&token=abc&book=rev-9&bookTitle=%E7%BB%8F%E7%BB%9C&page=3'),
    { revisionId: 'rev-9', bookTitle: '经络', page: 3 },
  );
  assert.equal(readContextFromQuery(''), null);
  assert.equal(readContextFromQuery('?embed=1'), null);
});

test('seed only carries the two note columns that live in the draft', () => {
  assert.deepEqual(
    contextSeed({ revisionId: 'rev-1', bookTitle: 'A', page: 4 }),
    { book_source_revision_id: 'rev-1', page_index: 4 },
  );
  assert.deepEqual(contextSeed(null), { book_source_revision_id: null, page_index: null });
});

test('label shows the 1-based page readers see', () => {
  assert.equal(contextLabel({ revisionId: 'r', bookTitle: '中药学', page: 0 }), '中药学 · 第 1 页');
  assert.equal(contextLabel({ revisionId: 'r', bookTitle: '', page: 9 }), '第 10 页');
  assert.equal(contextLabel({ revisionId: 'r', bookTitle: '中药学', page: null }), '中药学');
  assert.equal(contextLabel(null), '');
});

test('sameContext treats an unchanged reader position as no-op', () => {
  const a = { revisionId: 'r', bookTitle: 'A', page: 2 };
  assert.equal(sameContext(a, { ...a }), true);
  assert.equal(sameContext(a, { ...a, page: 3 }), false);
  assert.equal(sameContext(a, null), false);
  assert.equal(sameContext(null, null), true);
});

test('embedded mode is opt-in via embed=1 or embed=true', () => {
  assert.equal(isEmbedded('?embed=1'), true);
  assert.equal(isEmbedded('?embed=true'), true);
  assert.equal(isEmbedded('?embed=0'), false);
  assert.equal(isEmbedded(''), false);
  assert.equal(isEmbedded('?embed=1&token=x'), true);
});
