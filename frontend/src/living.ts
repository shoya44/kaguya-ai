type LifeMood = 'normal' | 'happy' | 'sleepy' | 'sulky';
type LifeActivity = 'idle' | 'reading' | 'working' | 'playing' | 'snacking' | 'daydreaming' | 'sleeping';

type LifeState = {
  lastSeen: number;
  interactions: number;
  affection: number;
  hourCounts: number[];
  mood: LifeMood;
  moodUntil: number;
};

const LIFE_KEY = 'kaguya.life.v1';
const HOUR = 60 * 60 * 1000;
const MINUTE = 60 * 1000;

function fresh(): LifeState {
  return { lastSeen: Date.now(), interactions: 0, affection: 50,
    hourCounts: Array.from({ length: 24 }, () => 0), mood: 'normal', moodUntil: 0 };
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
      mood: ['normal', 'happy', 'sleepy', 'sulky'].includes(String(parsed.mood)) ? parsed.mood as LifeMood : 'normal',
      moodUntil: Number(parsed.moodUntil) || 0,
    };
  } catch {
    return fresh();
  }
}

let state = load();

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
  if ((hour < 6 || hour >= 1) && idleMs > 25 * MINUTE && !learnedAwake) return 'sleeping';
  if (idleMs < 3 * MINUTE) return 'idle';
  if (idleMs > 35 * MINUTE) {
    return deterministicChoice(['reading', 'playing', 'snacking', 'daydreaming'] as const, now);
  }
  return deterministicChoice(['reading', 'working', 'playing', 'daydreaming'] as const, now);
}

function effectiveMood(now = Date.now()): LifeMood {
  if (state.moodUntil > now) return state.mood;
  const hour = new Date(now).getHours();
  return hour < 6 || hour >= 23 ? 'sleepy' : 'normal';
}

function emit(reentry = false): void {
  const now = new Date();
  const idleMs = Math.max(0, now.getTime() - state.lastSeen);
  window.dispatchEvent(new CustomEvent('kaguya-life', { detail: {
    mood: effectiveMood(now.getTime()),
    activity: activity(now, idleMs),
    energy: energy(now),
    affection: state.affection,
    reentry,
    learnedHours: [...preferredHours()],
  } }));
}

function reactToText(text: string): void {
  const value = text.trim();
  if (!value) return;
  const now = Date.now();
  state.interactions += 1;
  state.hourCounts[new Date(now).getHours()] += 1;
  state.lastSeen = now;

  if (/(かわいい|好き|ありがとう|助かった|えらい|いい子)/.test(value)) {
    state.mood = 'happy';
    state.moodUntil = now + 12 * MINUTE;
    state.affection = Math.min(100, state.affection + 1);
  } else if (/(Claude|ChatGPT|チャットGPT).*(の方が|より).*(好き|賢い|すごい|良い|いい)/i.test(value)) {
    state.mood = 'sulky';
    state.moodUntil = now + 8 * MINUTE;
  } else if (/(おやすみ|寝るね|寝よう)/.test(value)) {
    state.mood = 'sleepy';
    state.moodUntil = now + 30 * MINUTE;
  }

  save();
  emit(false);
}

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
