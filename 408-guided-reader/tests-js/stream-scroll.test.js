import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const app = readFileSync(new URL('../src/reader_service/static/app.js', import.meta.url), 'utf8');
const assistantSource = app.slice(app.indexOf('function appendAssistantDelta('), app.indexOf('function showAssistantStreamFailure('));
const assistantRun = app.slice(app.indexOf('async function runAssistantStream('), app.indexOf('async function advanceAssistantPending('));

function fixture() {
  const frames = new Map();
  let frameId = 0;
  const counts = {reads: 0, writes: 0};
  const text = () => ({content: '', hidden: true, append(node) { this.content += node.data; }});
  const answer = text(), reasoning = text();
  const block = {querySelector: () => reasoning};
  const row = {isConnected: true, reasoning: null,
    querySelector(selector) { return selector.includes('master-stream-answer') ? answer : this.reasoning; },
    prepend(node) { this.reasoning = node; },
  };
  const height = () => 100 + answer.content.length + reasoning.content.length;
  let scrollTop = 0;
  const history = {clientHeight: 100,
    get scrollHeight() { counts.reads++; return height(); },
    get scrollTop() { return scrollTop; },
    set scrollTop(value) { counts.writes++; scrollTop = Math.max(0, Math.min(value, height() - this.clientHeight)); },
    querySelector: () => row,
  };
  const controller = new AbortController();
  const state = {generation: 1, readerSessionId: 'session', assistantStreamControllers: new Map()};
  const view = {article: row, answer, content: '', generation: 1, readerSessionId: 'session', signal: controller.signal};
  const pending = {textContent: '', classList: {add() {}}};
  const controls = {history, question: {}, status: {classList: {add() {}, remove() {}}}};
  const context = vm.createContext({state, elements: {'assistant-turns': history},
    generation: 1, streamView: {answer: '', reasoning: '', generation: 1, signal: controller.signal}, activeStream: controller,
    document: {createTextNode: data => ({data})}, AbortController,
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); },
    el: id => controls[id], reasoningBlock: () => block,
    renderStreamView() { throw Error('The current stream row must be retained'); },
    syncComposer() {}, base: () => '/fake', recentReasoning: new Map(),
    clearAssistantStreamTransient() {}, showAssistantPending: () => pending,
    createAssistantStreamView: () => view, launchToken: 'fake-no-network',
  });
  vm.runInContext(assistantSource + assistantRun, context);
  return {context, state, view, row, answer, reasoning, controller, history, counts, frames,
    nextFrame() { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback()); },
  };
}

test('Assistant appends every delta immediately and follows it once per frame', () => {
  const f = fixture();
  for (let i = 0; i < 200; i++) f.context.appendAssistantDelta(f.view, '提示');
  assert.equal(f.answer.content, '提示'.repeat(200));
  assert.equal(f.view.content, f.answer.content);
  assert.equal(f.frames.size, 1);
  assert.equal(f.counts.reads, 0);
  f.nextFrame();
  assert.equal(f.counts.reads, 1);
  assert.equal(f.counts.writes, 1);
  assert.equal(f.history.scrollTop + f.history.clientHeight, f.history.scrollHeight);
  f.context.appendAssistantDelta(f.view, '下一帧');
  assert.equal(f.frames.size, 1);
  const reads = f.counts.reads;
  f.nextFrame();
  assert.equal(f.counts.reads, reads + 1);
});

test('Assistant pending scroll cannot affect a cancelled, departed, or replaced reading view', () => {
  for (const leave of [f => f.controller.abort(), f => { f.row.isConnected = false; },
    f => { f.state.generation++; }, f => { f.state.readerSessionId = 'other-session'; }]) {
    const f = fixture();
    f.context.appendAssistantDelta(f.view, '已经收到的部分');
    leave(f);
    f.nextFrame();
    assert.equal(f.answer.content, '已经收到的部分');
    assert.deepEqual(f.counts, {reads: 0, writes: 0});
  }
});

test('Assistant completion waits for the final visible text without buffering deltas', {timeout: 2000}, async () => {
  const f = fixture();
  f.context.streamAssistantResponse = async (_path, _options, receive) => {
    receive({type: 'delta', content: '完整回答'.repeat(100)});
    receive({type: 'complete', assistant: {roots: []}});
  };
  const result = f.context.runAssistantStream({path: '/fake', body: {}, question: '问题', streamKey: 'draft'});
  await new Promise(setImmediate);
  assert.equal(f.answer.content, '完整回答'.repeat(100));
  f.nextFrame();
  await new Promise(setImmediate);
  f.nextFrame();
  assert.equal((await result).type, 'complete');
  assert.equal(f.state.assistantStreamControllers.size, 0);
  assert.equal(f.history.scrollTop + f.history.clientHeight, f.history.scrollHeight);
});

