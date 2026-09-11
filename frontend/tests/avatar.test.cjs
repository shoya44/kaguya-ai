const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/avatar.ts'), 'utf8').replace(/^export /gm, '');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

function harness() {
  const images = new Map(), handlers = {}, rendered = [];
  class Image {
    complete = true; naturalWidth = 400; naturalHeight = 600; listeners = [];
    set src(value) { this.url = value; images.set(value, this); }
    addEventListener(_, callback) { this.listeners.push(callback); }
  }
  const canvas = { width: 400, height: 600, style: {},
    getContext: () => ({ clearRect() {}, drawImage(img) { rendered.push(img.url); } }) };
  const ctx = vm.createContext({ Image, canvas, window: {
    addEventListener: (name, callback) => { handlers[name] = callback; },
    setInterval: () => 1, clearInterval() {},
  } });
  vm.runInContext(compiled + '\nglobalThis.avatar = new Avatar(canvas);', ctx);
  return { avatar: ctx.avatar, images, rendered, life: detail => handlers['kaguya-life']({ detail }) };
}

test('quiet changes the sprite even when the logical sleep state is unchanged', () => {
  const h = harness();
  h.life({ mood: 'normal', activity: 'working', energy: 60 });
  h.avatar.setState('sleeping');
  assert.equal(h.rendered.at(-1), '/sprites/laptop.png');
  h.avatar.setState('sleeping', true);
  assert.equal(h.rendered.at(-1), '/sprites/sleep.png');
  h.avatar.setState('sleeping', false);
  assert.equal(h.rendered.at(-1), '/sprites/laptop.png');
});

test('old image finishing later cannot overwrite the current talking sprite', () => {
  const h = harness(), thinking = h.images.get('/sprites/think.png');
  thinking.complete = false;
  h.avatar.setState('thinking'); h.avatar.setState('talking');
  thinking.listeners.forEach(callback => callback());
  assert.equal(h.rendered.at(-1), '/sprites/talk.png');
});

test('broken image preserves the last valid frame', () => {
  const h = harness(), thinking = h.images.get('/sprites/think.png');
  const before = h.rendered.length;
  thinking.naturalWidth = 0;
  h.avatar.setState('thinking');
  assert.equal(h.rendered.length, before);
});
