const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

// 「なぜ使えないか」と「次に何をするか」を出す判定だけを取り出して確かめる。
// 実機のマイクやAudioContextには触れない。
const source = fs.readFileSync(path.join(__dirname, '../src/voice.ts'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '')
  .replace(/^export /gm, '');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

function reason({ secure, microphone }) {
  const context = vm.createContext({
    window: { isSecureContext: secure },
    navigator: microphone ? { mediaDevices: { getUserMedia() {} } } : {},
    document: { getElementById: () => null, addEventListener() {} },
  });
  vm.runInContext(compiled + '\nvar result = VoiceChat.unavailable();', context);
  return context.result;
}

test('HTTPS(またはPC本体)でマイクがあれば使える', () => {
  assert.equal(reason({ secure: true, microphone: true }), '');
});

test('家庭内Wi-FiのHTTP接続では、理由と次の一手を出す', () => {
  const message = reason({ secure: false, microphone: true });
  assert.match(message, /マイクを使えません/);
  // 「使えません」で終わらせず、設定方法まで案内する。
  assert.match(message, /pc_setup\.bat/);
  assert.match(message, /ts\.net/);
});

test('マイク非対応のブラウザは、接続ではなくブラウザの問題として案内する', () => {
  const message = reason({ secure: true, microphone: false });
  assert.match(message, /ブラウザはマイクに対応していません/);
  assert.doesNotMatch(message, /pc_setup\.bat/);
});

// 音量の覚え方と消音の見せ方だけを確かめる。実際の音の経路はブラウザで見る。
function volumeHarness(stored = {}) {
  const data = new Map(Object.entries(stored));
  const elements = {};
  const element = () => ({
    handlers: {}, attributes: {}, dataset: {}, value: '', hidden: true, disabled: false, textContent: '',
    addEventListener(name, callback) { this.handlers[name] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
  });
  const visibility = {};
  const context = vm.createContext({
    window: { isSecureContext: true, addEventListener() {}, clearTimeout() {}, clearInterval() {} },
    WebSocket: { OPEN: 1 },
    navigator: { mediaDevices: { getUserMedia() {} } },
    document: {
      getElementById: id => elements[id] ??= element(),
      addEventListener(name, handler) { visibility[name] = handler; },
    },
    localStorage: {
      getItem: key => data.has(key) ? data.get(key) : null,
      setItem: (key, value) => data.set(key, value),
    },
    Promise,
  });
  vm.runInContext(compiled + '\nvar chat = new VoiceChat("http://x", async () => ({sessionToken:"t"}), () => {});', context);
  return { elements, data, context, visibility,
    slide: percent => { elements['voice-gain'].value = String(percent); elements['voice-gain'].handlers.input(); },
    mute: () => elements['voice-mute'].handlers.click() };
}

test('音量は端末に覚えて、次の通話でも同じ大きさにする', () => {
  const h = volumeHarness();
  h.slide(40);
  assert.equal(h.data.get('kaguya.voiceVolume'), '40');
  // 覚えた値は次回つまみへ戻る。
  const again = volumeHarness({ 'kaguya.voiceVolume': '40' });
  assert.equal(again.elements['voice-gain'].value, '40');
});

test('覚えた値が無いときは、これまでと同じ大きさで始める', () => {
  const h = volumeHarness();
  assert.equal(h.elements['voice-gain'].value, '100');
});

test('壊れた値を覚えていても、通話できる大きさにする', () => {
  const h = volumeHarness({ 'kaguya.voiceVolume': 'ほげ' });
  assert.equal(h.elements['voice-gain'].value, '100');
});

test('消音は状態が見えるようにし、次の通話へ持ち越す', () => {
  const h = volumeHarness();
  h.mute();
  assert.equal(h.elements['voice-volume'].attributes['data-muted'], 'true');
  assert.equal(h.elements['voice-mute'].attributes['aria-pressed'], 'true');
  assert.equal(h.elements['voice-mute'].attributes['aria-label'], 'かぐやの音声の消音を解除');
  assert.equal(h.data.get('kaguya.voiceMuted'), '1');
  const again = volumeHarness({ 'kaguya.voiceMuted': '1' });
  assert.equal(again.elements['voice-volume'].attributes['data-muted'], 'true');
});

test('つまみを動かしたら消音は解除する', () => {
  const h = volumeHarness({ 'kaguya.voiceMuted': '1' });
  assert.equal(h.elements['voice-volume'].attributes['data-muted'], 'true');
  // 動かしても無音のままだと、壊れているように見える。
  h.slide(60);
  assert.equal(h.elements['voice-volume'].attributes['data-muted'], 'false');
  assert.equal(h.data.get('kaguya.voiceMuted'), '0');
});

test('マイクミュートと相手の音量は独立し、終了時はマイクを解放する', () => {
  const h = volumeHarness();
  const track = { enabled: true, stop() { this.stopped = true; } };
  h.context.chat.stream = { getAudioTracks: () => [track], getTracks: () => [track] };
  h.context.chat.active = true;
  h.mute();
  assert.equal(track.enabled, true);
  h.elements['voice-mic'].handlers.click();
  assert.equal(track.enabled, false);
  assert.equal(h.elements['voice-mic'].textContent, 'マイクを入れる');
  h.slide(50);
  assert.equal(track.enabled, false);
  h.elements['voice-mic'].handlers.click();
  assert.equal(track.enabled, true);
  h.context.chat.stop('通話終了');
  assert.equal(track.stopped, true);
  assert.equal(h.elements['voice-mic'].hidden, true);
});

test('画面を離れた理由と再開ボタンが残り、残り時間は終了時に消える', () => {
  const h = volumeHarness();
  h.context.chat.active = true;
  h.context.chat.deadline = Date.now() + 59000;
  h.context.chat.updateRemaining();
  assert.match(h.elements['voice-remaining'].textContent, /まもなく終了/);
  h.context.document.hidden = true;
  h.visibility.visibilitychange();
  assert.match(h.elements['voice-status'].textContent, /画面を離れたため/);
  assert.equal(h.elements['voice-reconnect'].hidden, false);
  assert.equal(h.elements['voice-remaining'].textContent, '');
});

test('端末が保存を拒んでも通話は続けられる', () => {
  const elements = {};
  const element = () => ({
    handlers: {}, attributes: {}, value: '', hidden: true, disabled: false, textContent: '',
    addEventListener(name, callback) { this.handlers[name] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
  });
  const context = vm.createContext({
    window: { isSecureContext: true, addEventListener() {} },
    navigator: { mediaDevices: { getUserMedia() {} } },
    document: { getElementById: id => elements[id] ??= element(), addEventListener() {} },
    // プライベートウィンドウなどでは読み書きそのものが失敗する。
    localStorage: { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } },
    Promise,
  });
  vm.runInContext(compiled + '\nvar chat = new VoiceChat("http://x", async () => ({sessionToken:"t"}), () => {});', context);
  assert.equal(elements['voice-gain'].value, '100');
  elements['voice-gain'].value = '30';
  elements['voice-gain'].handlers.input();
  assert.equal(elements['voice-volume'].attributes['data-muted'], 'false');
});
