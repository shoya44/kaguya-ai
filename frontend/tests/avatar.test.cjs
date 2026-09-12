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
      // 待ち時間で区別できるように覚える。呼吸と身じろぎは桁が違う。
      setTimeout: (callback, ms) => timeouts.push({ callback, ms: Number(ms) || 0 }),
      clearTimeout() {},
    } });
  vm.runInContext(compiled + '\nglobalThis.avatar = new Avatar(canvas);', ctx);
  // 指定より短い待ちのタイマーだけを進める。再登録ぶんは次の呼び出しに回す。
  const fire = (under, over = 0) => {
    const ready = timeouts.filter(entry => entry.ms < under && entry.ms >= over);
    for (const entry of ready) timeouts.splice(timeouts.indexOf(entry), 1);
    ready.forEach(entry => entry.callback());
  };
  return { avatar: ctx.avatar, images, rendered, paints, canvas,
    life: detail => handlers['kaguya-life']({ detail }),
    // 呼吸1コマと、一回性の動きの終わりまで進める。身じろぎは含めない。
    settle: () => fire(10_000),
    // 手持ち無沙汰な間の身じろぎのタイマーだけを進める。
    idleBreak: () => fire(Infinity, 10_000) };
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
  assert.equal(h.canvas.style.transform, 'scaleY(0.985)');
  const nudging = h.canvas.style.transition;
  h.settle();
  // 戻ったあとは、一回性の動きではなく呼吸の長さになっている。
  assert.notEqual(h.canvas.style.transition, nudging);
  assert.match(h.canvas.style.transition, /(1400|2500)ms/);
});

test('呼吸は縦に伸びるだけで、接地点は動かさない', () => {
  const h = harness();
  // 上下へ動かすと足や床まで一緒に浮く。平行移動が混ざっていないこと。
  const seen = new Set();
  for (let i = 0; i < 4; i++) { h.settle(); seen.add(h.canvas.style.transform); }
  for (const value of seen) assert.doesNotMatch(value, /translate/);
  assert.ok([...seen].some(value => /scaleY\(1\.0[1-9]/.test(value)), [...seen].join(' / '));
});

test('伏せている絵は、座っている絵より動かさない', () => {
  const h = harness();
  const stretch = value => {
    const found = /scaleY\(([\d.]+)\)/.exec(value);
    return found ? Number(found[1]) : 1;
  };
  // 呼吸は吸う・吐くを往復するので、2回ぶん見て膨らんだ側を取る。
  const peak = activity => {
    const detail = { mood: 'normal', activity, energy: 60 };
    h.life(detail);
    const first = stretch(h.canvas.style.transform);
    h.life(detail);
    return Math.max(first, stretch(h.canvas.style.transform));
  };
  const sitting = peak('snacking');
  const lying = peak('daydreaming');
  assert.ok(1 < lying && lying < sitting, `伏せ${lying} は 座り${sitting} より小さく伸びること`);
});

test('息は吸うより吐くほうが長い', () => {
  const h = harness();
  const durations = new Set();
  for (let i = 0; i < 4; i++) { h.settle(); durations.add(h.canvas.style.transition); }
  // 往復が同じ長さだと振り子に見える。2種類の長さが交互に出ること。
  assert.equal(durations.size, 2);
  assert.ok([...durations].some(value => value.includes('1400ms')));
  assert.ok([...durations].some(value => value.includes('2500ms')));
});

test('支点は足元に置く', () => {
  const h = harness();
  assert.equal(h.canvas.style.transformOrigin, '50% 100%');
});

test('同じ気分が届き続けても、跳ねるのは変わった一度だけ', () => {
  const h = harness();
  const hop = 'translateY(-9px) scaleY(1.01)';
  h.life({ mood: 'happy', activity: 'idle', energy: 60 });
  assert.equal(h.canvas.style.transform, hop);
  h.settle();
  // 2度目以降は普段のゆれのまま。ゆれは2つの位置を行き来するので、跳ねていないことを見る。
  h.life({ mood: 'happy', activity: 'idle', energy: 60 });
  assert.notEqual(h.canvas.style.transform, hop);
  h.life({ mood: 'happy', activity: 'idle', energy: 60 });
  assert.notEqual(h.canvas.style.transform, hop);
});

test('手持ち無沙汰な間は、ときどき身じろぎする', () => {
  const h = harness();
  h.idleBreak();
  assert.equal(h.canvas.style.transform, 'rotate(-0.6deg) scaleY(0.997)');
});

test('話しかけられている最中は身じろぎしない', () => {
  const h = harness();
  const settle = 'rotate(-0.6deg) scaleY(0.997)';
  for (const state of ['thinking', 'talking', 'organizing']) {
    h.avatar.setState(state);
    h.settle();
    h.idleBreak();
    assert.notEqual(h.canvas.style.transform, settle, `${state} 中に身じろぎしないこと`);
  }
});

test('身じろぎは、強い動きの最中には割り込まない', () => {
  const h = harness();
  h.avatar.react('droop');
  const droop = h.canvas.style.transform;
  h.idleBreak();
  assert.equal(h.canvas.style.transform, droop);
});

test('強い動きは、弱い動きの最中なら割り込める', () => {
  const h = harness();
  h.avatar.react('settle');
  h.avatar.react('hop');
  assert.equal(h.canvas.style.transform, 'translateY(-9px) scaleY(1.01)');
});

test('戻ってきた相手には顔を上げる', () => {
  const h = harness();
  h.life({ mood: 'normal', activity: 'reading', energy: 60, reentry: true });
  assert.equal(h.canvas.style.transform, 'scaleY(1.025)');
});

test('眠りへ入るときと、目を覚ますときに動く', () => {
  const h = harness();
  h.life({ mood: 'normal', activity: 'reading', energy: 60 });
  h.settle();
  // 静音にすると生活の演出より眠りが優先され、実際に眠った絵になる。
  h.avatar.setState('sleeping', true);
  assert.equal(h.canvas.style.transform, 'scaleY(0.98)', '寝入るときは沈む');
  h.settle();
  h.avatar.setState('idle');
  assert.equal(h.canvas.style.transform, 'scaleY(1.04)', '目を覚ますときは伸びをする');
});

test('眠ったように見えないなら、沈まない', () => {
  const h = harness();
  // 元気なら「会話なし=睡眠」でも生活の演出が優先され、絵は眠っていない。
  h.life({ mood: 'normal', activity: 'reading', energy: 60 });
  h.settle();
  h.avatar.setState('sleeping');
  assert.notEqual(h.canvas.style.transform, 'scaleY(0.98)');
});
