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

function harness(seed) {
  // living.ts only resolves text-input when the submit handler runs, while the
  // real page already contains both elements. Pre-create that real DOM shape
  // so tests do not depend on lazy getElementById side effects.
  const elements = {
    'input-form': new Element(),
    'text-input': new Element(),
  };
  const storage = new Map();
  // 既に使ったことのある端末を再現する。新品の端末はlastSeenが「今」になるため、
  // サーバ時刻との前後関係がテストごとにぶれる。
  if (seed) storage.set('kaguya.life.v1', JSON.stringify(seed));
  const emitted = [];
  const timers = [];
  const docHandlers = {};
  const winHandlers = {};

  class FakeCustomEvent {
    constructor(type, init = {}) { this.type = type; this.detail = init.detail; }
  }

  const ctx = vm.createContext({
    localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
    },
    document: {
      visibilityState: 'visible',
      getElementById: id => elements[id] ??= new Element(),
      addEventListener: (name, callback) => { docHandlers[name] = callback; },
    },
    window: {
      addEventListener: (name, callback) => { winHandlers[name] = callback; },
      dispatchEvent: event => { emitted.push(event); return true; },
      setInterval: () => 1,
      setTimeout: callback => { timers.push(callback); return timers.length; },
      clearTimeout: () => {},
    },
    CustomEvent: FakeCustomEvent,
    Date, JSON, Math, Number, Set,
  });

  vm.runInContext(compiled, ctx);
  return { elements, storage, emitted, timers, docHandlers, winHandlers };
}

test('living module emits local state without network dependencies', () => {
  const h = harness();
  assert.ok(h.emitted.some(event => event.type === 'kaguya-life'));
  const detail = h.emitted.find(event => event.type === 'kaguya-life').detail;
  assert.ok(['idle', 'reading', 'working', 'playing', 'snacking', 'daydreaming', 'sleeping'].includes(detail.activity));
  assert.equal(typeof detail.energy, 'number');
});

test('sending marks the visit but does not decide the mood or count the turn', () => {
  const h = harness();
  const before = JSON.parse(h.storage.get('kaguya.life.v1') ?? 'null');
  h.elements['text-input'].value = 'かぐや、かわいい。ありがとう';
  h.elements['input-form'].handlers.submit();
  const saved = JSON.parse(h.storage.get('kaguya.life.v1'));
  // 回数と時間帯は会話が成立してから数える。表情と親密度はサーバ側が持つ。
  assert.equal(saved.interactions, 0);
  assert.equal(saved.hourCounts.reduce((sum, value) => sum + value, 0), 0);
  assert.ok(!('mood' in saved));
  assert.ok(!('affection' in saved));
  assert.ok(before === null || saved.lastSeen >= before.lastSeen);
});

test('a conversation on any device counts once and keeps the visit fresh', () => {
  const h = harness();
  const at = Date.now();
  h.winHandlers['kaguya-served']({ detail: { at, counted: true } });
  const saved = JSON.parse(h.storage.get('kaguya.life.v1'));
  assert.equal(saved.interactions, 1);
  assert.equal(saved.hourCounts[new Date(at).getHours()], 1);
  assert.equal(saved.lastSeen, at);
});

test('the server last-activity only refreshes the visit, it does not count a turn', () => {
  const at = Date.now();
  const h = harness({ lastSeen: at - 3600_000, interactions: 4, hourCounts: Array(24).fill(0) });
  h.winHandlers['kaguya-served']({ detail: { at, counted: false } });
  const saved = JSON.parse(h.storage.get('kaguya.life.v1'));
  assert.equal(saved.interactions, 4);
  assert.equal(saved.lastSeen, at);
});

test('an older timestamp never moves the visit backwards', () => {
  const now = Date.now();
  const h = harness({ lastSeen: now - 7200_000, interactions: 0, hourCounts: Array(24).fill(0) });
  h.winHandlers['kaguya-served']({ detail: { at: now, counted: false } });
  h.winHandlers['kaguya-served']({ detail: { at: now - 3600_000, counted: false } });
  assert.equal(JSON.parse(h.storage.get('kaguya.life.v1')).lastSeen, now);
});

test('the mood comes from the server so every device shows the same face', () => {
  const h = harness();
  h.winHandlers['kaguya-mood']({ detail: { mood: 'sulky' } });
  assert.equal(h.emitted.at(-1).detail.mood, 'sulky');

  const before = h.emitted.length;
  h.winHandlers['kaguya-mood']({ detail: { mood: 'sulky' } });
  assert.equal(h.emitted.length, before, '同じ表情の再送では再描画しない');

  h.winHandlers['kaguya-mood']({ detail: { mood: 'unknown' } });
  assert.equal(h.emitted.at(-1).detail.mood, 'sulky', '未知の値は無視する');
});

test('pagehide persists state without any server dependency', () => {
  const h = harness();
  h.winHandlers.pagehide();
  assert.ok(h.storage.has('kaguya.life.v1'));
});
