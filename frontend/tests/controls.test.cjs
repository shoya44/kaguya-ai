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
  assert.deepEqual(card.children.map(child => child.tag), ['strong', 'pre']);
  assert.equal(card.children[0].textContent, '気にかけている話題 ／ <script>topic</script>');
  assert.equal(JSON.parse(card.children[1].textContent).asked, 2);
  assert.equal(elements['memory-next'].disabled, false);
  assert.equal(elements['memory-prev'].disabled, false);
});

test('empty Mind displays an empty state', async () => {
  const { controls, elements } = harness(async () => ({ items: [], next_offset: null }));
  await controls.refreshMemories();
  assert.equal(elements['memory-list'].textContent, '該当する記憶はありません。');
  assert.equal(elements['memory-next'].disabled, true);
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
