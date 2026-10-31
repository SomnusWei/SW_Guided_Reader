// 问AI默认技能与设置默认勾选保持一致（真机反馈 2026-10-31）
import assert from "node:assert/strict";
import test from "node:test";

const store = await import("../src/reader_service/static/skills-ui.js");

function withStorage(run) {
  const backing = new Map();
  globalThis.localStorage = {
    getItem: (key) => (backing.has(key) ? backing.get(key) : null),
    setItem: (key, value) => backing.set(key, String(value)),
    removeItem: (key) => backing.delete(key),
  };
  try {
    return run();
  } finally {
    delete globalThis.localStorage;
  }
}

const SKILLS = [
  { name: "assistant-explanation", display_name: "教材讲解", enabled: true, is_default: true },
  { name: "yixue-zonghe", display_name: "医学综合技能", enabled: true, seeded: true },
  { name: "user-own", display_name: "用户自装", enabled: true },
];

test("normalizeSkill：builtin 只认内置技能名，seeded 独立标记", () => {
  const skills = store.normalizeSkill({ skills: SKILLS });
  const builtin = skills.find((skill) => skill.name === "assistant-explanation");
  const seeded = skills.find((skill) => skill.name === "yixue-zonghe");
  const own = skills.find((skill) => skill.name === "user-own");
  assert.equal(builtin.builtin, true);
  assert.equal(seeded.builtin, false);
  assert.equal(seeded.seeded, true);
  assert.equal(own.seeded, false);
});

test("默认技能：无记忆时选中首个启用的随包技能", () => {
  withStorage(() => {
    const skills = store.createSkillStore({ api: async () => ({ skills: SKILLS }), announce: null });
    skills.skills.push(...store.normalizeSkill({ skills: SKILLS }));
    assert.equal(skills.selected("book-1"), "yixue-zonghe");
  });
});

test("默认技能：随包技能被停用后回落内置助手", () => {
  withStorage(() => {
    const skills = store.createSkillStore({ api: async () => ({}), announce: null });
    const payload = SKILLS.map((skill) => (
      skill.name === "yixue-zonghe" ? { ...skill, enabled: false } : skill));
    skills.skills.push(...store.normalizeSkill({ skills: payload }));
    assert.equal(skills.selected("book-1"), "");
  });
});

test("记忆优先：选过一次后仍用记忆的技能", () => {
  withStorage(() => {
    const skills = store.createSkillStore({ api: async () => ({}), announce: null });
    skills.skills.push(...store.normalizeSkill({ skills: SKILLS }));
    skills.remember("book-1", "user-own");
    assert.equal(skills.selected("book-1"), "user-own");
  });
});

test("记忆的技能被删除后回落随包默认", () => {
  withStorage(() => {
    const skills = store.createSkillStore({ api: async () => ({}), announce: null });
    skills.skills.push(...store.normalizeSkill({ skills: SKILLS }));
    skills.remember("book-1", "gone-skill");
    assert.equal(skills.selected("book-1"), "yixue-zonghe");
  });
});
