// 插件选区动作的纯逻辑：文本清洗（与后端同口径）、排序、上下文与 URL 构造。
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildPluginContext,
  cleanSelectionText,
  orderPluginActions,
  pluginUrl,
} from '../src/reader_service/static/plugin-actions.js';

test('cleanSelectionText 去首尾空白并折叠内部换行', () => {
  assert.equal(cleanSelectionText('  本虚标实 \n 是证候 '), '本虚标实 是证候');
});

test('cleanSelectionText 默认不删标点', () => {
  assert.equal(cleanSelectionText('「本虚标实」'), '「本虚标实」');
});

test('cleanSelectionText 开启 stripPunctuation 只削首尾', () => {
  assert.equal(cleanSelectionText('「本虚标实」，', { stripPunctuation: true }), '本虚标实');
  assert.equal(cleanSelectionText('《中风》', { stripPunctuation: true }), '中风');
});

test('cleanSelectionText 保留词中间的顿号与逗号', () => {
  assert.equal(cleanSelectionText('太阳病，脉浮', { stripPunctuation: true }), '太阳病，脉浮');
  assert.equal(cleanSelectionText('桂枝、芍药', { stripPunctuation: true }), '桂枝、芍药');
});

test('cleanSelectionText 截断到 maxChars', () => {
  assert.equal(cleanSelectionText('一二三四五', { maxChars: 3 }), '一二三');
});

test('cleanSelectionText 处理空值', () => {
  assert.equal(cleanSelectionText(''), '');
  assert.equal(cleanSelectionText(null), '');
  assert.equal(cleanSelectionText(undefined), '');
});

test('orderPluginActions 去掉停用动作并按 order 排序', () => {
  const ordered = orderPluginActions([
    { plugin: 'pinyin', id: 'annotate', label: '拼音', order: 45, enabled: true },
    { plugin: 'dict', id: 'lookup', label: '词典', order: 40 },
    { plugin: 'off', id: 'gone', label: '停用', order: 1, enabled: false },
  ]);
  assert.deepEqual(ordered.map((item) => item.key), ['dict:lookup', 'pinyin:annotate']);
  assert.equal(ordered[0].maxChars, 60);
  assert.equal(ordered[0].stripPunctuation, false);
});

test('orderPluginActions 同序时按插件名与动作名稳定排序', () => {
  const ordered = orderPluginActions([
    { plugin: 'b', id: 'x', label: 'B', order: 40 },
    { plugin: 'a', id: 'y', label: 'A', order: 40 },
    { plugin: 'a', id: 'x', label: 'Ax', order: 40 },
  ]);
  assert.deepEqual(ordered.map((item) => item.key), ['a:x', 'a:y', 'b:x']);
});

test('orderPluginActions 容忍空输入', () => {
  assert.deepEqual(orderPluginActions(null), []);
  assert.deepEqual(orderPluginActions([]), []);
});

test('buildPluginContext 输出 §3.2 的结构', () => {
  const context = buildPluginContext({
    action: 'lookup',
    text: '本虚标实',
    selection: { page: 12, bookId: 'b-1', bookTitle: '中医内科学' },
    theme: 'dark',
  });
  assert.deepEqual(context, {
    type: 'lt-plugin-context',
    action: 'lookup',
    text: '本虚标实',
    selection: { page: 12, bookId: 'b-1', bookTitle: '中医内科学' },
    theme: 'dark',
    locale: 'zh-CN',
  });
});

test('buildPluginContext 未知主题回退为 light', () => {
  assert.equal(buildPluginContext({ theme: 'neon' }).theme, 'light');
});

test('pluginUrl 把 action/text/theme 编码进 query', () => {
  const url = pluginUrl('/plugins/dict/index.html', {
    action: 'lookup',
    text: '本虚标实',
    theme: 'dark',
  });
  assert.ok(url.startsWith('/plugins/dict/index.html?'));
  const params = new URL(url, 'http://127.0.0.1').searchParams;
  assert.equal(params.get('action'), 'lookup');
  assert.equal(params.get('text'), '本虚标实');
  assert.equal(params.get('theme'), 'dark');
});

test('pluginUrl 对已有 query 的地址用 & 连接', () => {
  const url = pluginUrl('/plugins/dict/index.html?v=2', { text: 'x' });
  assert.ok(url.includes('.html?v=2&'));
});

test('pluginUrl 对特殊字符做转义', () => {
  const url = pluginUrl('/plugins/dict/index.html', { text: 'a&b=c d' });
  const params = new URL(url, 'http://127.0.0.1').searchParams;
  assert.equal(params.get('text'), 'a&b=c d');
});
