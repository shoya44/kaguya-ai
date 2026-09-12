const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/living.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

class Element {
  constructor() {
    this.handlers = {};
    this.value = '';
    this.hidden = true;
    this.textContent = '';
  }
  addEventListener(name, callback) { this.handlers[name] = callback; }
}

function harness() {
  const elements = { 'proactive-bubble': new Element() };
  const emitted = [];
  const timers = [];
  const docHandlers = {};
  const winHandlers = {};

  class FakeCustomEvent {
    constructor(type, init = {}) { this.type = type; this.detail = init.detail; }
  }

  const ctx = vm.createContext({
    document: {
      visibilityState: 'visible',
      getElementById: id => elements[id] ??= new Element(),
      addEventListener: (name, callback) => { docHandlers[name] = callback; },
    },
    window: {
      addEventListener: (name, callback) => { winHandlers[name] = callback; },
      dispatchEvent: event => { emitted.push(event); return true; },
      setTimeout: callback => { timers.push(callback); return timers.length; },
      clearTimeout: () => {},
    },
    CustomEvent: FakeCustomEvent,
    Date, JSON, Math, Number, String,
  });

  vm.runInContext(compiled, ctx);
  return { elements, emitted, timers, docHandlers, winHandlers };
}

const last = h => h.emitted.at(-1).detail;

test('画面を開いた時点で、いまの状態を1度だけ出す', () => {
  const h = harness();
  assert.equal(h.emitted.length, 1);
  assert.equal(h.emitted[0].type, 'kaguya-life');
  assert.equal(last(h).activity, 'idle');
  assert.equal(typeof last(h).energy, 'number');
});

test('表情はサーバが決めるので、どの端末でも同じ顔になる', () => {
  const h = harness();
  h.winHandlers['kaguya-mood']({ detail: { mood: 'sulky' } });
  assert.equal(last(h).mood, 'sulky');

  const before = h.emitted.length;
  h.winHandlers['kaguya-mood']({ detail: { mood: 'sulky' } });
  assert.equal(h.emitted.length, before, '同じ表情の再送では再描画しない');

  h.winHandlers['kaguya-mood']({ detail: { mood: 'unknown' } });
  assert.equal(last(h).mood, 'sulky', '未知の値は無視する');
});

test('活動と元気さもサーバが決める', () => {
  const h = harness();
  h.winHandlers['kaguya-living']({ detail: { activity: 'reading', energy: 41 } });
  assert.equal(last(h).activity, 'reading');
  assert.equal(last(h).energy, 41);
});

test('同じ活動が届いても描き直さない', () => {
  const h = harness();
  h.winHandlers['kaguya-living']({ detail: { activity: 'reading', energy: 41 } });
  const before = h.emitted.length;
  h.winHandlers['kaguya-living']({ detail: { activity: 'reading', energy: 41 } });
  assert.equal(h.emitted.length, before);
});

test('知らない活動は無視して、いまの表示を保つ', () => {
  const h = harness();
  h.winHandlers['kaguya-living']({ detail: { activity: 'reading', energy: 50 } });
  h.winHandlers['kaguya-living']({ detail: { activity: 'dancing', energy: 50 } });
  assert.equal(last(h).activity, 'reading');
});

test('久しぶりに開いたときだけ、何をしていたかを一言だけ言う', () => {
  const h = harness();
  const old = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  h.winHandlers['kaguya-living']({ detail: { activity: 'reading', energy: 50, last_seen_at: old } });
  h.winHandlers.pageshow();
  assert.equal(h.elements['proactive-bubble'].hidden, false);
  assert.match(h.elements['proactive-bubble'].textContent, /本読/);
});

test('すぐ戻ってきたときは黙っている', () => {
  const h = harness();
  const justNow = new Date().toISOString();
  h.winHandlers['kaguya-living']({ detail: { activity: 'reading', energy: 50, last_seen_at: justNow } });
  h.winHandlers.pageshow();
  assert.equal(h.elements['proactive-bubble'].hidden, true);
});

test('端末に状態を溜めない（localStorageを使わない）', () => {
  // ctx に localStorage を渡していないので、触れば ReferenceError で落ちる。
  // 落ちずにここまで来ること自体が、端末側に状態を持たない証拠になる。
  const h = harness();
  h.winHandlers.pagehide();
  h.docHandlers.visibilitychange();
  assert.ok(h.emitted.length >= 1);
});
