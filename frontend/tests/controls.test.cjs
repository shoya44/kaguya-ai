const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/controls.ts'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace('export class Controls', 'class Controls');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.handlers = {}; this.value = ''; }
  addEventListener(event, handler) { this.handlers[event] = handler; }
  setAttribute(key, value) { this[key] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; this.textContent = ''; }
}

function harness(api) {
  const elements = {};
  const document = {
    getElementById: id => elements[id] ||= new Element('div'),
    querySelectorAll: () => [], createElement: tag => new Element(tag),
  };
  const context = vm.createContext({ document, URLSearchParams, isTauri: () => false });
  vm.runInContext(compiled + '\nglobalThis.Controls = Controls;', context);
  const controls = new context.Controls(api);
  controls.refreshSummary = async () => {};
  controls.layer = 'mind';
  return { controls, elements };
}

test('Mind displays stored fields as text without mutation buttons and supports paging', async () => {
  let requested;
  const { controls, elements } = harness(async url => {
    requested = url;
    return { items: [{ category: 'open_loops', title: '<script>topic</script>',
      data: { quote: '<img src=x>', asked: 2, resolved_at: null } }], next_offset: 60 };
  });
  elements['memory-search'] = new Element('input');
  elements['memory-search'].value = '気になる';
  controls.offset = 30;
  await controls.refreshMemories();
  assert.equal(new URL(requested, 'http://localhost').searchParams.get('q'), '気になる');
  assert.match(requested, /offset=30/);
  const card = elements['memory-list'].children[0];
  assert.deepEqual(card.children.map(child => child.tag), ['strong', 'dl']);
  assert.equal(card.children[0].textContent, '気にかけている話題 ／ <script>topic</script>');
  assert.deepEqual(card.children[1].children.map(child => child.textContent),
    ['きっかけの言葉', '<img src=x>', '声をかけた回数', '2回', '解決した日時', '未解決']);
  assert.equal(elements['memory-next'].disabled, false);
  assert.equal(elements['memory-prev'].disabled, false);
});

test('Mind formats emotion names, scores, zero values and dates with readable labels', async () => {
  const { controls, elements } = harness(async () => ({ items: [
    { category: 'emotions', title: 'happiness', data: { name: 'happiness', value: 58.1234 } },
    { category: 'traits', title: 'coffee', data: { valence: 0.8, confidence: 0, evidence: 3 } },
    { category: 'open_loops', title: '予定', data: { kind: 'plan', asked: 0,
      due_at: '2026-09-12T09:30:00+09:00', last_asked_at: null } },
    { category: 'graph_edges', title: 'user / likes / coffee', data: { subject: 'user', relation: 'likes', object: 'coffee', strength: 0.6 } },
    { category: 'meta', title: 'interactions', data: { key: 'interactions', value: '4', extra: 'preserved' } },
    { category: 'phrases', title: 'hello', data: { text: 'hello', count: 2 } },
  ], next_offset: null }));
  await controls.refreshMemories();
  const cards = elements['memory-list'].children;
  const fields = index => cards[index].children[1].children.map(child => child.textContent);
  assert.equal(cards[0].children[0].textContent, '感情 ／ うれしさ');
  assert.ok(fields(0).includes('58.1 / 100'));
  assert.ok(fields(1).includes('80 / 100'));
  assert.ok(fields(1).includes('0 / 100'));
  assert.ok(fields(2).includes('0回'));
  assert.ok(fields(2).includes('まだ声をかけていません'));
  assert.ok(fields(2).some(value => value.includes('2026/09/12')));
  assert.equal(cards[3].children[0].textContent, '関連情報 ／ user → 好き → coffee');
  assert.ok(fields(4).includes('preserved'));
  assert.ok(fields(5).includes('2回'));
});

test('empty Mind displays an empty state', async () => {
  const { controls, elements } = harness(async () => ({ items: [], next_offset: null }));
  await controls.refreshMemories();
  assert.equal(elements['memory-list'].textContent, '該当する記憶はありません。');
  assert.equal(elements['memory-next'].disabled, true);
  assert.equal(elements['memory-page'].textContent, '0件');
});

test('loading clears stale cards and failed loads remain retryable without stale paging', async () => {
  let reject;
  const { controls, elements } = harness(() => new Promise((_, fail) => { reject = fail; }));
  elements['memory-list'] = new Element('div');
  elements['memory-list'].append(new Element('article'));
  controls.nextOffset = 30;
  const loading = controls.refreshMemories();
  assert.equal(elements['memory-list'].children.length, 0);
  assert.equal(elements['memory-list']['aria-busy'], 'true');
  assert.equal(elements['memory-next'].disabled, true);
  reject(new Error('offline'));
  await assert.rejects(loading, /offline/);
  assert.match(elements['memory-list'].textContent, /再試行/);
  assert.equal(elements['memory-list']['aria-busy'], 'false');
  controls.api = async () => ({ items: [], next_offset: null });
  await controls.refreshMemories();
  assert.equal(elements['memory-page'].textContent, '0件');
});

test('summary failure does not prevent browsing memories', async () => {
  const { controls, elements } = harness(async () => ({ items: [], next_offset: null }));
  controls.refreshSummary = async () => { throw new Error('summary unavailable'); };
  await controls.refreshMemories();
  assert.match(elements['memory-summary'].textContent, /取得できません/);
  assert.equal(elements['memory-list'].textContent, '該当する記憶はありません。');
});

test('action buttons prevent duplicate requests and re-enable after failure', async () => {
  const { controls } = harness(async () => ({}));
  let calls = 0, reject, pending;
  controls.perform = action => { pending = action().catch(() => {}); };
  const button = controls.button('取り消す', () => {
    calls += 1;
    return new Promise((_, fail) => { reject = fail; });
  });
  button.handlers.click(); button.handlers.click();
  assert.equal(calls, 1);
  assert.equal(button.disabled, true);
  reject(new Error('failed'));
  await pending;
  assert.equal(button.disabled, false);
});

test('older requests cannot replace the newly selected memory layer', async () => {
  let finishOld;
  const { controls, elements } = harness(url => url.startsWith('/memories/mind')
    ? new Promise(resolve => { finishOld = resolve; })
    : Promise.resolve({ items: [], next_offset: null }));
  const old = controls.refreshMemories();
  await Promise.resolve();
  controls.layer = 'persona';
  await controls.refreshMemories();
  finishOld({ items: [{ category: 'meta', title: 'old', data: {} }], next_offset: 30 });
  await old;
  assert.equal(elements['memory-list'].textContent, '該当する記憶はありません。');
  assert.equal(elements['memory-next'].disabled, true);
});
