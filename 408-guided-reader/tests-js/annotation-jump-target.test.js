// annotationJumpTarget：全书批注面板点击跳转的定位计算
import test from 'node:test';
import assert from 'node:assert/strict';
import { annotationJumpTarget } from '../src/reader_service/static/annotation-display.js';

test('优先使用 rect 的上沿', () => {
  const target = annotationJumpTarget({ pdf_page_index: 4, rect: [0.1, 0.32, 0.9, 0.4] });
  assert.deepEqual(target, { page: 4, y: 0.32 });
});

test('没有 rect 时取 quads 所有点的最小 y', () => {
  const annotation = {
    pdf_page_index: 2,
    quads: [
      [[0.1, 0.55], [0.4, 0.55], [0.4, 0.6], [0.1, 0.6]],
      [[0.1, 0.2], [0.4, 0.2], [0.4, 0.25], [0.1, 0.25]],
    ],
  };
  assert.deepEqual(annotationJumpTarget(annotation), { page: 2, y: 0.2 });
});

test('rect 与 quads 都没有时落回页首', () => {
  assert.deepEqual(annotationJumpTarget({ pdf_page_index: 7 }), { page: 7, y: 0 });
  assert.deepEqual(annotationJumpTarget({}), { page: 0, y: 0 });
  assert.deepEqual(annotationJumpTarget(null), { page: 0, y: 0 });
});

test('老数据用 page_index 兜底页码', () => {
  assert.deepEqual(annotationJumpTarget({ page_index: 3, quads: [[[0.1, 0.42], [0.5, 0.42]]] }),
    { page: 3, y: 0.42 });
});

test('quads 为空数组或畸形数据时不返回 NaN', () => {
  assert.deepEqual(annotationJumpTarget({ pdf_page_index: 1, quads: [] }), { page: 1, y: 0 });
  assert.deepEqual(annotationJumpTarget({ pdf_page_index: 1, quads: [null, [null]] }), { page: 1, y: 0 });
});
