// 插件宿主接线护栏：选区菜单必须是动态的（来自注册表），不能再把
// 「拼音 / 词典」写死在 HTML 里；弹窗 iframe 与 postMessage 协议要在位。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const staticDir = path.join(import.meta.dirname, '..', 'src', 'reader_service', 'static');
const read = (name) => readFile(path.join(staticDir, name), 'utf8');

test('选区菜单里有插件动作容器，没有写死的拼音/词典按钮', async () => {
  const html = await read('index.html');
  assert.ok(html.includes('id="plugin-selection-actions"'), '缺少插件动作容器');
  assert.equal(html.includes('id="ask-pinyin"'), false, 'index.html 仍有写死的「拼音」按钮');
  assert.equal(html.includes('id="ask-dict"'), false, 'index.html 仍有写死的「词典」按钮');
});

test('插件弹窗 DOM 齐备', async () => {
  const html = await read('index.html');
  for (const id of ['plugin-dialog', 'plugin-frame', 'plugin-dialog-title', 'plugin-dialog-close']) {
    assert.ok(html.includes(`id="${id}"`), `index.html 缺少 #${id}`);
  }
});

test('app.js 通过 plugin-host 接入选区动作', async () => {
  const app = await read('app.js');
  assert.ok(app.includes('/plugin-host.js'), 'app.js 没有引入 plugin-host.js');
  assert.ok(app.includes('createPluginHost('), 'app.js 没有创建插件宿主');
  assert.ok(app.includes('pluginHost.refresh()'), 'app.js 没有拉取插件注册表');
  assert.equal(
    app.includes('sendSelectionToPlatformTool'),
    false,
    'app.js 仍在用跨窗口 postMessage 的旧联动（主窗口场景下恒报错）',
  );
});

test('plugin-host 用 postMessage 下发 lt-plugin-context 并复用同一个 dialog', async () => {
  const host = await read('plugin-host.js');
  assert.ok(host.includes('postMessage(context'), '没有下发插件上下文');
  assert.ok(host.includes('showModal'), '没有用 dialog 打开插件');
  assert.ok(host.includes('lt-plugin-event'), '没有监听插件反向事件');
});

test('插件动作容器不影响选区工具条布局', async () => {
  const css = await read('styles.css');
  assert.ok(css.includes('.plugin-actions { display: contents; }'));
  assert.ok(css.includes('.plugin-dialog'));
});
