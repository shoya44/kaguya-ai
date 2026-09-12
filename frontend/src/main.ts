import './style.css';
import { Avatar } from './avatar';
import { isTauri } from './tauri';
import { invoke } from '@tauri-apps/api/core';
import { Controls } from './controls';
import { PCPanel } from './pc';
import { VoiceChat } from './voice';
import { getCurrentWindow, currentMonitor, primaryMonitor, LogicalSize, PhysicalPosition, PhysicalSize } from '@tauri-apps/api/window';
import { Menu } from '@tauri-apps/api/menu';

// Stage 6 (home Wi-Fi only, no HTTPS/pairing yet): inside Tauri the backend
// is always our own loopback child process, so 127.0.0.1 stays correct.
// Everywhere else (the iPhone's Safari, or a plain dev browser tab) the
// backend is reached at whatever host the page itself was loaded from —
// the iPhone opens http://<PC's LAN address>:8765/, which FastAPI now also
// serves this build from, so location.hostname is exactly the right host.
const BACKEND_HOST = isTauri() ? '127.0.0.1' : location.hostname;
const API_BASE = isTauri() || location.port === '5173' ? `http://${BACKEND_HOST}:8765` : location.origin;
const WS_BASE = API_BASE.replace(/^http/, 'ws');
const SESSION_KEY = 'kaguya.session';
const CLIENT_KEY = 'kaguya.client';
const MINI_MODE_KEY = 'kaguya.miniMode';
const DRAFT_KEY = 'kaguya.draft.v1';
let voiceActive = false;

// 簡易版UI（デスクトップマスコット表示）: 透過・枠なしウィンドウを画面右下に
// 固定する。位置はドラッグ不可・毎回右下に再計算するだけなので設定ファイル
// への永続化はせず、モードのon/offのみlocalStorageに覚えておく。
const MINI_SIZE = { width: 160, height: 400 };
const MINI_MARGIN = { x: 12, y: 4 };
const NORMAL_SIZE_DEFAULT = { width: 420, height: 640 };

// 簡易表示に入る直前の通常ウィンドウの状態。最大化中に setSize/setPosition を
// 呼んでもWindows側で反映されないことがあるため、最大化は先に解除し、戻す時に
// 元の状態へ復元する。切替要求は直列化し、ダブルクリック等でも競合させない。
let savedNormalPosition: PhysicalPosition | null = null;
let savedNormalSize: PhysicalSize | null = null;
let savedNormalMaximized = false;
let miniModeTransition: Promise<void> = Promise.resolve();

function applyMiniUi(enabled: boolean): void {
  if (enabled) setInputFocused(false);
  document.body.classList.toggle('mini-mode', enabled);
  document.getElementById('app')!.classList.toggle('mode-mini', enabled);
  document.getElementById('app')!.classList.toggle('mode-normal', !enabled);
  inputEl.placeholder = enabled ? '' : INPUT_PLACEHOLDER;
  try { localStorage.setItem(MINI_MODE_KEY, enabled ? '1' : '0'); } catch { /* best effort */ }
}

function setMiniMode(enabled: boolean): Promise<void> {
  const transition = miniModeTransition.then(() => transitionMiniMode(enabled));
  // 失敗しても次回の切替要求まで失敗状態を引きずらない。
  miniModeTransition = transition.catch(() => {});
  return transition;
}

// core:window:default に含まれるTauri標準の内部トグルを使う。
// maximize/unmaximize個別の追加権限を増やさず、現在の最大化状態だけ反転できる。
async function toggleMaximizedWindow(): Promise<void> {
  await invoke('plugin:window|internal_toggle_maximize');
}

async function transitionMiniMode(enabled: boolean): Promise<void> {
  const app = document.getElementById('app')!;
  if (app.classList.contains('mode-mini') === enabled) return;
  if (!isTauri()) {
    applyMiniUi(enabled);
    return;
  }

  const win = getCurrentWindow();
  if (enabled) {
    // リサイズ前に対象モニターを確定する。装飾変更・縮小の途中で
    // currentMonitor() が一時的に null になるケースを避ける。
    const monitor = (await currentMonitor().catch(() => null)) ?? (await primaryMonitor().catch(() => null));
    savedNormalMaximized = await win.isMaximized().catch(() => false);
    if (savedNormalMaximized) await toggleMaximizedWindow();
    savedNormalPosition = await win.outerPosition().catch(() => null);
    // setSize() は内側サイズを設定するAPIなので、復元用も innerSize() で保持する。
    savedNormalSize = await win.innerSize().catch(() => null);

    try {
      // 見た目だけの操作は失敗しても縮小・移動を続ける。
      await win.setDecorations(false).catch(() => {});
      await win.setResizable(false).catch(() => {});
      await win.setShadow(false).catch(() => {});
      await win.setSize(new LogicalSize(MINI_SIZE.width, MINI_SIZE.height));
      if (monitor) {
        const scale = monitor.scaleFactor || 1;
        const marginX = MINI_MARGIN.x * scale;
        const marginY = MINI_MARGIN.y * scale;
        const miniWidthPx = MINI_SIZE.width * scale;
        const miniHeightPx = MINI_SIZE.height * scale;
        const x = monitor.workArea.position.x + monitor.workArea.size.width - miniWidthPx - marginX;
        const y = monitor.workArea.position.y + monitor.workArea.size.height - miniHeightPx - marginY;
        await win.setPosition(new PhysicalPosition(Math.round(x), Math.round(y)));
      }
      await win.show().catch(() => {});
      await win.setFocus().catch(() => {});
      // ネイティブ側の縮小・移動が完了してからDOMを簡易表示へ切り替える。
      // 途中失敗時に「UIだけ透明な簡易表示」になる状態を防ぐ。
      applyMiniUi(true);
    } catch (error) {
      // 中途半端な枠なし/縮小状態を残さず、通常表示へ戻して呼び出し側へ通知する。
      await win.setDecorations(true).catch(() => {});
      await win.setResizable(true).catch(() => {});
      await win.setShadow(true).catch(() => {});
      if (savedNormalMaximized) {
        await toggleMaximizedWindow().catch(() => {});
      } else {
        await win.setSize(savedNormalSize ?? new LogicalSize(NORMAL_SIZE_DEFAULT.width, NORMAL_SIZE_DEFAULT.height)).catch(() => {});
        if (savedNormalPosition) await win.setPosition(savedNormalPosition).catch(() => {});
      }
      applyMiniUi(false);
      throw error;
    }
    return;
  }

  await win.setDecorations(true).catch(() => {});
  await win.setResizable(true).catch(() => {});
  await win.setShadow(true).catch(() => {});
  if (savedNormalMaximized) {
    await toggleMaximizedWindow();
  } else {
    await win.setSize(savedNormalSize ?? new LogicalSize(NORMAL_SIZE_DEFAULT.width, NORMAL_SIZE_DEFAULT.height));
    if (savedNormalPosition) await win.setPosition(savedNormalPosition);
  }
  await win.show().catch(() => {});
  await win.setFocus().catch(() => {});
  applyMiniUi(false);
}

// キャラ画像を右クリックすると、表示切替と終了をまとめた簡易メニューを出す。
// 画面上に専用ボタンを置かないぶん、右クリックが簡易版での主な操作導線になる。
function setupAvatarContextMenu(): void {
  if (!isTauri()) return;
  const avatarEl = document.getElementById('avatar');
  avatarEl?.addEventListener('contextmenu', async (event) => {
    event.preventDefault();
    const isMini = document.getElementById('app')!.classList.contains('mode-mini');
    try {
      const menu = await Menu.new({
        items: [
          {
            id: 'toggle-mini',
            text: isMini ? '通常表示に戻す' : '簡易表示に切り替え',
            action: () => { setMiniMode(!isMini).catch(() => showError('表示を切り替えられませんでした。', null)); },
          },
          { item: 'Separator' },
          {
            id: 'quit',
            text: '終了',
            action: () => { invoke('app_quit').catch(() => showError('終了できませんでした。', null)); },
          },
        ],
      });
      await menu.popup();
    } catch {
      showError('メニューを表示できませんでした。', null);
    }
  });
  // 簡易表示中はキャラのダブルクリックでも通常表示に戻せるようにする
  // （右クリックメニューを開かなくても済む、最短の導線）。
  avatarEl?.addEventListener('dblclick', () => {
    if (document.getElementById('app')!.classList.contains('mode-mini')) {
      setMiniMode(false).catch(() => showError('通常表示に戻せませんでした。', null));
    }
  });
}

interface StoredSession {
  clientId: string;
  sessionToken: string;
}

interface HistoryTurn {
  turn_id: string;
  text: string;
  answer: string | null;
  status: string;
  client_id: string;
  partial?: string;
  references?: { label: string; text: string }[];
  elapsed_ms?: number;
  first_text_ms?: number;
}

interface PendingTurn {
  turnId: string;
  text: string;
  retry: boolean;
}

const historyEl = document.getElementById('history') as HTMLDivElement;
const errorEl = document.getElementById('error') as HTMLDivElement;
const form = document.getElementById('input-form') as HTMLFormElement;
const inputEl = document.getElementById('text-input') as HTMLTextAreaElement;
const sendBtn = document.getElementById('send-btn') as HTMLButtonElement;
const olderBtn = document.getElementById('older-btn') as HTMLButtonElement;
const avatarCanvas = document.getElementById('avatar') as HTMLCanvasElement;
const avatar = new Avatar(avatarCanvas);

let session: StoredSession | null = loadSession();
let ws: WebSocket | null = null;
let pending: PendingTurn | null = null;
let reconnectDelay = 1000;
let nextCursor: string | null = null;
let historyLoading = false;
let busy = false;
let synchronized = false;
let unsavedTurnId: string | null = null;
let turns = new Map<string, HistoryTurn>();
let controls: Controls | null = null;
let activeTurnId: string | null = null;
const answerElements = new Map<string, HTMLDivElement>();

// iPhone/iPadでは改行キーは改行のまま残し、送信は送信ボタンだけに任せる。
// PC（細かいポインタ）では従来どおりEnter送信・Shift+Enter改行。
const TOUCH_INPUT = typeof window.matchMedia === 'function'
  && window.matchMedia('(hover: none) and (pointer: coarse)').matches;
// タッチ端末の入力欄は1行分の高さしかないので、但し書きを足すと
// プレースホルダーが2行になり下半分が見切れる。送信ボタンは隣にあるため省く。
const INPUT_PLACEHOLDER = TOUCH_INPUT
  ? 'かぐやに話しかける'
  : 'かぐやに話しかける（Enterで送信・Shift+Enterで改行）';

// 追加で聞きたいときの定型ボタン。候補を作るための追加のLLM呼び出しはしない。
const FOLLOW_UPS = ['もっと詳しく', '例をあげて', '短くまとめて'];

// 書きかけは端末内（localStorage）だけに置く。送信が受理されるまで消さないので、
// 送信前にSafariがタブを捨てても、開き直せば書きかけがそのまま戻る。
function saveDraft(turnId?: string): void {
  const text = inputEl.value;
  try {
    if (!text && !turnId) localStorage.removeItem(DRAFT_KEY);
    else localStorage.setItem(DRAFT_KEY, JSON.stringify({ text, turnId }));
  } catch { /* optional storage */ }
}

function restoreDraft(): void {
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
    if (typeof draft?.text === 'string' && !inputEl.value) inputEl.value = draft.text.slice(0, 2000);
  } catch { /* invalid/disabled storage */ }
}

// 送信が受理された時点で、その送信ぶんの書きかけだけを消す。受理前に書き始めた
// 次の文章は消さないよう、turn_idが一致するときに限る。
function settleDraft(turnId: string): void {
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
    if (!draft || draft.turnId !== turnId) return;
    if (inputEl.value === draft.text) inputEl.value = '';
    localStorage.removeItem(DRAFT_KEY);
  } catch { /* optional storage */ }
}

restoreDraft();
inputEl.addEventListener('input', () => saveDraft());
// 入力中はキャラクターを小さな顔だけの表示にして、会話履歴へ場所を渡す。
function setInputFocused(focused: boolean): void {
  document.body.classList.toggle('input-focused', focused);
  avatar.setFaceMode(focused && !document.getElementById('app')!.classList.contains('mode-mini'));
}

inputEl.addEventListener('focus', () => setInputFocused(true));
inputEl.addEventListener('blur', () => setInputFocused(false));

// 接続状態は常設の小さな表示。実際に起きていることだけを書く
// （検索していないのに「記憶を探しているよ」のような演出はしない）。
function connectionStatus(text: string): void {
  document.getElementById('connection-status')!.textContent = text;
}

const PHASE_LABELS: Record<string, string> = {
  preparing: '接続中', generating: '回答を生成中', saving: '保存中',
};

function chatStatus(phase: string | null): void {
  document.getElementById('chat-status')!.textContent = phase ? PHASE_LABELS[phase] ?? '' : '';
}

// ストリーミング中の本文差し替え。過去を読んでいる間は勝手に最下部へ動かさない。
function applyPartial(turnId: string, text: string): void {
  const el = answerElements.get(turnId);
  if (!el || !text) return;
  const follow = nearLatest();
  el.textContent = text;
  if (follow) scrollLatest();
  else document.getElementById('latest-btn')!.hidden = false;
}

function nearLatest(): boolean {
  return historyEl.scrollHeight - historyEl.scrollTop - historyEl.clientHeight < 64;
}

function scrollLatest(): void {
  historyEl.scrollTop = historyEl.scrollHeight;
  document.getElementById('latest-btn')!.hidden = true;
}

document.getElementById('latest-btn')!.addEventListener('click', scrollLatest);
historyEl.addEventListener('scroll', () => {
  if (nearLatest()) document.getElementById('latest-btn')!.hidden = true;
});


// crypto.randomUUID() は Secure Context 限定で、LAN内HTTPで開くiPhone Safariでは
// 利用できない場合がある。getRandomValues() を使ってRFC 4122 v4 UUIDを生成する。
// iOS Safariではソフトウェアキーボード表示時にレイアウトviewportと
// 実際に見えている領域(Visual Viewport)の高さがずれることがある。
// CSSへ現在の可視高さを渡し、入力欄がキーボードの裏へ隠れるのを防ぐ。
function setupViewportHeight(): void {
  let largestViewportHeight = window.visualViewport?.height ?? window.innerHeight;

  const update = () => {
    const viewport = window.visualViewport;
    const height = viewport?.height ?? window.innerHeight;
    const offsetTop = viewport?.offsetTop ?? 0;
    const offsetLeft = viewport?.offsetLeft ?? 0;

    largestViewportHeight = Math.max(largestViewportHeight, height);
    document.documentElement.style.setProperty('--app-height', `${Math.round(height)}px`);
    document.documentElement.style.setProperty('--app-top', `${Math.round(offsetTop)}px`);
    document.documentElement.style.setProperty('--app-left', `${Math.round(offsetLeft)}px`);

    // iOS Safariはキーボード表示時、Visual Viewport自体を上方向へパンする。
    // heightだけでなくoffsetTop/offsetLeftもCSSへ渡し、UIを「今見えている領域」へ固定する。
    const keyboardOpen = height < largestViewportHeight * 0.78;
    document.body.classList.toggle('keyboard-open', keyboardOpen);
  };

  update();
  window.addEventListener('resize', update);
  window.addEventListener('orientationchange', () => {
    largestViewportHeight = window.visualViewport?.height ?? window.innerHeight;
    window.setTimeout(update, 50);
  });
  window.visualViewport?.addEventListener('resize', update);
  window.visualViewport?.addEventListener('scroll', update);
}

function createTurnId(): string {
  if (typeof crypto.randomUUID === 'function') {
    try {
      return crypto.randomUUID();
    } catch {
      // HTTPなどでrandomUUIDが拒否された場合は下のフォールバックを使う。
    }
  }

  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10, 16).join('')}`;
}

// キャラクターの表情は、ここにある状態から毎回一意に決まる。
const TALKING_MS = 6000;
const SLEEP_AFTER_MS = 10 * 60 * 1000;
let quietMode = false;
let organizing = false;
let talkingUntil = 0;
let talkingTimer: number | null = null;
// 返事（talking）と自発的な声かけ（greeting）で表情を分ける。
let talkingState: 'talking' | 'greeting' = 'talking';
let lastConversation = Date.now();

function refreshAvatar(): void {
  const now = Date.now();
  if (talkingTimer !== null) window.clearTimeout(talkingTimer);
  talkingTimer = now < talkingUntil ? window.setTimeout(refreshAvatar, talkingUntil - now) : null;
  const state = busy ? 'thinking' : organizing ? 'organizing' : now < talkingUntil ? talkingState
    : quietMode || now - lastConversation > SLEEP_AFTER_MS ? 'sleeping' : 'idle';
  avatar.setState(state, quietMode);
}

async function api(path: string, init: RequestInit = {}): Promise<any> {
  const request = async () => {
    const current = await ensureSession();
    return fetch(API_BASE + path, { ...init, headers: {
      'Content-Type': 'application/json', Authorization: `Bearer ${current.sessionToken}`, ...init.headers,
    }, signal: init.signal ?? AbortSignal.timeout(15000) });
  };
  let response = await request();
  if (response.status === 401) { invalidateSession(); response = await request(); }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : '操作できませんでした。接続・入力を確認してください。');
  }
  return response.json();
}

function loadSession(): StoredSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as StoredSession) : null;
  } catch {
    return null;
  }
}

function saveSession(value: StoredSession): void {
  session = value;
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
  } catch {
    // sessionStorage may be unavailable; the tab still works for this load.
  }
  try { localStorage.setItem(CLIENT_KEY, value.clientId); } catch { /* optional persistence */ }
}

async function ensureSession(): Promise<StoredSession> {
  if (session?.sessionToken) {
    saveSession(session);
    return session;
  }
  let clientId = session?.clientId;
  try {
    clientId ??= localStorage.getItem(CLIENT_KEY) ?? undefined;
  } catch { /* The current session still works without local storage. */ }
  const res = await fetch(`${API_BASE}/session`, {
    signal: AbortSignal.timeout(15000),
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: clientId }),
  });
  if (!res.ok) throw new Error('session_failed');
  const body = (await res.json()) as { client_id: string; session_token: string };
  const value = { clientId: body.client_id, sessionToken: body.session_token };
  saveSession(value);
  return value;
}

function invalidateSession(): void {
  if (session) saveSession({ ...session, sessionToken: '' });
}

function appendMessage(role: 'user' | 'assistant', text: string, opts?: { pending?: boolean; id?: string }): HTMLDivElement {
  const el = document.createElement('div');
  el.className = `msg ${role}${opts?.pending ? ' pending' : ''}`;
  el.textContent = text;
  if (opts?.id) el.dataset.turnId = opts.id;
  historyEl.appendChild(el);
  return el;
}

// 実測した所要時間だけを出す。サーバーが測っていない回（再接続後の復元など）
// では何も出さない。
function appendTiming(turn: HistoryTurn): void {
  if (!turn.elapsed_ms) return;
  const seconds = (value: number) => `${(value / 1000).toFixed(1)}秒`;
  const el = document.createElement('div');
  el.className = 'msg-timing';
  el.textContent = turn.first_text_ms
    ? `書き始めまで ${seconds(turn.first_text_ms)} ／ 全体 ${seconds(turn.elapsed_ms)}`
    : `全体 ${seconds(turn.elapsed_ms)}`;
  historyEl.appendChild(el);
}

// 返答が何を参照したかは、実際に渡した記憶があるときだけ出す。
function appendReferences(turn: HistoryTurn): void {
  if (!turn.references?.length) return;
  const box = document.createElement('details');
  box.className = 'msg-references';
  const title = document.createElement('summary');
  title.textContent = `参照した記憶 ${turn.references.length}件`;
  box.appendChild(title);
  for (const item of turn.references) {
    const row = document.createElement('div');
    row.textContent = `${item.label}：${item.text}`;
    box.appendChild(row);
  }
  historyEl.appendChild(box);
}

// 最後の返答にだけ定型の追いかけ質問を出す。押すと通常の会話として送るだけで、
// 候補を作るための追加のLLM呼び出しはしない。
function appendFollowUps(turn: HistoryTurn, isLast: boolean): void {
  if (!isLast || !turn.answer || turn.status !== 'completed') return;
  const row = document.createElement('div');
  row.className = 'follow-ups';
  for (const label of FOLLOW_UPS) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.disabled = busy || !synchronized || !!unsavedTurnId;
    button.addEventListener('click', () => sendTurn(label, createTurnId(), false));
    row.appendChild(button);
  }
  historyEl.appendChild(row);
}

function renderHistoryTurn(turn: HistoryTurn, isLast: boolean): void {
  const userEl = appendMessage('user', turn.text, { id: turn.turn_id });
  if (turn.answer) {
    answerElements.set(turn.turn_id, appendMessage('assistant', turn.answer, { id: turn.turn_id, pending: turn.status === 'pending' }));
    appendTiming(turn);
    appendReferences(turn);
    appendFollowUps(turn, isLast);
  } else if (turn.status === 'pending') {
    answerElements.set(turn.turn_id, appendMessage('assistant', turn.partial || '……考え中……', { id: turn.turn_id, pending: true }));
  } else if (['failed', 'cancelled'].includes(turn.status)) {
    if (turn.partial) appendMessage('assistant', `${turn.partial}\n（途中の返答・未保存）`, { id: turn.turn_id, pending: true });
    const label = document.createElement('div');
    label.textContent = '前回の会話は完了しませんでした。';
    userEl.appendChild(label);
    if (turn.client_id === session?.clientId) {
      const retryBtn = document.createElement('button');
      retryBtn.textContent = '再送する';
      retryBtn.disabled = busy || !synchronized || !!unsavedTurnId;
      retryBtn.addEventListener('click', () => sendTurn(turn.text, turn.turn_id, true));
      userEl.appendChild(retryBtn);
    }
  }
}

function renderTurns(forceLatest = false): void {
  const follow = forceLatest || nearLatest();
  const oldTop = historyEl.scrollTop;
  historyEl.innerHTML = '';
  answerElements.clear();
  const lastId = [...turns.keys()].at(-1);
  for (const turn of turns.values()) renderHistoryTurn(turn, turn.turn_id === lastId);
  if (follow) scrollLatest();
  else {
    historyEl.scrollTop = oldTop;
    document.getElementById('latest-btn')!.hidden = false;
  }
}

async function loadHistory(older = false): Promise<void> {
  if (older && (!nextCursor || historyLoading || !synchronized)) return;
  const sourceSocket = ws;
  historyLoading = true;
  olderBtn.disabled = true;
  try {
    // 初回は少なく読み、遡るときだけまとめて読む。iPhoneでの起動を軽くする。
    const params = new URLSearchParams({ limit: older ? '50' : '20' });
    if (older && nextCursor) params.set('cursor', nextCursor);
    const request = async () => {
      const s = await ensureSession();
      return fetch(`${API_BASE}/history?${params}`, {
        signal: AbortSignal.timeout(15000),
        headers: { Authorization: `Bearer ${s.sessionToken}` },
      });
    };
    let res = await request();
    if (res.status === 401) {
      invalidateSession();
      res = await request();
    }
    if (!res.ok) {
      throw new Error('履歴を取得できませんでした。');
    }
    const body = (await res.json()) as { items: HistoryTurn[]; next_cursor: string | null };
    if (sourceSocket !== ws) return;
    const page = new Map(body.items.map(turn => [turn.turn_id, turn]));
    const oldHeight = historyEl.scrollHeight;
    const oldTop = historyEl.scrollTop;
    const previous = turns;
    turns = older ? new Map([...page, ...turns]) : page;
    // 再接続後や別端末から見たときも、送信済みの書きかけはここで消える。
    for (const [id, turn] of turns) {
      const before = previous.get(id);
      if (before?.references) turn.references = before.references;
      if (before?.elapsed_ms) { turn.elapsed_ms = before.elapsed_ms; turn.first_text_ms = before.first_text_ms; }
      if (before?.partial && !turn.answer) turn.partial = before.partial;
      if (turn.status === 'pending' || turn.status === 'completed') settleDraft(id);
    }
    nextCursor = body.next_cursor;
    olderBtn.hidden = !nextCursor;
    renderTurns(!older && previous.size === 0);
    if (older) historyEl.scrollTop = oldTop + historyEl.scrollHeight - oldHeight;
  } finally {
    historyLoading = false;
    olderBtn.disabled = false;
  }
}

function showError(message: string, retryTurnId: string | null, retryAfter?: number | null): void {
  errorEl.hidden = false;
  errorEl.innerHTML = '';
  const text = document.createElement('div');
  text.textContent = retryAfter ? `${message}（約${retryAfter}秒後に再試行できます）` : message;
  errorEl.appendChild(text);
  if (retryTurnId) {
    const btn = document.createElement('button');
    btn.textContent = '再送する';
    btn.addEventListener('click', () => {
      hideError();
      if (pending && pending.turnId === retryTurnId) {
        sendTurn(pending.text, retryTurnId, true);
      }
    });
    errorEl.appendChild(btn);
  }
}

function hideError(): void {
  errorEl.hidden = true;
  errorEl.innerHTML = '';
}

function setBusy(value: boolean): void {
  busy = value;
  document.body.classList.toggle('chat-busy', busy);
  sendBtn.disabled = busy || voiceActive || !synchronized || !!unsavedTurnId;
  historyEl.querySelectorAll<HTMLButtonElement>('button').forEach(button => {
    button.disabled = busy || voiceActive || !synchronized || !!unsavedTurnId;
  });
  refreshAvatar();
  // PCでは返答後に入力へ戻す。iPhone等のタッチ端末では、ユーザーが閉じた
  // ソフトウェアキーボードを勝手に再表示しない。
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  if (!document.getElementById('chat')!.hidden && (isTauri() || finePointer)) inputEl.focus();
  (document.getElementById('cancel-btn') as HTMLButtonElement).disabled = !busy || !activeTurnId ||
    turns.get(activeTurnId)?.client_id !== session?.clientId;
}

function markPendingSettled(turnId: string, answerText: string | null): void {
  const turn = turns.get(turnId);
  if (turn) {
    turns.set(turnId, { ...turn, answer: answerText, status: answerText ? 'completed' : 'failed' });
    renderTurns();
  }
  // 簡易版では会話履歴を表示していないため、回答をキャラ上の吹き出しで
  // 一時的に見せる（自発的な声かけと同じ吹き出し要素を流用）。
  if (answerText && document.getElementById('app')!.classList.contains('mode-mini')) {
    showMiniReply(answerText);
  }
}

let miniReplyTimer: number | null = null;
let reminderId: string | null = null;
document.getElementById('proactive-bubble')!.addEventListener('click', () => {
  if (reminderId) {
    const id = reminderId;
    api(`/reminders/${encodeURIComponent(id)}/ack`, { method: 'POST' }).then(() => {
      if (reminderId === id) { reminderId = null; hideBubble(); }
    }).catch(() => showError('通知の確認を保存できませんでした。もう一度クリックしてください。', null));
    return;
  }
  hideBubble();
});

function hideBubble(): void {
  if (reminderId) return;
  if (miniReplyTimer !== null) { window.clearTimeout(miniReplyTimer); miniReplyTimer = null; }
  document.getElementById('proactive-bubble')!.hidden = true;
}

function showMiniReply(text: string): void {
  if (reminderId) return;
  const bubble = document.getElementById('proactive-bubble')!;
  bubble.textContent = text;
  bubble.hidden = false;
  if (miniReplyTimer !== null) window.clearTimeout(miniReplyTimer);
  miniReplyTimer = window.setTimeout(() => { bubble.hidden = true; }, 8000);
}

// 声かけを出してよい状態か。Tauriでは最小化・別デスクトップまで見るため
// desktop_visible を使い、iPhone/ブラウザではタブの表示状態で代用する。
async function characterVisible(): Promise<boolean> {
  if (document.getElementById('character-view')!.hidden) return false;
  if (!isTauri()) return document.visibilityState === 'visible';
  return invoke<boolean>('desktop_visible').catch(() => false);
}

async function connectWs(): Promise<void> {
  if (isTauri()) await waitForBackend(20000, 500);
  const s = await ensureSession();
  connectionStatus('接続中…');
  const socket = new WebSocket(`${WS_BASE}/ws?token=${encodeURIComponent(s.sessionToken)}`);
  synchronized = false;
  ws = socket;
  const queued: Record<string, unknown>[] = [];
  let syncing = false;

  socket.addEventListener('message', (event: MessageEvent<string>) => {
    if (ws !== socket) return;
    const data = JSON.parse(event.data);
    if (synchronized) {
      handleServerEvent(data);
      return;
    }
    queued.push(data);
    if (syncing) return;
    syncing = true;
    // Subscribe before fetching history; replay events received during the fetch.
    loadHistory().then(() => {
      if (ws !== socket) return;
      synchronized = true;
      unsavedTurnId = null;
      reconnectDelay = 1000;
      connectionStatus('接続済み');
      hideError();
      controls?.refreshSettings().catch(() => showError('設定を取得できませんでした。設定画面から再試行してください。', null));
      for (const event of queued) handleServerEvent(event);
      if (pending && !turns.has(pending.turnId)) {
        const unsent = pending;
        turns.set(unsent.turnId, { turn_id: unsent.turnId, text: unsent.text, answer: null,
          status: 'failed', client_id: s.clientId });
        renderTurns();
      }
    }).catch(() => {
      showError('履歴を取得できませんでした。再接続します。', null);
      socket.close();
    });
  });

  socket.addEventListener('close', (event) => {
    if (ws !== socket) return;
    ws = null;
    synchronized = false;
    setBusy(false);
    chatStatus(null);
    connectionStatus('未接続（再接続します）');
    if (event.code === 4401) invalidateSession();
    showError('サーバーとの接続が切れました。再接続します。', null);
    scheduleReconnect();
  });

  socket.addEventListener('error', () => {
    socket.close();
  });
}

function scheduleReconnect(): void {
  window.setTimeout(() => {
    connectWs().catch((error: unknown) => {
      showError(error instanceof Error ? error.message : String(error), null);
      scheduleReconnect();
    });
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 15000);
}

function handleServerEvent(data: Record<string, unknown>): void {
  const type = data.type as string;
  switch (type) {
    case 'pc.open': {
      // A chat request should only move the tab on the device that sent it.
      // Other connected devices still receive the broadcast but ignore it.
      const target = typeof data.target_client_id === 'string' ? data.target_client_id : '';
      if (target && session?.clientId !== target) return;
      controls?.open('pc');
      window.dispatchEvent(new CustomEvent('pc.open', { detail: data }));
      return;
    }
    case 'state.changed': {
      // 別の端末で話していた時間も「会っていた」に数える。この端末を開いた直後に
      // 「ちょっと寝てた」と言わないよう、サーバの最終会話時刻で揃える。
      if (typeof data.last_activity === 'string') {
        const at = Date.parse(data.last_activity);
        if (Number.isFinite(at)) {
          window.dispatchEvent(new CustomEvent('kaguya-served', { detail: { at, counted: false } }));
        }
      }
      activeTurnId = (data.turn_id as string) ?? null;
      chatStatus(data.state === 'thinking' ? (data.phase as string) ?? null : null);
      // 別端末から始まった会話でも、進行中の本文・参照をこの端末へ復元する。
      if (activeTurnId) {
        const known = turns.get(activeTurnId);
        const turn: HistoryTurn = known ?? { turn_id: activeTurnId, text: (data.text as string) ?? '',
          answer: null, status: 'pending', client_id: (data.client_id as string) ?? '' };
        turn.partial = (data.partial as string) || turn.partial;
        const references = data.references as HistoryTurn['references'];
        if (references?.length) turn.references = references;
        if (!known) { turns.set(activeTurnId, turn); renderTurns(); }
        else applyPartial(activeTurnId, turn.partial || '');
      }
      setBusy(data.state === 'thinking');
      break;
    }
    case 'chat.progress': {
      const turnId = data.turn_id as string;
      const turn = turns.get(turnId);
      if (turn) turn.partial = data.partial as string;
      applyPartial(turnId, data.partial as string);
      break;
    }
    case 'proactive.message': {
      characterVisible().then(visible => {
        if (!visible || reminderId) return;
        const bubble = document.getElementById('proactive-bubble')!;
        bubble.textContent = data.text as string; bubble.hidden = false;
        talkingState = 'greeting';
        talkingUntil = Date.now() + TALKING_MS;
        refreshAvatar();
      });
      break;
    }
    case 'mood.changed':
      // 表情の判定はサーバ側にある。ここは受け取ってlivingへ渡すだけ。
      window.dispatchEvent(new CustomEvent('kaguya-mood', { detail: { mood: data.mood } }));
      break;
    case 'settings.changed':
      controls?.applyOptions(data.options as Record<string, any>).catch(() => showError('表示設定を反映できませんでした。', null));
      break;
    case 'reminder.due': {
      if (typeof data.id !== 'string') break;
      const repeated = reminderId === data.id;
      reminderId = data.id;
      const bubble = document.getElementById('proactive-bubble')!;
      bubble.textContent = data.text as string;
      bubble.hidden = false;
      if (miniReplyTimer !== null) window.clearTimeout(miniReplyTimer);
      // 声かけと違い自動では消さない。クリックで閉じるまで残す。
      miniReplyTimer = null;
      if (repeated) break;
      talkingState = 'talking';
      talkingUntil = Date.now() + TALKING_MS;
      refreshAvatar();
      if (isTauri()) invoke('show_window').catch(() => {});
      break;
    }
    case 'reminder.ack':
      if (reminderId === data.id) { reminderId = null; hideBubble(); }
      break;
    case 'jobs.changed':
      organizing = data.running === true;
      refreshAvatar();
      controls?.refreshSettings().catch(() => {});
      break;
    case 'memories.changed':
      pending = null;
      turns.clear();
      loadHistory().catch(() => showError('記憶変更後の履歴を取得できませんでした。', null));
      break;
    case 'chat.accepted':
      turns.set(data.turn_id as string, { turn_id: data.turn_id as string,
        text: data.text as string, answer: null, status: 'pending', client_id: data.client_id as string });
      settleDraft(data.turn_id as string);
      renderTurns();
      break;
    case 'chat.completed': {
      const turnId = data.turn_id as string;
      if (unsavedTurnId === turnId) unsavedTurnId = null;
      if (!turns.has(turnId)) {
        turns.set(turnId, { turn_id: turnId, text: data.text as string, answer: null,
          status: 'pending', client_id: '' });
      }
      const completed = turns.get(turnId)!;
      const references = data.references as HistoryTurn['references'];
      if (references?.length) completed.references = references;
      completed.elapsed_ms = data.elapsed_ms as number | undefined;
      completed.first_text_ms = data.first_text_ms as number | undefined;
      settleDraft(turnId);
      markPendingSettled(turnId, data.answer as string);
      chatStatus(null);
      if (pending?.turnId === turnId) pending = null;
      lastConversation = Date.now();
      // どの端末から送られた会話でも、利用時間の学習を全端末で同じだけ進める。
      window.dispatchEvent(new CustomEvent('kaguya-served',
        { detail: { at: lastConversation, counted: true } }));
      talkingState = 'talking';
      talkingUntil = lastConversation + TALKING_MS;
      setBusy(false);
      hideError();
      break;
    }
    case 'chat.error': {
      const turnId = (data.turn_id as string) ?? null;
      const code = data.code as string;
      const message = data.message as string;
      // 操作拒否は進行中の会話の失敗ではない。
      if (['saving', 'not_owner', 'busy', 'turn_conflict'].includes(code)) {
        if (turnId && turnId !== activeTurnId) markPendingSettled(turnId, null);
        if (!activeTurnId) chatStatus(null);
        setBusy(activeTurnId !== null);
        showError(message, null);
        return;
      }
      setBusy(false);
      chatStatus(null);
      if (code === 'save_failed') {
        // Keep the pending bubble; only a save retry is offered, per spec.
        showSaveRetry(turnId!, data.text as string, data.answer as string);
      } else {
        if (turnId) markPendingSettled(turnId, null);
        showError(message, ['rate_limit', 'timeout', 'network_error', 'api_error', 'internal_error'].includes(code) && turnId ? turnId : null,
          data.retry_after as number | null | undefined);
      }
      break;
    }
    default:
      break;
  }
}

function showSaveRetry(turnId: string, turnText: string, answer: string): void {
  unsavedTurnId = turnId;
  turns.set(turnId, { turn_id: turnId, text: turnText, answer, status: 'pending',
    client_id: turns.get(turnId)?.client_id ?? '' });
  renderTurns();
  setBusy(false);
  errorEl.hidden = false;
  errorEl.innerHTML = '';
  const text = document.createElement('div');
  text.textContent = '回答を保存できませんでした。';
  errorEl.appendChild(text);
  const btn = document.createElement('button');
  btn.textContent = '保存のみ再試行する';
  btn.addEventListener('click', () => {
    if (!ws || ws.readyState !== WebSocket.OPEN || !synchronized) return;
    hideError();
    ws?.send(JSON.stringify({ type: 'chat.retry_save', turn_id: turnId }));
  });
  errorEl.appendChild(btn);
}

function sendTurn(text: string, turnId: string, retry: boolean): void {
  if (busy || unsavedTurnId) {
    showError(busy ? '返事を待ってね。書きかけの文章はそのまま残しておくね。' : '先に回答の保存を完了してください。', null);
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN || !synchronized) {
    showError('サーバーに接続できません。', null);
    return;
  }
  pending = { turnId, text, retry };
  lastConversation = Date.now();
  if (drafts[drafts.length - 1] !== text) drafts.push(text);
  draftIndex = drafts.length;
  hideBubble();
  turns.set(turnId, { turn_id: turnId, text, answer: null, status: 'pending', client_id: session!.clientId });
  renderTurns();
  setBusy(true);
  hideError();
  ws.send(JSON.stringify({ type: 'chat.send', turn_id: turnId, text, retry }));
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  const turnId = createTurnId();
  // 端末内の下書きは送信が受理されるまで消さない。受理はサーバーのchat.accepted
  // （または履歴の取得）で確認し、そこで初めて入力欄と保存分を消す。
  saveDraft(turnId);
  sendTurn(text, turnId, false);
  if (pending?.turnId === turnId) inputEl.value = '';
});

// 送信した文章の履歴。言い直し・再送のときに↑で呼び戻せるようにする。
const drafts: string[] = [];
let draftIndex = 0;

inputEl.addEventListener('keydown', (event) => {
  // isComposing: IMEの変換確定Enterを送信と誤認しないための判定。
  // タッチ端末（iPhone）では改行キーは改行のまま。送信は送信ボタンだけ。
  if (!TOUCH_INPUT && event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
    return;
  }
  if (event.isComposing || !drafts.length) return;
  const atStart = inputEl.selectionStart === 0 && inputEl.selectionEnd === 0;
  const atEnd = inputEl.selectionStart === inputEl.value.length && inputEl.selectionEnd === inputEl.value.length;
  // 複数行の編集を妨げないよう、カーソルが端にあるときだけ履歴をたどる。
  if (event.key === 'ArrowUp' && atStart && draftIndex > 0) {
    event.preventDefault();
    inputEl.value = drafts[--draftIndex];
  } else if (event.key === 'ArrowDown' && atEnd && draftIndex < drafts.length) {
    event.preventDefault();
    draftIndex += 1;
    inputEl.value = drafts[draftIndex] ?? '';
  }
});

olderBtn.addEventListener('click', () => {
  loadHistory(true).catch(() => showError('過去の履歴を取得できませんでした。', null));
});

document.getElementById('cancel-btn')!.addEventListener('click', () => {
  if (ws?.readyState === WebSocket.OPEN && activeTurnId) {
    ws.send(JSON.stringify({ type: 'chat.cancel', turn_id: activeTurnId }));
  }
});

async function startChat(): Promise<void> {
  try {
    await connectWs();
  } catch (error) {
    showError(error instanceof Error ? error.message : String(error), null);
    scheduleReconnect();
  }
}

// In Tauri, the backend is spawned as our own child process by lib.rs and
// may not have finished starting yet. Rather than listen for a Tauri event
// (which races listener registration against emit), poll /health directly
// with simple retries — plain, deterministic, no missed-event window.
async function waitForBackend(maxWaitMs: number, intervalMs: number): Promise<boolean> {
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    const instance = await invoke<string>('backend_status');
    try {
      const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(1500) });
      if (res.ok) {
        const health = await res.json();
        if (health.backend_instance !== instance) {
          throw new Error('ポート8765は別のプロセスに使用されています。既存のアプリを終了してから起動し直してください。');
        }
        await invoke<string>('backend_status');
        return true;
      }
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('ポート8765')) throw error;
      // backend not up yet; retry
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('サーバーの起動を確認できませんでした。アプリを起動し直してください。');
}

async function main(): Promise<void> {
  setupViewportHeight();
  inputEl.placeholder = INPUT_PLACEHOLDER;
  connectionStatus('接続中…');
  controls = new Controls(api, options => {
    quietMode = options.quiet === true;
    refreshAvatar();
  });
  new PCPanel(api, API_BASE);
  new VoiceChat(API_BASE, ensureSession, active => {
    voiceActive = active;
    setBusy(busy);
  });
  setBusy(false);

  setupAvatarContextMenu();
  document.getElementById('mini-toggle-btn')?.addEventListener('click', () => {
    setMiniMode(true).catch(() => showError('簡易表示に切り替えられませんでした。', null));
  });
  // Windowsログイン時の自動起動タスクは `app.exe --mini` で呼ばれる想定。
  // その場合は前回の表示モードに関わらず必ず簡易表示から始める。
  // 簡易表示はデスクトップ(Tauri)専用。iPhone/Safariでは通常UIに固定する。
  let restoredMini = false;
  if (isTauri()) {
    const startMini = await invoke<boolean>('start_mini').catch(() => false);
    restoredMini = startMini;
    if (!startMini) {
      try { restoredMini = localStorage.getItem(MINI_MODE_KEY) === '1'; } catch { /* default: normal */ }
    }
  }
  if (restoredMini) await setMiniMode(true);
  else if (!isTauri()) await setMiniMode(false);

  window.setInterval(() => {
    if (ws?.readyState !== WebSocket.OPEN) return;
    refreshAvatar();
    characterVisible().then(visible => {
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'presence', visible }));
      if (!visible) document.getElementById('proactive-bubble')!.hidden = true;
    });
  }, 5000);
  await startChat();
}

main().catch(() => showError('サーバーに接続できません。', null));
