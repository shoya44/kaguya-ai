const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

// 一覧に出す「長さ」の書式だけを取り出して確かめる。DOMもサーバーも使わない。
const source = fs.readFileSync(path.join(__dirname, '../src/pc.ts'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '')
  .replace(/^export /gm, '');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

function call(method, ...args) {
  const context = vm.createContext({ document: { getElementById: () => null, querySelectorAll: () => [] }, window: { addEventListener() {} } });
  vm.runInContext(compiled + `\nvar result = PCPanel.${method}(...args);`, Object.assign(context, { args }));
  return context.result;
}

const duration = seconds => call('duration', seconds);

test('分と秒で出す', () => {
  assert.equal(duration(150), '2:30');
  assert.equal(duration(59), '0:59');
  assert.equal(duration(600), '10:00');
});

test('1時間を超えたら時も出す', () => {
  assert.equal(duration(3600), '1:00:00');
  assert.equal(duration(7325), '2:02:05');
});

test('読めなかった動画は長さを出さない', () => {
  for (const value of [null, undefined, 0, -1, NaN, 'abc']) {
    assert.equal(duration(value), '', `${value}`);
  }
});

test('大きさはエクスプローラーと同じ単位で出す', () => {
  assert.equal(call('size', 512), '512 B');
  assert.equal(call('size', 1024), '1.0 KB');
  assert.equal(call('size', 87654321), '83.6 MB');
  assert.equal(call('size', 1287654321), '1.2 GB');
});

test('大きさが読めなければ何も出さない', () => {
  for (const value of [null, undefined, 0, -1, NaN, 'abc']) {
    assert.equal(call('size', value), '', `${value}`);
  }
});

test('置き場所はフォルダ名から順に並べる', () => {
  assert.equal(call('breadcrumb', '動画', '旅行/2026沖縄.mp4'), '動画 ＞ 旅行');
  assert.equal(call('breadcrumb', '動画', '旅行/沖縄/1日目.mp4'), '動画 ＞ 旅行 ＞ 沖縄');
});

test('フォルダ直下のファイルはフォルダ名だけにする', () => {
  assert.equal(call('breadcrumb', 'vid', 'clip01.mp4'), 'vid');
  assert.equal(call('breadcrumb', 'vid', ''), 'vid');
});
