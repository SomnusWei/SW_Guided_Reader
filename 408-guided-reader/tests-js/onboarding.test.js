// 首启引导的纯逻辑：候选路径挑选与三步步骤机。
// DOM 交互由 tests-e2e/theme-smoke.mjs 的引导段覆盖。
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createOnboardingSteps,
  pickCandidate,
} from '../src/reader_service/static/onboarding.js';

test('pickCandidate 只接受非空且不以斜杠结尾的字符串路径', () => {
  assert.equal(pickCandidate(["C:\\a\\yixue-zonghe"]), "C:\\a\\yixue-zonghe");
  assert.equal(pickCandidate(["", null, "C:\\b"]), "C:\\b", "跳过空值取第一个合法项");
  assert.equal(pickCandidate(["C:\\trailing\\"]), "", "以斜杠结尾的不是有效技能目录");
  assert.equal(pickCandidate([]), "");
  assert.equal(pickCandidate(undefined), "");
  assert.equal(pickCandidate(42), "");
});

test('步骤机：欢迎 → 资料库 → 完成，前进不可回退', () => {
  const steps = createOnboardingSteps();
  assert.equal(steps.index, 0);
  assert.equal(steps.done, false);
  assert.equal(steps.next(), 1);
  assert.equal(steps.next(), 2);
  assert.equal(steps.done, true);
  assert.equal(steps.next(), 2, "最后一步再 next 不越界");
});

test('步骤机：finish 直接到完成步', () => {
  const steps = createOnboardingSteps();
  steps.finish();
  assert.equal(steps.index, 2);
  assert.equal(steps.done, true);
});
