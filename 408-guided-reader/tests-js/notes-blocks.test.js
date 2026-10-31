// notes-blocks.js 纯逻辑测试：空行分段、围栏代码块保护、空块修剪、往返稳定
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  continuesOnEnter,
  insideFence,
  joinBlocks,
  parseBlocks,
} from '../src/reader_service/static/notes-blocks.js';

test('普通段落按空行分段', () => {
  assert.deepEqual(parseBlocks('# 标题\n\n第一段\n\n第二段'), ['# 标题', '第一段', '第二段']);
});

test('围栏代码块内的空行不切断', () => {
  const text = '前一段\n\n```python\na = 1\n\nb = 2\n```\n\n后一段';
  assert.deepEqual(parseBlocks(text), ['前一段', '```python\na = 1\n\nb = 2\n```', '后一段']);
});

test('未闭合的围栏代码块保持到结尾', () => {
  assert.deepEqual(parseBlocks('```js\nconst x = 1;\n'), ['```js\nconst x = 1;\n']);
});

test('~ 波浪围栏同样支持', () => {
  assert.deepEqual(parseBlocks('~~~\n文本\n\n继续\n~~~\n\n尾段'), ['~~~\n文本\n\n继续\n~~~', '尾段']);
});

test('首尾空行与连续空行被修剪', () => {
  assert.deepEqual(parseBlocks('\n\n\n内容\n\n\n\n'), ['内容']);
});

test('纯空白正文返回单个空块', () => {
  assert.deepEqual(parseBlocks(''), ['']);
  assert.deepEqual(parseBlocks('   \n\n  '), ['']);
});

test('joinBlocks 跳过空块', () => {
  assert.equal(joinBlocks(['a', '', 'b']), 'a\n\nb');
  assert.equal(joinBlocks(['', '   ']), '');
  assert.equal(joinBlocks([]), '');
});

test('parse ∘ join 往返稳定', () => {
  const text = '# 笔记\n\n> 引用\n\n```js\nx = 1\n\ny = 2\n```\n\n尾';
  assert.deepEqual(parseBlocks(joinBlocks(parseBlocks(text))), parseBlocks(text));
  assert.equal(joinBlocks(parseBlocks(text)), text.trim());
});

test('CRLF 换行归一化', () => {
  assert.deepEqual(parseBlocks('a\r\n\rb\r\n'), ['a', 'b']);
});

test('列表整块保留', () => {
  const list = '- 一\n- 二\n  - 嵌套';
  assert.deepEqual(parseBlocks(`标题\n\n${list}\n\n尾`), ['标题', list, '尾']);
});

test('insideFence：光标前有未闭合围栏返回 true', () => {
  assert.equal(insideFence('```js\nconst x = 1;'), true);
  assert.equal(insideFence('```js\nx = 1\n```\n后文'), false);
  assert.equal(insideFence('~~~\na\n\nb'), true);
  assert.equal(insideFence('普通段落'), false);
  assert.equal(insideFence(''), false);
});

test('continuesOnEnter：列表/引用/表格行继续换行', () => {
  assert.equal(continuesOnEnter('- 项目'), true);
  assert.equal(continuesOnEnter('* 项目'), true);
  assert.equal(continuesOnEnter('+ 项目'), true);
  assert.equal(continuesOnEnter('1. 有序'), true);
  assert.equal(continuesOnEnter('2) 有序'), true);
  assert.equal(continuesOnEnter('  - 嵌套'), true);
  assert.equal(continuesOnEnter('- [ ] 任务'), true);
  assert.equal(continuesOnEnter('> 引用行'), true);
  assert.equal(continuesOnEnter('| 表格 | 行 |'), true);
  // 空列表项 / 空引用行：回车应提交退出（与 Typora 一致）
  assert.equal(continuesOnEnter('- '), false);
  assert.equal(continuesOnEnter('>'), false);
  // 普通文本 / 标题：回车提交渲染
  assert.equal(continuesOnEnter('# 标题'), false);
  assert.equal(continuesOnEnter('正文一段'), false);
  assert.equal(continuesOnEnter(''), false);
});
