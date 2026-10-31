// 主题控制器：偏好归一化、system 解析、镜像写回、服务端权威、只跟随不改写。
// 这些都是纯逻辑或可注入依赖的逻辑，所以不用起浏览器就能测。
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  THEME_STORAGE_KEY,
  createThemeController,
  normalizeThemePref,
  resolveTheme,
} from '../src/reader_service/static/theme.js';

function fakeRoot(attrs = {}) {
  return {
    attrs: {...attrs},
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    },
    setAttribute(name, value) {
      this.attrs[name] = String(value);
    },
  };
}

function fakeStorage(initial = {}) {
  const data = {...initial};
  return {
    data,
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null;
    },
    setItem(key, value) {
      data[key] = String(value);
    },
  };
}

function fakeMedia(matches) {
  const listeners = [];
  return {
    matches,
    listeners,
    addEventListener(type, handler) {
      if (type === "change") listeners.push(handler);
    },
    emit(next) {
      this.matches = next;
      for (const handler of listeners) handler();
    },
  };
}

test('normalizeThemePref 只认三档，其余回落到 system', () => {
  assert.equal(normalizeThemePref("dark"), "dark");
  assert.equal(normalizeThemePref(" LIGHT "), "light");
  assert.equal(normalizeThemePref("system"), "system");
  assert.equal(normalizeThemePref("sepia"), "system");
  assert.equal(normalizeThemePref(null), "system");
  assert.equal(normalizeThemePref(42), "system");
});

test('resolveTheme：显式选择不受系统影响，system 才跟系统', () => {
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("dark", false), "dark");
  assert.equal(resolveTheme("system", true), "dark");
  assert.equal(resolveTheme("system", false), "light");
});

test('没有服务端时沿用 html 上注入的偏好，并立刻打上 data-theme', async () => {
  const root = fakeRoot({"data-theme-pref": "dark"});
  const storage = fakeStorage();
  const controller = createThemeController({root, storage, media: fakeMedia(false), request: null});
  await controller.load();
  assert.equal(root.attrs["data-theme"], "dark");
  assert.equal(controller.theme, "dark");
});

test('prefers-color-scheme 为深色时 system 解析成 dark', async () => {
  const root = fakeRoot();
  const controller = createThemeController({
    root, storage: fakeStorage(), media: fakeMedia(true), request: null,
  });
  await controller.load();
  assert.equal(controller.pref, "system");
  assert.equal(root.attrs["data-theme"], "dark");
});

test('加载时以服务端为准，覆盖注入值', async () => {
  const root = fakeRoot({"data-theme-pref": "light"});
  const storage = fakeStorage();
  const calls = [];
  const controller = createThemeController({
    root, storage, media: fakeMedia(false),
    request: async (path) => {
      calls.push(path);
      assert.equal(path, "/api/settings/appearance");
      return {theme: "dark"};
    },
  });
  await controller.load();
  assert.equal(calls.length, 1);
  assert.equal(controller.pref, "dark");
  assert.equal(root.attrs["data-theme"], "dark");
});

test('设置接口失败不阻断首屏，仍然按注入值上色', async () => {
  const root = fakeRoot({"data-theme-pref": "dark"});
  const controller = createThemeController({
    root, storage: fakeStorage(), media: fakeMedia(false),
    request: async () => { throw new Error("boom"); },
  });
  await controller.load();
  assert.equal(root.attrs["data-theme"], "dark");
});

test('每次换肤都把偏好镜像回 localStorage，供下个页面首屏防闪用', async () => {
  const root = fakeRoot();
  const storage = fakeStorage();
  const controller = createThemeController({
    root, storage, media: fakeMedia(false), request: async () => ({theme: "system"}),
  });
  await controller.load();
  assert.equal(storage.data[THEME_STORAGE_KEY], "system");
  await controller.set("dark", {persist: false});
  assert.equal(storage.data[THEME_STORAGE_KEY], "dark");
});

test('set 会 PUT 回服务端并回报 saved', async () => {
  const root = fakeRoot();
  const requests = [];
  const controller = createThemeController({
    root, storage: fakeStorage(), media: fakeMedia(false),
    request: async (path, options) => {
      requests.push({path, options});
      return options?.method === "PUT"
        ? {theme: "dark", themes: ["light", "dark", "system"]}
        : {theme: "system", themes: ["light", "dark", "system"]};
    },
  });
  await controller.load();
  const result = await controller.set("dark");
  assert.equal(result.saved, true);
  const put = requests.at(-1);
  assert.equal(put.path, "/api/settings/appearance");
  assert.equal(put.options.method, "PUT");
  assert.deepEqual(JSON.parse(put.options.body), {theme: "dark"});
});

test('保存失败时仍然换肤，但明确回报 saved=false', async () => {
  const root = fakeRoot();
  const controller = createThemeController({
    root, storage: fakeStorage(), media: fakeMedia(false),
    request: async (path, options) => {
      if (options?.method === "PUT") throw new Error("网络断了");
      return {theme: "system"};
    },
  });
  await controller.load();
  const result = await controller.set("light");
  assert.equal(result.saved, false);
  assert.equal(root.attrs["data-theme"], "light");
});

test('system 时跟随系统变化，显式选择后不再跟随', async () => {
  const root = fakeRoot();
  const media = fakeMedia(false);
  const controller = createThemeController({
    root, storage: fakeStorage(), media, request: async () => ({theme: "system"}),
  });
  await controller.load();
  assert.equal(root.attrs["data-theme"], "light");
  media.emit(true);
  assert.equal(root.attrs["data-theme"], "dark");

  await controller.set("light", {persist: false});
  media.emit(false);
  media.emit(true);
  assert.equal(root.attrs["data-theme"], "light", "用户显式选浅色后系统切换不该把界面变暗");
});

test('onChange 把新主题交给调用方（用来推给插件 iframe）', async () => {
  const root = fakeRoot();
  const seen = [];
  const controller = createThemeController({
    root, storage: fakeStorage(), media: fakeMedia(false),
    request: async () => ({theme: "light"}),
    onChange: (theme, pref) => seen.push([theme, pref]),
  });
  await controller.load();
  await controller.set("dark", {persist: false});
  assert.deepEqual(seen.at(-1), ["dark", "dark"]);
});
