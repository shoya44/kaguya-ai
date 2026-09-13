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
  let lastTimer = 0;
  const ctx = vm.createContext({ Image, canvas,
    performance: { now: () => clock },
    requestAnimationFrame: callback => { clock += 110; callback(clock); return 1; },
    cancelAnimationFrame() {},
    window: {
      addEventListener: (name, callback) => { handlers[name] = callback; },
      setInterval: () => 1, clearInterval() {},
      // 待ち時間で区別できるように覚える。呼吸と身じろぎは桁が違う。
      // 取り消しは本物と同じく実際に消す。消さないと呼吸のタイマーが二重に
      // 溜まり、1回進めるつもりで2回進んでしまう。
      setTimeout: (callback, ms) => {
        const id = ++lastTimer;
        timeouts.push({ id, callback, ms: Number(ms) || 0 });
        return id;
      },
      clearTimeout: id => {
        const at = timeouts.findIndex(entry => entry.id === id);
        if (at >= 0) timeouts.splice(at, 1);
      },
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
    // 呼吸1コマ、一回性の動きの終わり、まばたきの終わりまで進める。
    settle: () => fire(3_000),
    // まばたきのタイマーだけを進める（間隔は3.6〜7秒）。
    blink: () => fire(10_000, 3_000),
    // 手持ち無沙汰な間の身じろぎのタイマーだけを進める（30〜90秒）。
    idleBreak: () => fire(Infinity, 10_000) };
}

test('何かしている最中は、気分より活動の姿を出す', () => {
  // 気分は何日も同じ値で張り付くことがある。気分の絵を常に優先していたため、
  // そのあいだ読書も作業もおやつも一度も画面に出なかった。
  const h = harness();
  h.life({ mood: 'worried', activity: 'working', energy: 60 });
  assert.equal(h.rendered.at(-1), '/sprites/laptop.png', '心配していても作業はしている');
  h.life({ mood: 'worried', activity: 'snacking', energy: 60 });
  assert.equal(h.rendered.at(-1), '/sprites/snack.png');
  // 手が空いて、そばにいるときだけ顔で気分を出す。
  h.life({ mood: 'worried', activity: 'idle', energy: 60 });
  assert.equal(h.rendered.at(-1), '/sprites/worry.png');
});

test('気分の絵が無くても、活動の姿は出る', () => {
  const h = harness();
  h.life({ mood: 'normal', activity: 'playing', energy: 60 });
  assert.equal(h.rendered.at(-1), '/sprites/cards.png');
});

test('眠った絵になるかはサーバの生活状態だけで決まる', () => {
  // 静音（声かけ停止）は眠りではない。ここを睡眠扱いにしていたため、
  // 静音にした日からずっと寝た絵のままになっていた。
  const h = harness();
  h.life({ mood: 'normal', activity: 'working', energy: 60 });
  h.avatar.setState('sleeping');
  assert.equal(h.rendered.at(-1), '/sprites/laptop.png', '起きて作業中なら寝ない');
  h.life({ mood: 'sleepy', activity: 'sleeping', energy: 20 });
  assert.equal(h.rendered.at(-1), '/sprites/sleep.png', '眠いと届いて初めて寝る');
  h.life({ mood: 'normal', activity: 'working', energy: 60 });
  assert.equal(h.rendered.at(-1), '/sprites/laptop.png', '起きたらまた作業に戻る');
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
  // 眠ったことはサーバの生活状態から届く。
  h.life({ mood: 'sleepy', activity: 'sleeping', energy: 20 });
  assert.equal(h.canvas.style.transform, 'scaleY(0.98)', '寝入るときは沈む');
  h.settle();
  h.life({ mood: 'normal', activity: 'reading', energy: 60 });
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

test('返答が流れてくる間の拍は、間隔を空けて出す', () => {
  const h = harness();
  const beat = 'scaleY(0.995)';
  h.avatar.react('beat');
  assert.equal(h.canvas.style.transform, beat);
  h.settle();
  // 拍は数十ms間隔で届く。そのまま出すと震えるので、続けては出さない。
  h.avatar.react('beat');
  assert.notEqual(h.canvas.style.transform, beat);
});

test('顔を上げるのは、打つたびではなく気づいた一度だけ', () => {
  const h = harness();
  const perk = 'scaleY(1.025)';
  h.avatar.react('perk');
  assert.equal(h.canvas.style.transform, perk);
  h.settle();
  h.avatar.react('perk');
  assert.notEqual(h.canvas.style.transform, perk);
});

test('呼びかけは、ほかのどの動きにも飲まれない', () => {
  const h = harness();
  const call = 'translateY(-6px) rotate(1.5deg) scaleY(1.02)';
  // いちばん強い hop / droop の最中でも割り込めること。
  for (const strong of ['hop', 'droop']) {
    const fresh = harness();
    fresh.avatar.react(strong);
    fresh.avatar.react('call');
    assert.equal(fresh.canvas.style.transform, call, `${strong} の最中でも呼びかけられること`);
  }
  h.avatar.react('call');
  // 逆に、呼びかけの最中はほかの動きが割り込めない。
  h.avatar.react('hop');
  assert.equal(h.canvas.style.transform, call);
});

test('拍は、話しかけへの反応より弱い', () => {
  const h = harness();
  h.avatar.react('inhale');
  const inhale = h.canvas.style.transform;
  h.avatar.react('beat');
  assert.equal(h.canvas.style.transform, inhale);
});

test('ときどき目を閉じて、すぐ開ける', () => {
  const h = harness();
  // idleの絵は時刻で変わる（IDLE_BY_TIME）。読書なら常に本の絵に決まる。
  h.life({ mood: 'normal', activity: 'reading', energy: 60 });
  assert.equal(h.rendered.at(-1), '/sprites/book.png');
  h.blink();
  assert.equal(h.rendered.at(-1), '/sprites/book-blink.png', '目を閉じる');
  h.settle();
  assert.equal(h.rendered.at(-1), '/sprites/book.png', 'すぐ開ける');
});

test('まばたきは重ね合わせずに差し替える', () => {
  const h = harness();
  h.life({ mood: 'normal', activity: 'reading', energy: 60 });
  const before = h.paints.length;
  h.blink();
  // 220msかけて閉じると瞬きではなく眠そうに見える。半端な濃さで描かないこと。
  const during = h.paints.slice(before);
  assert.deepEqual(during.map(paint => paint.alpha), [1]);
  assert.equal(during[0].src, '/sprites/book-blink.png');
});

test('眠っている絵では瞬かない', () => {
  const h = harness();
  h.life({ mood: 'sleepy', activity: 'sleeping', energy: 20 });
  h.avatar.setState('sleeping');
  assert.equal(h.rendered.at(-1), '/sprites/sleep.png');
  const before = h.rendered.length;
  h.blink();
  assert.equal(h.rendered.length, before, 'もう目を閉じているので描き直さない');
});

test('差分絵の無い絵では瞬かない', () => {
  const h = harness();
  // トランプ遊びの絵にはまばたき差分が無い。
  h.life({ mood: 'normal', activity: 'playing', energy: 60 });
  assert.equal(h.rendered.at(-1), '/sprites/cards.png');
  const before = h.rendered.length;
  h.blink();
  assert.equal(h.rendered.length, before);
});

test('まばたき差分が未配置なら、目を開けたままにする', () => {
  const h = harness(), closed = h.images.get('/sprites/book-blink.png');
  closed.complete = false;
  h.life({ mood: 'normal', activity: 'reading', energy: 60 });
  h.blink();
  closed.on.error.forEach(callback => callback());
  assert.equal(h.rendered.at(-1), '/sprites/book.png');
});

test('通話中は絵を切り替えない', () => {
  const h = harness();
  h.life({ mood: 'normal', activity: 'reading', energy: 60 });
  h.avatar.hold(true);
  assert.equal(h.rendered.at(-1), '/sprites/talk.png', '通話中は話す絵で固定する');
  const before = h.rendered.length;
  // 通話中に届く気分・活動の変化では絵を変えない。
  h.life({ mood: 'happy', activity: 'playing', energy: 90 });
  h.life({ mood: 'bored', activity: 'snacking', energy: 30 });
  h.avatar.setState('thinking');
  assert.deepEqual([...new Set(h.rendered.slice(before))], ['/sprites/talk.png']);
});

test('通話中でもまばたきは動く', () => {
  const h = harness();
  h.avatar.hold(true);
  h.blink();
  assert.equal(h.rendered.at(-1), '/sprites/talk-blink.png');
  h.settle();
  assert.equal(h.rendered.at(-1), '/sprites/talk.png');
});

test('通話が終わると、いまの状態の絵へ戻る', () => {
  const h = harness();
  h.life({ mood: 'normal', activity: 'reading', energy: 60 });
  h.avatar.hold(true);
  h.life({ mood: 'normal', activity: 'playing', energy: 60 });
  h.avatar.hold(false);
  assert.equal(h.rendered.at(-1), '/sprites/cards.png', '通話中に届いていた活動が反映される');
});

test('通話中は、伏せ姿勢あつかいの小さな呼吸にしない', () => {
  const h = harness();
  // 伏せている絵のまま固定すると、通話中ずっと呼吸が小さいままになる。
  h.life({ mood: 'normal', activity: 'daydreaming', energy: 60 });
  h.avatar.hold(true);
  const seen = new Set();
  for (let i = 0; i < 4; i++) { h.settle(); seen.add(h.canvas.style.transform); }
  assert.ok([...seen].some(value => value.includes('scaleY(1.0125)')), [...seen].join(' / '));
});

test('READMEの絵の一覧が、実物と食い違っていない', () => {
  // READMEは「ここを見ればなんでも分かる」を目指している。絵を足したのに
  // 書き忘れると、一覧を信じた人が存在しない絵を探すことになる。
  const readme = fs.readFileSync(path.join(__dirname, '../../README.md'), 'utf8');
  const section = readme.split('## 状態と表情')[1].split('## モーション')[0];
  const placed = fs.readdirSync(path.join(__dirname, '../public/sprites'))
    .filter(name => name.endsWith('.png'));
  for (const file of placed) {
    // まばたき差分は本体の行に「あり」として載るので、個別には書かない。
    if (file.endsWith('-blink.png')) continue;
    assert.ok(section.includes(file), `${file} がREADMEの一覧に無い`);
  }
  // 逆に、置いていない絵を載せない。
  for (const found of section.matchAll(/`([a-z-]+\.png)`/g)) {
    assert.ok(placed.includes(found[1]), `${found[1]} はREADMEにあるが置かれていない`);
  }
});

test('まばたきできる絵の一覧が、READMEと合っている', () => {
  const readme = fs.readFileSync(path.join(__dirname, '../../README.md'), 'utf8');
  const section = readme.split('## 状態と表情')[1].split('## モーション')[0];
  const placed = fs.readdirSync(path.join(__dirname, '../public/sprites'));
  for (const row of section.split('\n')) {
    const cells = row.split('|').map(cell => cell.trim());
    const file = cells.find(cell => /^`[a-z-]+\.png`$/.test(cell))?.replace(/`/g, '');
    if (!file || cells.length < 4) continue;
    const claimsBlink = cells.some(cell => cell === 'あり');
    const hasBlink = placed.includes(file.replace('.png', '-blink.png'));
    assert.equal(claimsBlink, hasBlink, `${file} のまばたき有無がREADMEと違う`);
  }
});
