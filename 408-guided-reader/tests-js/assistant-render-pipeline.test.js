// assistant-render.js 纯逻辑测试：markdown 预处理（引用块还原、HTML 转义、数学/代码保护）
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

const source = readFileSync(
  new URL('../src/reader_service/static/assistant-render.js', import.meta.url),
  'utf8',
);
const body = source
  .replace(/^import .*$/gm, '')
  .replaceAll('export function', 'function')
  .replaceAll('export const', 'const');

const context = vm.createContext({
  window: {},
  marked: { Renderer: class {}, setOptions() {}, parse: () => '' },
  createDOMPurify: () => ({ sanitize: (x) => x }),
  katex: {},
  crypto: webcrypto,
});
vm.runInContext(body, context);
const prepare = context.prepareMarkdownAndMath;
assert.equal(typeof prepare, 'function', 'prepareMarkdownAndMath 应可从模块中提取');

test('行首引用标记还原为 >，marked 可解析引用块', () => {
  const { markdown } = prepare('# 标题\n\n> 引用：数学是科学的皇后');
  assert.ok(markdown.includes('\n> 引用：数学是科学的皇后'), markdown);
  assert.ok(!/^&gt;/m.test(markdown), '行首不应残留 &gt;');
});

test('嵌套引用同样还原', () => {
  const { markdown } = prepare('> > 深层引用');
  assert.ok(markdown.includes('> > 深层引用'), markdown);
});

test('行内 < > 仍保持转义（安全不变）', () => {
  const { markdown } = prepare('比较 a > b 与 a < b，以及 <script>alert(1)</script>');
  assert.ok(markdown.includes('a &gt; b'), markdown);
  assert.ok(markdown.includes('a &lt; b'), markdown);
  assert.ok(markdown.includes('&lt;script&gt;'), markdown);
});

test('代码块内的 > 不受影响（原样保留）', () => {
  const { markdown } = prepare('```\n> quoted in code\n```');
  assert.ok(markdown.includes('> quoted in code'), markdown);
});

test('数学公式仍被提取为占位符', () => {
  const { markdown, math } = prepare('勾股定理 $a^2+b^2=c^2$ 与 $$\\int x dx$$');
  assert.equal(math.length, 2);
  assert.equal(math[0].tex, 'a^2+b^2=c^2');
  assert.equal(math[1].display, true);
  assert.ok(!markdown.includes('$a^2'), '正文不应残留原始公式');
});
