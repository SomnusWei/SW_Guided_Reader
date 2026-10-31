// 2.0 删除知识点（KP）与行间教学后，前端不允许再出现它们的入口或请求。
// 这条测试的作用是防止后续改动把已删除的功能悄悄带回来。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const staticDir = path.join(import.meta.dirname, '..', 'src', 'reader_service', 'static');
const read = (name) => readFile(path.join(staticDir, name), 'utf8');

test('阅读器页面不再有章节学习地图面板', async () => {
  const html = await read('index.html');
  for (const id of ['knowledge-panel', 'knowledge-map', 'knowledge-prepare', 'knowledge-title']) {
    assert.equal(html.includes(`id="${id}"`), false, `index.html 仍包含 #${id}`);
  }
});

test('app.js 不再请求知识点/行间教学/master 接口', async () => {
  const app = await read('app.js');
  const forbidden = [
    '/knowledge-map',
    '/inline-teaching',
    '/learning/',
    '/api/learning/',
    'master-ui.js',
    'inline-ui.js',
  ];
  const hits = forbidden.filter((needle) => app.includes(needle));
  assert.deepEqual(hits, [], `app.js 仍引用已删除的接口或模块：${hits.join(', ')}`);
});

test('已删除的三个前端模块不再存在于 static 目录', async () => {
  const { access } = await import('node:fs/promises');
  for (const name of ['master-ui.js', 'inline-ui.js', 'inline-placement.js']) {
    await assert.rejects(access(path.join(staticDir, name)), `${name} 应已删除`);
  }
});
