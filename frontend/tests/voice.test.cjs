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
