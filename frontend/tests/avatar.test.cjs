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
  const images = new Map(), handlers = {}, rendered = [], paints = [], timeouts = [];
  class Image {
    complete = true; naturalWidth = 400; naturalHeight = 600; listeners = []; on = {};
    set src(value) { this.url = value; images.set(value, this); }
    // 差し替えの成否で動きが変わるので、load と error を区別して覚えておく。
    addEventListener(name, callback) { this.listeners.push(callback); (this.on[name] ??= []).push(callback); }
  }
  const context = { globalAlpha: 1, clearRect() {},
    drawImage(img) { rendered.push(img.url); paints.push({ src: img.url, alpha: context.globalAlpha }); } };
  const canvas = { width: 400, height: 600, style: {}, getContext: () => context };
  // 重ね合わせは実時間で進む。時計は進めた分だけ返し、1コマごとに半分ずつ進める。
  let clock = 0;
  const ctx = vm.createContext({ Image, canvas,
    performance: { now: () => clock },
    requestAnimationFrame: callback => { clock += 110; callback(clock); return 1; },
    cancelAnimationFrame() {},
    window: {
      addEventListener: (name, callback) => { handlers[name] = callback; },
      setInterval: () => 1, clearInterval() {},
      setTimeout: callback => timeouts.push(callback), clearTimeout() {},
    } });
  vm.runInContext(compiled + '\nglobalThis.avatar = new Avatar(canvas);', ctx);
  return { avatar: ctx.avatar, images, rendered, paints, canvas,
    life: detail => handlers['kaguya-life']({ detail }),
    // 一回性の動きが終わった時点まで進める。
    settle: () => { const pending = timeouts.splice(0); pending.forEach(callback => callback()); } };
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

test('絵の差し替えは、前の絵と重ねながら行う', () => {
  const h = harness();
  h.avatar.setState('thinking');
  const before = h.paints.length;
  h.avatar.setState('talking');
  const during = h.paints.slice(before);
  // 途中の濃さで両方が描かれていれば、瞬間の差し替えではなく重ね合わせになっている。
  const faded = during.filter(paint => 0 < paint.alpha && paint.alpha < 1);
  assert.deepEqual([...new Set(faded.map(paint => paint.src))].sort(),
    ['/sprites/talk.png', '/sprites/think.png']);
  assert.equal(h.rendered.at(-1), '/sprites/talk.png');
});

test('専用イラストが未配置なら、従来の絵で代わりに描く', () => {
  const h = harness(), snack = h.images.get('/sprites/snack.png');
  snack.complete = false;
  h.life({ mood: 'normal', activity: 'snacking', energy: 60 });
  snack.on.error.forEach(callback => callback());
  assert.equal(h.rendered.at(-1), '/sprites/wave.png');
});

test('出来事には一度だけ動き、そのあと普段のゆれへ戻る', () => {
  const h = harness();
  h.avatar.react('nod');
  assert.equal(h.canvas.style.transform, 'translateY(4px) rotate(0.6deg)');
  const nudging = h.canvas.style.transition;
  h.settle();
  // 戻ったあとは、状態に応じた普段のゆれの長さになっている。
  assert.notEqual(h.canvas.style.transition, nudging);
  assert.match(h.canvas.style.transition, /1750ms/);
});

test('同じ気分が届き続けても、跳ねるのは変わった一度だけ', () => {
  const h = harness();
  const hop = 'translateY(-10px) scale(1.02)';
  h.life({ mood: 'happy', activity: 'idle', energy: 60 });
  assert.equal(h.canvas.style.transform, hop);
  h.settle();
  // 2度目以降は普段のゆれのまま。ゆれは2つの位置を行き来するので、跳ねていないことを見る。
  h.life({ mood: 'happy', activity: 'idle', energy: 60 });
  assert.notEqual(h.canvas.style.transform, hop);
  h.life({ mood: 'happy', activity: 'idle', energy: 60 });
  assert.notEqual(h.canvas.style.transform, hop);
});
