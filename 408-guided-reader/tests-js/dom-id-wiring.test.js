// app.js 的 elements 表与 index.html 的 id 必须一一对应：
// 漏一个 id 会在启动时 addEventListener(null) 直接崩掉整页。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const staticDir = path.join(import.meta.dirname, '..', 'src', 'reader_service', 'static');

async function load() {
  const [app, html] = await Promise.all([
    readFile(path.join(staticDir, 'app.js'), 'utf8'),
    readFile(path.join(staticDir, 'index.html'), 'utf8'),
  ]);
  const block = app.match(/const elements = Object\.fromEntries\(\s*\[([\s\S]*?)\]\s*\.map/);
  assert.ok(block, 'app.js 里找不到 elements 表');
  const ids = [...block[1].matchAll(/"([a-z0-9-]+)"/g)].map((match) => match[1]);
  return { ids, html };
}

test('elements 表里的每个 id 都存在于 index.html', async () => {
  const { ids, html } = await load();
  const missing = ids.filter((id) => !html.includes(`id="${id}"`));
  assert.deepEqual(missing, [], `index.html 缺少这些 id：${missing.join(', ')}`);
});

test('回答选区工具条包含「再问一层」与「所选内容存为笔记」', async () => {
  const { ids, html } = await load();
  assert.ok(ids.includes('assistant-ask-deeper'));
  assert.ok(ids.includes('assistant-save-excerpt'));
  const toolbar = html.match(/<div id="assistant-answer-actions"[\s\S]*?<\/div>\s*<\/div>/);
  assert.ok(toolbar, '找不到回答选区工具条');
  const deeper = toolbar[0].indexOf('id="assistant-ask-deeper"');
  const excerpt = toolbar[0].indexOf('id="assistant-save-excerpt"');
  assert.ok(deeper >= 0 && excerpt > deeper, '「所选内容存为笔记」应排在「再问一层」右侧');
});
