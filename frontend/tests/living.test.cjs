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
  // living.ts only resolves text-input when the submit handler runs, while the
  // real page already contains both elements. Pre-create that real DOM shape
  // so tests do not depend on lazy getElementById side effects.
  const elements = {
    'input-form': new Element(),
    'text-input': new Element(),
  };
  const storage = new Map();
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

test('praise changes mood locally and records an interaction hour', () => {
  const h = harness();
  h.elements['text-input'].value = 'かぐや、かわいい。ありがとう';
  h.elements['input-form'].handlers.submit();
  const last = h.emitted.at(-1).detail;
  assert.equal(last.mood, 'happy');
  const saved = JSON.parse(h.storage.get('kaguya.life.v1'));
  assert.equal(saved.interactions, 1);
  assert.equal(saved.hourCounts.reduce((sum, value) => sum + value, 0), 1);
});

test('pagehide persists state without any server dependency', () => {
  const h = harness();
  h.winHandlers.pagehide();
  assert.ok(h.storage.has('kaguya.life.v1'));
});
