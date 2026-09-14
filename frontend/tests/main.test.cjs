const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

// Run the actual UI handlers without a server, API key, or browser dependency.
const source = fs.readFileSync(path.join(__dirname, '../src/main.ts'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '')
  .replace(/main\(\)\.catch\([^\n]+/, '');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.classes = new Set();
    this.classList = {
      contains: name => this.classes.has(name),
      toggle: (name, on) => { if (on === undefined ? this.classes.has(name) : !on) this.classes.delete(name); else this.classes.add(name); },
      add: name => this.classes.add(name), remove: name => this.classes.delete(name),
    };
    this.handlers = {};
    this.className = '';
    this.textContent = '';
    this.scrollTop = 0;
    this.clientHeight = 0;
    this.value = '';
    this.hidden = false;
    this.disabled = false;
  }
  focus() {}
  get scrollHeight() { return this.children.length * 30; }
  set innerHTML(_) { this.children = []; }
  appendChild(child) { this.children.push(child); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(name, callback) { this.handlers[name] = callback; }
  querySelectorAll(tag) {
    return this.children.flatMap(child => [
      ...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag),
    ]);
  }
}

function harness(options = {}) {
  const elements = {}, sockets = [], timers = [], calls = [], sent = [], nudges = [];
  const sessionData = new Map(), localData = new Map();
  const storage = data => ({ getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value),
    removeItem: key => data.delete(key) });
  const body = new Element('body');
  class Socket {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 1; this.handlers = {}; sockets.push(this); }
    addEventListener(name, callback) { this.handlers[name] = callback; }
    send(value) { sent.push(JSON.parse(value)); }
    emit(event) { this.handlers.message({ data: JSON.stringify(event) }); }
    close(code = 1006) { this.readyState = 3; this.handlers.close?.({ code }); }
  }
  const dispatched = [];
  const ctx = vm.createContext({
    document: {
      body,
      getElementById: id => elements[id] ??= new Element(),
      createElement: tag => new Element(tag),
      visibilityState: 'visible',
    },
    location: { hostname: '127.0.0.1', port: '5173', origin: 'http://127.0.0.1:5173' },
    // かぐやの動きは avatar.test.cjs が見る。ここでは「どの出来事で
    // どの動きを頼んだか」だけを覚えて、繋ぎ間違いを止める。
    Avatar: class { setState() {} hold() {} react(nudge) { nudges.push(nudge); } },
    isTauri: () => !!options.tauri,
    // 通話できる端末かどうかだけを見る。実際の通話はvoice.test.cjsが確かめる。
    VoiceChat: { unavailable: () => options.voiceBlocked ?? '' },
    invoke: options.invoke ?? (async () => 'owned'),
    sessionStorage: storage(sessionData), localStorage: storage(localData),
    WebSocket: Socket, URLSearchParams, AbortSignal, Date, Error,
    crypto: { randomUUID: () => 'new-turn' },
    // living.tsは別のモジュールスクリプトなので、main.tsとはwindowイベントで繋がる。
    CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    window: {
      setTimeout: callback => timers.push(callback),
      setInterval: () => 0,
      clearTimeout: () => {},
      addEventListener: () => {},
      dispatchEvent: event => { dispatched.push(event); return true; },
      matchMedia: query => ({ matches: !!options.touch && query.includes('pointer: coarse') }),
    },
    fetch: async (url, init) => {
      calls.push({ url, init });
      return options.fetch ? options.fetch(url, init) : response({ items: [], next_cursor: null });
    },
  });
  vm.runInContext(compiled, ctx);
  const run = code => vm.runInContext(code, ctx);
  run("saveSession({clientId:'client',sessionToken:'old-token'})");
  const flush = () => new Promise(resolve => setImmediate(resolve));
  return { run, elements, sockets, timers, calls, sent, localData, dispatched, flush, nudges };
}
const response = (body, status = 200) => ({ ok: status === 200, status, json: async () => body });
const row = (id, status = 'completed') => ({ turn_id: id, text: id, answer: status === 'completed' ? `answer-${id}` : null, status, client_id: 'client' });
async function connected(h) {
  await h.run('connectWs()');
  h.sockets.at(-1).emit({ type: 'state.changed', state: 'idle' });
  await h.flush();
}

const living = h => h.dispatched.filter(event => event.type === 'kaguya-living').map(event => event.detail);

test('かぐやの活動はサーバから届き、そのまま画面へ渡される', async () => {
  const h = harness(); await connected(h);
  h.sockets[0].emit({ type: 'living.changed', activity: 'reading', energy: 42,
                      last_seen_at: '2026-09-12T21:00:00+09:00' });
  await h.flush();
  const details = living(h);
  assert.equal(details.length, 1);
  assert.equal(details[0].activity, 'reading');
  assert.equal(details[0].energy, 42);
});

test('会った記録はサーバが持つので、端末側では数えない', async () => {
  const h = harness(); await connected(h);
  h.sockets[0].emit({ type: 'chat.completed', turn_id: 't1', text: 'hello', answer: 'hi' });
  await h.flush();
  assert.equal(h.dispatched.filter(event => event.type === 'kaguya-served').length, 0);
});

for (const code of ['timeout', 'rate_limit']) {
  test(`${code}: retry displays exactly one answer, including duplicate completion`, async () => {
    const h = harness(); await connected(h);
    h.run("sendTurn('hello','t1',false)");
    h.sockets[0].emit({ type: 'chat.error', turn_id: 't1', code, message: 'retry' });
    h.run("sendTurn('hello','t1',true)");
    const done = { type: 'chat.completed', turn_id: 't1', text: 'hello', answer: 'reply' };
    h.sockets[0].emit(done); h.sockets[0].emit(done);
    const answers = h.elements.history.children.filter(el => el.className === 'msg assistant');
    assert.equal(answers.length, 1); assert.equal(answers[0].textContent, 'reply');
    assert.equal(h.sent.at(-1).retry, true);
  });
}

test('4401 renews the token, retaining the client identity and refreshing history', async () => {
  const h = harness({ fetch: async url => url.endsWith('/session')
    ? response({ client_id: 'client', session_token: 'renewed' })
    : response({ items: [row('restored', 'failed')], next_cursor: null }) });
  await h.run('connectWs()'); h.sockets[0].close(4401);
  h.timers.shift()(); await h.flush();
  assert.match(h.sockets[1].url, /renewed$/);
  assert.equal(JSON.parse(h.calls.find(call => call.url.endsWith('/session')).init.body).client_id, 'client');
  h.sockets[1].emit({ type: 'state.changed', state: 'idle' }); await h.flush();
  const retry = h.elements.history.querySelectorAll('button')[0];
  assert.equal(retry.disabled, false); retry.handlers.click();
  assert.equal(h.sent[0].turn_id, 'restored'); assert.equal(h.sent[0].retry, true);
  assert.equal(h.localData.get('kaguya.client'), 'client');
});

test('history 401 renews once and forwards the new bearer token', async () => {
  let attempts = 0;
  const h = harness({ fetch: async url => {
    if (url.endsWith('/session')) return response({ client_id: 'client', session_token: 'renewed' });
    return ++attempts === 1 ? response({}, 401) : response({ items: [], next_cursor: null });
  } });
  await h.run('loadHistory()');
  assert.equal(attempts, 2);
  assert.equal(h.calls.at(-1).init.headers.Authorization, 'Bearer renewed');
});

test('older history prepends in order without losing or duplicating current turns', async () => {
  const h = harness({ fetch: async url => response(url.includes('cursor=')
    ? { items: Array.from({ length: 5 }, (_, n) => row(`t${n}`)), next_cursor: null }
    : { items: Array.from({ length: 50 }, (_, n) => row(`t${n + 5}`)), next_cursor: 'older' }) });
  await connected(h); await h.run('loadHistory(true)');
  const ids = JSON.parse(h.run('JSON.stringify([...turns.keys()])'));
  assert.equal(ids.length, 55); assert.equal(ids[0], 't0'); assert.equal(ids.at(-1), 't54');
  assert.match(h.calls[1].url, /limit=50&cursor=older/); assert.equal(h.elements['older-btn'].hidden, true);
});

test('events during history fetch are replayed after the snapshot', async () => {
  let resolve;
  const h = harness({ fetch: () => new Promise(done => { resolve = done; }) });
  await h.run('connectWs()');
  h.sockets[0].emit({ type: 'state.changed', state: 'thinking' }); await h.flush();
  h.sockets[0].emit({ type: 'chat.completed', turn_id: 't1', text: 'hello', answer: 'reply' });
  h.sockets[0].emit({ type: 'state.changed', state: 'idle' });
  resolve(response({ items: [row('t1', 'pending')], next_cursor: null })); await h.flush();
  assert.equal(h.run("turns.get('t1').answer"), 'reply'); assert.equal(h.elements['send-btn'].disabled, false);
});

test('disconnected submit preserves the draft and does not add a phantom turn', () => {
  const h = harness(); h.elements['text-input'].value = 'draft';
  h.elements['input-form'].handlers.submit({ preventDefault() {} });
  assert.equal(h.elements['text-input'].value, 'draft'); assert.equal(h.run('turns.size'), 0);
});

test('save failure survives rendering and offers only save retry', async () => {
  const h = harness(); await connected(h);
  h.sockets[0].emit({ type: 'chat.error', code: 'save_failed', turn_id: 't1', text: 'hello', answer: 'reply' });
  h.run('renderTurns()');
  assert.equal(h.elements.history.children.at(-1).textContent, 'reply');
  assert.equal(h.elements['send-btn'].disabled, true);
  h.elements.error.querySelectorAll('button')[0].handlers.click();
  assert.equal(h.sent.at(-1).type, 'chat.retry_save');
});

test('Tauri rejects another backend instance before issuing a session request', async () => {
  const h = harness({ tauri: true, fetch: async () => response({ backend_instance: 'other' }) });
  await assert.rejects(h.run('connectWs()'), /ポート8765/);
  assert.equal(h.calls.length, 1); assert.ok(h.calls[0].url.endsWith('/health'));
  assert.equal(h.sockets.length, 0);
});

test('Tauri child startup error reaches the UI without contacting the occupied port', async () => {
  const h = harness({ tauri: true, invoke: async () => { throw 'ポート8765を使用できません。'; } });
  await h.run('startChat()');
  assert.match(h.elements.error.children[0].textContent, /ポート8765/);
  assert.equal(h.calls.length, 0);
});

test('cancel refused during saving preserves active turn and disables sending', async () => {
  const h = harness(); await connected(h);
  h.run("sendTurn('hello','t1',false)");
  h.sockets[0].emit({ type: 'state.changed', state: 'thinking', turn_id: 't1' });
  h.sockets[0].emit({ type: 'chat.error', code: 'saving', turn_id: 't1', message: 'saving' });
  assert.equal(h.run('busy'), true);
  assert.equal(h.run("turns.get('t1').status"), 'pending');
  assert.equal(h.elements['send-btn'].disabled, true);
});

test('busy rejection before admission restores input without inventing an active turn', async () => {
  const h = harness(); await connected(h);
  h.run("sendTurn('hello','t1',false)");
  h.sockets[0].emit({ type: 'chat.error', code: 'busy', turn_id: 't1', message: 'editing' });
  assert.equal(h.run('busy'), false);
  assert.equal(h.run("turns.get('t1').status"), 'failed');
});

test('reminder survives duplicates and replies until acknowledgement succeeds', async () => {
  const h = harness(); await connected(h);
  const event = { type: 'reminder.due', id: 'reminder-one', text: 'take medicine' };
  h.sockets[0].emit(event); h.sockets[0].emit(event);
  h.run("showMiniReply('reply'); hideBubble()");
  const bubble = h.elements['proactive-bubble'];
  assert.equal(bubble.textContent, 'take medicine');
  assert.equal(bubble.hidden, false);
  bubble.handlers.click(); await h.flush();
  assert.ok(h.calls.some(call => call.url.endsWith('/reminders/reminder-one/ack')));
  assert.equal(bubble.hidden, true);
  assert.equal(h.run('reminderId'), null);
});

test('予約の時刻は、画面を前に出して気づかせる', async () => {
  const invoked = [];
  const h = harness({
    tauri: true,
    invoke: async name => { invoked.push(name); return 'owned'; },
    // Tauriではバックエンドの持ち主を確かめてから繋ぐ。同じ識別子を返しておく。
    fetch: async url => url.endsWith('/health')
      ? response({ backend_instance: 'owned' }) : response({ items: [], next_cursor: null }),
  });
  await connected(h);
  h.sockets[0].emit({ type: 'reminder.due', id: 'r-alert', text: '休憩する' });
  await h.flush();
  // 前に出せないときはタスクバーを点滅させる（Rust側のalert_window）。
  assert.ok(invoked.includes('alert_window'));
});

test('予約の時刻には、そのまま通話に出られる', async () => {
  const h = harness(); await connected(h);
  h.sockets[0].emit({ type: 'reminder.due', id: 'r-call', text: '休憩する' });
  const button = h.elements['reminder-call'];
  assert.equal(button.hidden, false);
  button.handlers.click(); await h.flush();
  // 押した時点で気づいているので、確認済みとして送る。ボタンは出したままにしない。
  assert.ok(h.calls.some(call => call.url.endsWith('/reminders/r-call/ack')));
  assert.equal(button.hidden, true);
  assert.equal(h.run('reminderId'), null);
});

test('通話できない端末では、出るボタンを出さない', async () => {
  const h = harness({ voiceBlocked: 'この接続ではブラウザがマイクを使えません。' });
  await connected(h);
  h.sockets[0].emit({ type: 'reminder.due', id: 'r-mute', text: '休憩する' });
  assert.equal(h.elements['reminder-call'].hidden, true);
  // 吹き出しは今までどおり出る。
  assert.equal(h.elements['proactive-bubble'].hidden, false);
});

test('failed reminder acknowledgement leaves notification available to retry', async () => {
  const h = harness({ fetch: async url => url.endsWith('/ack') ? response({}, 502)
    : response({ items: [], next_cursor: null }) });
  await connected(h);
  h.sockets[0].emit({ type: 'reminder.due', id: 'one', text: 'medicine' });
  h.elements['proactive-bubble'].handlers.click(); await h.flush();
  assert.equal(h.run('reminderId'), 'one');
  assert.equal(h.elements['proactive-bubble'].hidden, false);
});

test('streaming updates the pending answer in place and keeps one bubble', async () => {
  const h = harness(); await connected(h);
  h.run("sendTurn('hello','t1',false)");
  h.sockets[0].emit({ type: 'state.changed', state: 'thinking', turn_id: 't1', phase: 'generating' });
  h.sockets[0].emit({ type: 'chat.progress', turn_id: 't1', partial: 'こん' });
  h.sockets[0].emit({ type: 'chat.progress', turn_id: 't1', partial: 'こんにちは' });
  const answers = h.elements.history.children.filter(el => el.className === 'msg assistant pending');
  assert.equal(answers.length, 1);
  assert.equal(answers[0].textContent, 'こんにちは');
  assert.equal(h.elements['chat-status'].textContent, '回答を生成中');
  h.sockets[0].emit({ type: 'chat.completed', turn_id: 't1', text: 'hello', answer: 'こんにちは、元気？' });
  assert.equal(h.elements['chat-status'].textContent, '');
  assert.equal(h.elements.history.children.filter(el => el.className.startsWith('msg assistant')).length, 1);
});

test('reading older messages is not scrolled to the bottom by streaming', async () => {
  const h = harness({ fetch: async () => response({ items: Array.from({ length: 50 }, (_, n) => row(`t${n}`)), next_cursor: null }) });
  await connected(h);
  const history = h.elements.history;
  // 過去を読んでいる状態（最下部から離れている）を作る。
  history.scrollTop = 0; history.clientHeight = 10;
  h.run("sendTurn('hello','new',false)");
  assert.equal(history.scrollTop, 0);
  h.sockets[0].emit({ type: 'chat.progress', turn_id: 'new', partial: 'とても長い返答' });
  assert.equal(history.scrollTop, 0);
  assert.equal(h.elements['latest-btn'].hidden, false);
  h.elements['latest-btn'].handlers.click();
  assert.equal(history.scrollTop, history.scrollHeight);
  assert.equal(h.elements['latest-btn'].hidden, true);
});

test('the draft is kept on the device until the send is accepted', async () => {
  const h = harness(); await connected(h);
  h.elements['text-input'].value = 'まだ書きかけ';
  h.elements['text-input'].handlers.input();
  assert.equal(JSON.parse(h.localData.get('kaguya.draft.v1')).text, 'まだ書きかけ');
  h.elements['input-form'].handlers.submit({ preventDefault() {} });
  const turnId = h.sent.at(-1).turn_id;
  assert.equal(JSON.parse(h.localData.get('kaguya.draft.v1')).turnId, turnId);
  h.sockets[0].emit({ type: 'chat.accepted', turn_id: turnId, text: 'まだ書きかけ', client_id: 'client' });
  assert.equal(h.localData.get('kaguya.draft.v1'), undefined);
});

test('a rejected send leaves the draft in place for a retry', () => {
  const h = harness();
  h.elements['text-input'].value = '送れなかった文';
  h.elements['text-input'].handlers.input();
  h.elements['input-form'].handlers.submit({ preventDefault() {} });
  assert.equal(h.elements['text-input'].value, '送れなかった文');
  assert.equal(JSON.parse(h.localData.get('kaguya.draft.v1')).text, '送れなかった文');
});

test('follow-up buttons send fixed text without asking the server for candidates', async () => {
  const h = harness(); await connected(h);
  h.run("sendTurn('hello','t1',false)");
  const before = h.calls.length;
  h.sockets[0].emit({ type: 'chat.completed', turn_id: 't1', text: 'hello', answer: '短い返事' });
  const buttons = h.elements.history.querySelectorAll('button');
  assert.deepEqual(buttons.map(b => b.textContent), ['もっと詳しく', '例をあげて', '短くまとめて']);
  assert.equal(h.calls.length, before);
  buttons[0].handlers.click();
  assert.equal(h.sent.at(-1).text, 'もっと詳しく');
});

test('references are shown only when memories were actually passed to the model', async () => {
  const h = harness(); await connected(h);
  h.run("sendTurn('hello','t1',false)");
  h.sockets[0].emit({ type: 'chat.completed', turn_id: 't1', text: 'hello', answer: '返事',
    references: [{ label: 'コーヒー', text: 'ブラックが好き' }] });
  const box = h.elements.history.children.find(el => el.className === 'msg-references');
  assert.equal(box.children[0].textContent, '参照した記憶 1件');
  assert.equal(box.children[1].textContent, 'コーヒー：ブラックが好き');
});

test('on a touch device the return key inserts a newline instead of sending', async () => {
  const h = harness({ touch: true }); await connected(h);
  h.elements['text-input'].value = '一行目';
  let prevented = false;
  h.elements['text-input'].handlers.keydown({ key: 'Enter', shiftKey: false, isComposing: false,
    preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(h.sent.length, 0);
});

test('the connection state is shown and restored across a reconnect', async () => {
  const h = harness(); await connected(h);
  assert.equal(h.elements['connection-status'].textContent, '接続済み');
  h.sockets[0].close();
  assert.equal(h.elements['connection-status'].textContent, '未接続（再接続します）');
});

test('the measured response time is shown under the answer', async () => {
  const h = harness(); await connected(h);
  h.run("sendTurn('hello','t1',false)");
  h.sockets[0].emit({ type: 'chat.completed', turn_id: 't1', text: 'hello', answer: '返事',
    elapsed_ms: 3420, first_text_ms: 1180 });
  const timing = h.elements.history.children.find(el => el.className === 'msg-timing');
  assert.equal(timing.textContent, '書き始めまで 1.2秒 ／ 全体 3.4秒');
});

test('no timing is invented when the server did not measure one', async () => {
  const h = harness(); await connected(h);
  h.run("sendTurn('hello','t1',false)");
  h.sockets[0].emit({ type: 'chat.completed', turn_id: 't1', text: 'hello', answer: '返事' });
  assert.equal(h.elements.history.children.some(el => el.className === 'msg-timing'), false);
});

test('the first history page is small and only catching up reads a full page', async () => {
  const h = harness({ fetch: async () => response({ items: [row('t0')], next_cursor: 'older' }) });
  await connected(h);
  assert.match(h.calls.at(-1).url, /limit=20/);
  await h.run('loadHistory(true)');
  assert.match(h.calls.at(-1).url, /limit=50&cursor=older/);
});

test('会話の出来事が、かぐやの動きに繋がっている', async () => {
  const h = harness(); await connected(h);
  const socket = h.sockets[0];

  h.run("sendTurn('hello','t1',false)");
  assert.ok(h.nudges.includes('nod'), '送信を受け取ったら頷く');

  h.nudges.length = 0;
  socket.emit({ type: 'chat.progress', turn_id: 't1', partial: 'こ' });
  assert.ok(h.nudges.includes('beat'), '返答が流れる間は拍を打つ');

  h.nudges.length = 0;
  socket.emit({ type: 'chat.completed', turn_id: 't1', text: 'hello', answer: 'hi' });
  await h.flush();
  assert.ok(h.nudges.includes('inhale'), '話し出す前にひと呼吸する');

  h.nudges.length = 0;
  socket.emit({ type: 'reminder.due', id: 'r1', text: '薬を飲む' });
  assert.ok(h.nudges.includes('call'), 'リマインダーは呼びかける');

  h.nudges.length = 0;
  h.run("sendTurn('hello','t2',false)");
  socket.emit({ type: 'chat.error', turn_id: 't2', code: 'timeout', message: 'だめだった' });
  assert.ok(h.nudges.includes('droop'), '返事に失敗したらしゅんとする');
});

test('書き始めに気づくが、打つたびには反応を頼まない', () => {
  const h = harness();
  const input = h.elements['text-input'];
  input.value = 'こ';
  input.handlers.input();
  input.value = 'こん';
  input.handlers.input();
  // 間隔を絞るのはavatar側。main.tsは入力のたびに頼んでよい。
  assert.deepEqual(h.nudges, ['perk', 'perk']);
});

test('通話中は、簡易表示でも終わらせるボタンが残るようにする', async () => {
  const h = harness(); await connected(h);
  // 簡易表示は吹き出しが出ると入力欄ごと隠れる。通話中に隠れると
  // 終わらせるボタンまで消えるので、通話中であることをCSSへ伝える。
  h.run('voiceToggle(true)');
  assert.ok(h.elements.app.classes.has('calling'), '通話開始で calling が付く');
  h.run('voiceToggle(false)');
  assert.ok(!h.elements.app.classes.has('calling'), '通話終了で calling が外れる');
  // 絵も通話に合わせて止め、また動き出す。
  assert.deepEqual(h.nudges.slice(-2), ['inhale', 'nod']);
});

test('タスクバーに出さない設定が残っていること', () => {
  // トレイが唯一の入口になる設定。外すとタスクバーにも出てしまう。
  const conf = JSON.parse(fs.readFileSync(path.join(__dirname, '../src-tauri/tauri.conf.json'), 'utf8'));
  const main = conf.app.windows.find(w => w.label === 'main');
  assert.equal(main.skipTaskbar, true);
});
