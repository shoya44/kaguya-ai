type LifeMood = 'normal' | 'happy' | 'sleepy' | 'sulky' | 'worried' | 'bored';
type LifeActivity = 'idle' | 'reading' | 'working' | 'playing' | 'snacking' | 'daydreaming' | 'sleeping';

// backend/app/mood.py の FACES、backend/app/living.py の ACTIVITIES と同じ並び。
const MOODS: readonly LifeMood[] = ['normal', 'happy', 'sleepy', 'sulky', 'worried', 'bored'];
const ACTIVITIES: readonly LifeActivity[] = ['idle', 'reading', 'working', 'playing',
  'snacking', 'daydreaming', 'sleeping'];

const MINUTE = 60 * 1000;

// 表情も活動も元気さもサーバが決める。かぐやはPC上のFastAPIに1人しかいないので、
// 端末ごとに判定すると、PCとiPhoneで別の顔・別の行動になってしまう。
// この画面が持つのは「いつ画面を見たか」だけ。
let mood: LifeMood = 'normal';
let activity: LifeActivity = 'idle';
let energy = 60;
let lastSeen = Date.now();
let reentryTimer: number | null = null;

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

function showReentry(): void {
  const bubble = document.getElementById('proactive-bubble') as HTMLDivElement | null;
  const text = reentryLine(activity);
  if (!bubble || !text || !bubble.hidden) return;
  bubble.textContent = text;
  bubble.hidden = false;
  if (reentryTimer !== null) window.clearTimeout(reentryTimer);
  reentryTimer = window.setTimeout(() => {
    if (bubble.textContent === text) bubble.hidden = true;
  }, 5000);
}

function emit(reentry = false): void {
  window.dispatchEvent(new CustomEvent('kaguya-life', { detail: { mood, activity, energy, reentry } }));
  if (reentry) showReentry();
}

window.addEventListener('kaguya-mood', event => {
  const value = String((event as CustomEvent).detail?.mood ?? '');
  if (!MOODS.includes(value as LifeMood) || value === mood) return;
  mood = value as LifeMood;
  emit(false);
});

// 活動と元気さもサーバから届く。届くまでは初期値のまま静かに待つ。
window.addEventListener('kaguya-living', event => {
  const detail = (event as CustomEvent).detail ?? {};
  const next = String(detail.activity ?? '');
  const changed = ACTIVITIES.includes(next as LifeActivity) && next !== activity;
  if (ACTIVITIES.includes(next as LifeActivity)) activity = next as LifeActivity;
  if (Number.isFinite(Number(detail.energy))) energy = Number(detail.energy);
  const at = Date.parse(String(detail.last_seen_at ?? ''));
  if (Number.isFinite(at)) lastSeen = at;
  if (changed || detail.force === true) emit(false);
});

function markHidden(): void {
  lastSeen = Date.now();
}

function resume(): void {
  // 久しぶりに画面を開いたときだけ、いま何をしていたかを一言だけ言う。
  emit(Date.now() - lastSeen > 15 * MINUTE);
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') markHidden();
  else resume();
});
window.addEventListener('pagehide', markHidden);
window.addEventListener('pageshow', resume);

emit(false);
