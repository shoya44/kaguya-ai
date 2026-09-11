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
    this.classList = { contains: () => false, toggle: () => {}, add: () => {}, remove: () => {} };
    this.handlers = {};
    this.className = '';
    this.textContent = '';
    this.scrollTop = 0;
    this.value = '';
  }
  focus() {}
  get scrollHeight() { return this.children.length * 30; }
  set innerHTML(_) { this.children = []; }
  appendChild(child) { this.children.push(child); }
  addEventListener(name, callback) { this.handlers[name] = callback; }
  querySelectorAll(tag) {
    return this.children.flatMap(child => [
      ...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag),
    ]);
  }
}

function harness(options = {}) {
  const elements = {}, sockets = [], timers = [], calls = [], sent = [];
  const sessionData = new Map(), localData = new Map();
  const storage = data => ({ getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) });
  class Socket {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 1; this.handlers = {}; sockets.push(this); }
    addEventListener(name, callback) { this.handlers[name] = callback; }
    send(value) { sent.push(JSON.parse(value)); }
    emit(event) { this.handlers.message({ data: JSON.stringify(event) }); }
    close(code = 1006) { this.readyState = 3; this.handlers.close?.({ code }); }
  }
  const ctx = vm.createContext({
    document: { getElementById: id => elements[id] ??= new Element(), createElement: tag => new Element(tag),
      visibilityState: 'visible' },
    location: { hostname: '127.0.0.1' },
    Avatar: class { setState() {} }, isTauri: () => !!options.tauri,
    invoke: options.invoke ?? (async () => 'owned'),
    sessionStorage: storage(sessionData), localStorage: storage(localData),
    WebSocket: Socket, URLSearchParams, AbortSignal, Date, Error,
    crypto: { randomUUID: () => 'new-turn' },
    window: { setTimeout: callback => timers.push(callback), setInterval: () => 0, clearTimeout: () => {} },
    fetch: async (url, init) => {
      calls.push({ url, init });
      return options.fetch ? options.fetch(url, init) : response({ items: [], next_cursor: null });
    },
  });
  vm.runInContext(compiled, ctx);
  const run = code => vm.runInContext(code, ctx);
  run("saveSession({clientId:'client',sessionToken:'old-token'})");
  const flush = () => new Promise(resolve => setImmediate(resolve));
  return { run, elements, sockets, timers, calls, sent, localData, flush };
}
const response = (body, status = 200) => ({ ok: status === 200, status, json: async () => body });
const row = (id, status = 'completed') => ({ turn_id: id, text: id, answer: status === 'completed' ? `answer-${id}` : null, status, client_id: 'client' });
async function connected(h) {
  await h.run('connectWs()');
  h.sockets.at(-1).emit({ type: 'state.changed', state: 'idle' });
  await h.flush();
}

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
