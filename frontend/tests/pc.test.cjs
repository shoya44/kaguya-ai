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

function duration(seconds) {
  const context = vm.createContext({ document: { getElementById: () => null, querySelectorAll: () => [] }, window: { addEventListener() {} } });
  vm.runInContext(compiled + '\nvar result = PCPanel.duration(value);', Object.assign(context, { value: seconds }));
  return context.result;
}

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
