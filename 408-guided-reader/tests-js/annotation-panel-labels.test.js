// annotation-ui.js 中的批注展示判定：AI 摘录与整段 AI 解答必须能分辨
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  annotationBadgeLabel,
  annotationBodyText,
  annotationKind,
  isExcerptNote,
} from '../src/reader_service/static/annotation-display.js';

const excerpt = {
  kind: 'AI_NOTE',
  source_kind: 'AI_SAVED',
  quote: '总线事务',
  body: '> 什么是总线事务？\n\n总线事务由地址、数据与控制信号组成。',
  payload: {
    question: '什么是总线事务？',
    answer: '总线事务由地址、数据与控制信号组成。',
    excerpt: '总线事务由地址、数据与控制信号组成。',
    quote: '总线事务',
    page_index: 3,
  },
  verification_state: null,
};

const explanation = {
  kind: 'AI_NOTE',
  source_kind: 'AI_SAVED',
  quote: '总线事务',
  body: '> 什么是总线事务？\n\n完整解释正文',
  payload: { question: '什么是总线事务？', answer: '完整解释正文', quote: '总线事务', page_index: 3 },
  verification_state: 'PASS',
};

test('带 payload.excerpt 的 AI 笔记识别为摘录', () => {
  assert.equal(isExcerptNote(excerpt), true);
  assert.equal(isExcerptNote(explanation), false);
  assert.equal(isExcerptNote({ kind: 'HIGHLIGHT', body: 'x' }), false);
  assert.equal(isExcerptNote(null), false);
});

test('摘录徽标显示「摘录」，整段解释仍是「AI 解答」', () => {
  assert.equal(annotationBadgeLabel(excerpt), '摘录');
  assert.equal(annotationBadgeLabel(explanation), 'AI 解答');
  assert.equal(annotationBadgeLabel({ kind: 'HIGHLIGHT' }), '高亮');
  assert.equal(annotationBadgeLabel({ kind: 'TEXTBOX' }), '文本框');
});

test('摘录正文只显示选中片段，不显示整段解释或提问', () => {
  assert.equal(annotationBodyText(excerpt), '总线事务由地址、数据与控制信号组成。');
  assert.notEqual(annotationBodyText(excerpt), annotationBodyText(explanation));
  assert.equal(annotationBodyText(explanation), '什么是总线事务？');
});

test('普通批注正文仍取 body 或原文引用', () => {
  assert.equal(annotationBodyText({ kind: 'HIGHLIGHT', body: '我的笔记', quote: '原文' }), '我的笔记');
  assert.equal(annotationBodyText({ kind: 'HIGHLIGHT', body: null, quote: '原文' }), '原文');
  assert.equal(annotationBodyText({ kind: 'HIGHLIGHT' }), '');
});

test('老数据没有 payload 时不影响 kind 判定', () => {
  assert.equal(annotationKind({ source_kind: 'AI_SAVED' }), 'AI_NOTE');
  assert.equal(isExcerptNote({ source_kind: 'AI_SAVED' }), false);
  assert.equal(annotationBadgeLabel({ source_kind: 'AI_SAVED' }), 'AI 解答');
});
