// excerptPreviewText：摘录按 markdown 入库后，面板列表预览要剥掉标记显示纯文本
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  annotationBodyText,
  excerptPreviewText,
} from '../src/reader_service/static/annotation-display.js';

test('剥掉标题与加粗标记，保留文字', () => {
  assert.equal(
    excerptPreviewText('## 通法与补法\n\n**补气**：八珍汤。'),
    '通法与补法\n补气：八珍汤。',
  );
});

test('表格行展开为单元格文字，分隔行整行剥掉', () => {
  const text = excerptPreviewText('| 证型 | 主症 |\n| --- | --- |\n| 气虚 | 乏力 |');
  assert.equal(text, '证型  主症\n气虚  乏力');
});

test('列表项转圆点，引用标记剥掉', () => {
  assert.equal(excerptPreviewText('- 补血：当归补血汤\n- 温阳：理中汤'), '· 补血：当归补血汤\n· 温阳：理中汤');
  assert.equal(excerptPreviewText('> 引用的一句话'), '引用的一句话');
});

test('围栏行剥掉但围栏内代码保留，行内代码剥反引号', () => {
  assert.equal(excerptPreviewText('```\nkeep() this\n```'), 'keep() this');
  assert.equal(excerptPreviewText('调用 `run_x()` 完成'), '调用 run_x() 完成');
});

test('斜体、删除线与数学定界符剥掉', () => {
  assert.equal(excerptPreviewText('*强调* 与 ~~删除~~ 还有 $a^2$'), '强调 与 删除 还有 a^2');
});

test('空值与纯文本原样返回', () => {
  assert.equal(excerptPreviewText(''), '');
  assert.equal(excerptPreviewText(null), '');
  assert.equal(excerptPreviewText('普通一句话'), '普通一句话');
});

test('annotationBodyText 对摘录做预览剥除，不再露出 markdown 标记', () => {
  const annotation = {
    kind: 'AI_NOTE',
    source_kind: 'AI_SAVED',
    payload: { excerpt: '**补气温阳**，方用理中汤。' },
  };
  const text = annotationBodyText(annotation);
  assert.equal(text, '补气温阳，方用理中汤。');
  assert.ok(!text.includes('**'));
});
