type LifeMood = 'normal' | 'happy' | 'sleepy' | 'sulky';
type LifeActivity = 'idle' | 'reading' | 'working' | 'playing' | 'snacking' | 'daydreaming' | 'sleeping';

type LifeState = {
  lastSeen: number;
  interactions: number;
  affection: number;
  hourCounts: number[];
};

const LIFE_KEY = 'kaguya.life.v1';
const MINUTE = 60 * 1000;

function fresh(): LifeState {
  return { lastSeen: Date.now(), interactions: 0, affection: 50,
    hourCounts: Array.from({ length: 24 }, () => 0) };
}

function load(): LifeState {
  try {
    const parsed = JSON.parse(localStorage.getItem(LIFE_KEY) || 'null') as Partial<LifeState> | null;
    if (!parsed) return fresh();
    return {
      lastSeen: Number(parsed.lastSeen) || Date.now(),
      interactions: Math.max(0, Number(parsed.interactions) || 0),
      affection: Math.min(100, Math.max(0, Number(parsed.affection) || 50)),
      hourCounts: Array.isArray(parsed.hourCounts) && parsed.hourCounts.length === 24
        ? parsed.hourCounts.map(value => Math.max(0, Number(value) || 0)) : fresh().hourCounts,
    };
  } catch {
    return fresh();
  }
}

let state = load();
let reentryTimer: number | null = null;
// 表情はサーバが決める。かぐやはPC上のFastAPIに1人しかいないので、
// PCとiPhoneで別々に判定すると顔が食い違う。届くまでは時刻だけで暫定表示する。
let serverMood: LifeMood | null = null;

function save(): void {
  try { localStorage.setItem(LIFE_KEY, JSON.stringify(state)); } catch { /* best effort */ }
}

function preferredHours(): Set<number> {
  const ranked = state.hourCounts
    .map((count, hour) => ({ count, hour }))
    .filter(row => row.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 3);
  return new Set(ranked.map(row => row.hour));
}

function energy(now: Date): number {
  const hour = now.getHours();
  let value = hour < 6 ? 18 : hour < 10 ? 55 : hour < 18 ? 78 : hour < 23 ? 62 : 35;
  if (preferredHours().has(hour)) value += 12;
  return Math.max(0, Math.min(100, value));
}

function deterministicChoice<T>(items: readonly T[], now: Date): T {
  const seed = now.getDate() * 31 + now.getHours() * 7 + state.interactions;
  return items[Math.abs(seed) % items.length];
}

function activity(now: Date, idleMs: number): LifeActivity {
  const hour = now.getHours();
  const learnedAwake = preferredHours().has(hour);
  if (hour < 6 && idleMs > 25 * MINUTE && !learnedAwake) return 'sleeping';
  if (idleMs < 3 * MINUTE) return 'idle';
  if (idleMs > 35 * MINUTE) {
    return deterministicChoice(['reading', 'playing', 'snacking', 'daydreaming'] as const, now);
  }
  return deterministicChoice(['reading', 'working', 'playing', 'daydreaming'] as const, now);
}

function effectiveMood(now = Date.now()): LifeMood {
  if (serverMood) return serverMood;
  const hour = new Date(now).getHours();
  return hour < 6 || hour >= 23 ? 'sleepy' : 'normal';
}

function reentryLine(activityNow: LifeActivity): string | null {
  const lines: Partial<Record<LifeActivity, string>> = {
    reading: 'あ、おかえり。ちょうど本読んでた。',
    working: 'おかえり。ちょっと作業してたとこ。',
    playing: 'あ、来た。暇だったから遊んでた。',
    snacking: 'おかえり。……今おやつ食べてた。',
    daydreaming: 'あ、おかえり。ちょっとぼーっとしてた。',
    sleeping: 'ん……おかえり。ちょっと寝てた。',
  };
  return lines[activityNow] ?? null;
}

function showReentry(activityNow: LifeActivity): void {
  const bubble = document.getElementById('proactive-bubble') as HTMLDivElement | null;
  const text = reentryLine(activityNow);
  if (!bubble || !text || !bubble.hidden) return;
  bubble.textContent = text;
  bubble.hidden = false;
  if (reentryTimer !== null) window.clearTimeout(reentryTimer);
  reentryTimer = window.setTimeout(() => {
    if (bubble.textContent === text) bubble.hidden = true;
  }, 5000);
}

function emit(reentry = false): void {
  const now = new Date();
  const idleMs = Math.max(0, now.getTime() - state.lastSeen);
  const activityNow = activity(now, idleMs);
  window.dispatchEvent(new CustomEvent('kaguya-life', { detail: {
    mood: effectiveMood(now.getTime()),
    activity: activityNow,
    energy: energy(now),
    affection: state.affection,
    reentry,
    learnedHours: [...preferredHours()],
  } }));
  if (reentry && idleMs > 15 * MINUTE) showReentry(activityNow);
}

function reactToText(text: string): void {
  const value = text.trim();
  if (!value) return;
  const now = Date.now();
  state.interactions += 1;
  state.hourCounts[new Date(now).getHours()] += 1;
  state.lastSeen = now;
  if (/(かわいい|好き|ありがとう|助かった|えらい|いい子)/.test(value)) {
    state.affection = Math.min(100, state.affection + 1);
  }

  save();
  emit(false);
}

window.addEventListener('kaguya-mood', event => {
  const value = String((event as CustomEvent).detail?.mood ?? '');
  if (!['normal', 'happy', 'sleepy', 'sulky'].includes(value) || value === serverMood) return;
  serverMood = value as LifeMood;
  emit(false);
});

const form = document.getElementById('input-form');
form?.addEventListener('submit', () => {
  const input = document.getElementById('text-input') as HTMLTextAreaElement | null;
  reactToText(input?.value ?? '');
}, true);

function markHidden(): void {
  state.lastSeen = Date.now();
  save();
}

function resume(): void {
  const gap = Date.now() - state.lastSeen;
  emit(gap > 2 * MINUTE);
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') markHidden();
  else resume();
});
window.addEventListener('pagehide', markHidden);
window.addEventListener('pageshow', resume);
window.setInterval(() => {
  if (document.visibilityState === 'visible') emit(false);
}, 60_000);

resume();
