export type AvatarState = 'idle' | 'thinking' | 'talking' | 'greeting' | 'sleeping' | 'organizing';
type TimeSlot = 'morning' | 'day' | 'evening' | 'night';
type LifeMood = 'normal' | 'happy' | 'sleepy' | 'sulky' | 'worried' | 'bored';
type LifeActivity = 'idle' | 'reading' | 'working' | 'playing' | 'snacking' | 'daydreaming' | 'sleeping';
// 一回性の動き。定常のゆれと違い、出来事に対して一度だけ返す。
export type Nudge = 'nod' | 'hop' | 'droop';

const SPRITES: Record<AvatarState, string[]> = {
  idle: ['/sprites/wave.png', '/sprites/book.png', '/sprites/laptop.png', '/sprites/cards.png'],
  thinking: ['/sprites/think.png'],
  talking: ['/sprites/talk.png'],
  greeting: ['/sprites/laugh.png'],
  sleeping: ['/sprites/sleep.png'],
  organizing: ['/sprites/write.png'],
};
const IDLE_BY_TIME: Record<TimeSlot, string[]> = {
  morning: ['/sprites/wave.png'],
  day: ['/sprites/laptop.png', '/sprites/book.png'],
  evening: ['/sprites/cards.png', '/sprites/book.png'],
  night: ['/sprites/book.png'],
};
const LIFE_SPRITES: Record<LifeActivity, string[]> = {
  idle: ['/sprites/wave.png'],
  reading: ['/sprites/book.png'],
  working: ['/sprites/laptop.png'],
  playing: ['/sprites/cards.png'],
  snacking: ['/sprites/snack.png'],
  daydreaming: ['/sprites/daydream.png'],
  sleeping: ['/sprites/sleep.png'],
};
// 気分ごとの絵。専用イラストが増えたらここだけ差し替える。
const MOOD_SPRITES: Record<LifeMood, string | null> = {
  normal: null,
  happy: '/sprites/laugh.png',
  sleepy: '/sprites/sleep.png',
  sulky: '/sprites/sulk.png',
  worried: '/sprites/worry.png',
  bored: '/sprites/bored.png',
};
// 専用イラストがまだ置かれていないときの代わり。読み込みに失敗した絵だけが
// ここを通る。絵を配置すれば、コードを変えずに専用イラストへ切り替わる。
const FALLBACK: Record<string, string> = {
  '/sprites/snack.png': '/sprites/wave.png',
  '/sprites/daydream.png': '/sprites/book.png',
  '/sprites/sulk.png': '/sprites/book.png',
  '/sprites/worry.png': '/sprites/think.png',
  '/sprites/bored.png': '/sprites/cards.png',
};
// 気分が変わった瞬間に一度だけ返す動き。載っていない気分は普段どおり。
const MOOD_NUDGE: Partial<Record<LifeMood, Nudge>> = {
  happy: 'hop',
  worried: 'droop',
  sulky: 'droop',
};
const NUDGES: Record<Nudge, { transform: string; ms: number }> = {
  nod: { transform: 'translateY(4px) rotate(0.6deg)', ms: 260 },
  hop: { transform: 'translateY(-10px) scale(1.02)', ms: 340 },
  droop: { transform: 'translateY(3px) rotate(-1.2deg) scale(0.99)', ms: 420 },
};

const IDLE_ROTATE_MS = 45_000;
const MOTION_MS = 1_900;
// 絵の差し替えにかける時間。瞬時に入れ替わると、表情が変わったというより点滅して見える。
const FADE_MS = 220;

function timeSlot(hour = new Date().getHours()): TimeSlot {
  if (5 <= hour && hour < 11) return 'morning';
  if (11 <= hour && hour < 17) return 'day';
  if (17 <= hour && hour < 23) return 'evening';
  return 'night';
}

const images = new Map<string, HTMLImageElement>();
// 置かれていない絵。1度失敗したら以後は代わりの絵を使い、取りに行かない。
const missing = new Set<string>();
function loadImage(src: string): HTMLImageElement {
  let img = images.get(src);
  if (!img) {
    img = new Image();
    img.src = src;
    images.set(src, img);
  }
  return img;
}
function resolve(src: string): string {
  return missing.has(src) ? (FALLBACK[src] ?? src) : src;
}
for (const frames of Object.values(SPRITES)) for (const src of frames) loadImage(src);
for (const frames of Object.values(LIFE_SPRITES)) for (const src of frames) loadImage(src);

export class Avatar {
  private ctx: CanvasRenderingContext2D;
  private state: AvatarState = 'idle';
  private frame = 0;
  private timer: number | null = null;
  private motionTimer: number | null = null;
  private motionFlip = false;
  private lifeMood: LifeMood = 'normal';
  private lifeActivity: LifeActivity = 'idle';
  private lifeEnergy = 60;
  private quiet = false;
  private drawVersion = 0;
  private shownSrc = '';
  private fadeRaf: number | null = null;
  private nudgeTimer: number | null = null;

  constructor(private canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('2D context unavailable');
    this.ctx = ctx;
    window.addEventListener('kaguya-life', event => {
      const detail = (event as CustomEvent).detail ?? {};
      const before = this.lifeMood;
      if (Object.prototype.hasOwnProperty.call(MOOD_SPRITES, detail.mood)) this.lifeMood = detail.mood;
      if (Object.prototype.hasOwnProperty.call(LIFE_SPRITES, detail.activity)) this.lifeActivity = detail.activity;
      if (Number.isFinite(detail.energy)) this.lifeEnergy = Number(detail.energy);
      // main.tsの「長時間会話なし=睡眠」より、復帰直後の生活演出を優先する。
      // ただし静音中は従来どおり睡眠表示のままにする。
      if (this.state === 'idle' || this.state === 'sleeping') {
        this.frame = 0;
        this.rotate();
        this.draw();
      }
      // 気分が「変わった」ことにだけ反応する。同じ気分が届き続けても跳ねない。
      const nudge = this.lifeMood !== before ? MOOD_NUDGE[this.lifeMood] : undefined;
      if (nudge) this.react(nudge);
      else this.updateMotion();
    });
    this.rotate();
    this.draw();
    this.updateMotion();
  }

  setState(state: AvatarState, quiet = false): void {
    if (this.state === state && this.quiet === quiet) return;
    this.state = state;
    this.quiet = quiet;
    this.frame = 0;
    this.rotate();
    this.draw();
    this.updateMotion();
  }

  /** 出来事に対して一度だけ動く。終わったら普段のゆれへ戻る。 */
  react(nudge: Nudge): void {
    const style = this.canvas.style;
    if (!style) return;
    const move = NUDGES[nudge];
    if (this.nudgeTimer !== null) window.clearTimeout(this.nudgeTimer);
    if (this.motionTimer !== null) window.clearInterval(this.motionTimer);
    this.motionTimer = null;
    style.transition = `transform ${move.ms}ms cubic-bezier(0.34, 1.4, 0.64, 1)`;
    style.transform = move.transform;
    this.nudgeTimer = window.setTimeout(() => {
      this.nudgeTimer = null;
      this.updateMotion();
    }, move.ms);
  }

  private livingOverridesSleeping(): boolean {
    return this.state === 'sleeping' && !this.quiet
      && this.lifeMood !== 'sleepy' && this.lifeActivity !== 'sleeping' && this.lifeEnergy >= 20;
  }

  private moodFrames(): string[] | null {
    const src = MOOD_SPRITES[this.lifeMood];
    return src ? [src] : null;
  }

  private frames(): string[] {
    if (this.state === 'sleeping' && this.livingOverridesSleeping()) {
      // 眠りより生活の演出を優先する場面。眠そうな気分はここへ来ない。
      return this.moodFrames()
        ?? (this.lifeActivity !== 'idle' ? LIFE_SPRITES[this.lifeActivity] : IDLE_BY_TIME[timeSlot()]);
    }
    if (this.state !== 'idle') return SPRITES[this.state];
    if (this.lifeMood === 'happy') return ['/sprites/laugh.png'];
    if (this.lifeMood === 'sleepy' || this.lifeActivity === 'sleeping' || this.lifeEnergy < 20) {
      return ['/sprites/sleep.png'];
    }
    return this.moodFrames()
      ?? (this.lifeActivity !== 'idle' ? LIFE_SPRITES[this.lifeActivity] : IDLE_BY_TIME[timeSlot()]);
  }

  private rotate(): void {
    if (this.timer !== null) window.clearInterval(this.timer);
    this.timer = null;
    if (this.frames().length < 2) {
      if (this.state === 'idle' || this.livingOverridesSleeping()) {
        this.timer = window.setInterval(() => { this.frame = 0; this.draw(); }, IDLE_ROTATE_MS);
      }
      return;
    }
    this.timer = window.setInterval(() => {
      const frames = this.frames();
      this.frame = (this.frame + 1) % frames.length;
      this.draw();
    }, IDLE_ROTATE_MS);
  }

  private updateMotion(): void {
    // 一回性の動きの途中なら触らない。終わったときに改めてここへ戻ってくる。
    if (this.nudgeTimer !== null) return;
    if (this.motionTimer !== null) window.clearInterval(this.motionTimer);
    this.motionTimer = null;
    const style = (this.canvas as HTMLCanvasElement & { style?: CSSStyleDeclaration }).style;
    if (!style) return;
    style.transition = `transform ${MOTION_MS - 150}ms ease-in-out`;

    const apply = () => {
      this.motionFlip = !this.motionFlip;
      const visuallySleeping = resolve(this.frames()[0]) === '/sprites/sleep.png';
      if (visuallySleeping) {
        style.transform = this.motionFlip ? 'translateY(1px) scale(0.995)' : 'translateY(0) scale(1.005)';
      } else if (this.state === 'talking' || this.state === 'greeting' || this.lifeMood === 'happy') {
        style.transform = this.motionFlip ? 'translateY(-3px) rotate(-0.4deg)' : 'translateY(0) rotate(0.4deg)';
      } else if (this.state === 'thinking') {
        style.transform = this.motionFlip ? 'translateY(-1px) rotate(-0.5deg)' : 'translateY(0) rotate(0deg)';
      } else if (this.lifeMood === 'sulky') {
        style.transform = this.motionFlip ? 'translateX(-2px) rotate(-0.8deg)' : 'translateX(0) rotate(-0.2deg)';
      } else if (this.lifeMood === 'worried') {
        // 心配しているときは動きを小さくする。弾むと軽く見える。
        style.transform = this.motionFlip ? 'translateY(-1px)' : 'translateY(0)';
      } else {
        style.transform = this.motionFlip ? 'translateY(-2px)' : 'translateY(0)';
      }
    };
    apply();
    this.motionTimer = window.setInterval(apply, MOTION_MS);
  }

  private draw(): void {
    const frames = this.frames();
    const src = resolve(frames[this.frame] ?? frames[0]);
    const img = loadImage(src);
    const version = ++this.drawVersion;
    const begin = () => {
      if (version !== this.drawVersion || !img.naturalWidth || !img.naturalHeight) return;
      const from = this.shownSrc && this.shownSrc !== src ? images.get(this.shownSrc) ?? null : null;
      this.shownSrc = src;
      this.fade(from, img);
    };
    if (img.complete && img.naturalWidth) {
      begin();
      return;
    }
    img.addEventListener('load', begin, { once: true });
    img.addEventListener('error', () => {
      // 専用イラストが未配置。代わりの絵で描き直す。
      if (missing.has(src) || !FALLBACK[src]) return;
      missing.add(src);
      if (version === this.drawVersion) this.draw();
    }, { once: true });
  }

  private fade(from: HTMLImageElement | null, to: HTMLImageElement): void {
    if (this.fadeRaf !== null) cancelAnimationFrame(this.fadeRaf);
    this.fadeRaf = null;
    if (!from || !from.naturalWidth) {
      this.paint(null, to, 1);
      return;
    }
    const start = performance.now();
    const step = (now: number) => {
      const ratio = Math.min(1, (now - start) / FADE_MS);
      this.paint(from, to, ratio);
      this.fadeRaf = ratio < 1 ? requestAnimationFrame(step) : null;
    };
    this.fadeRaf = requestAnimationFrame(step);
  }

  private paint(from: HTMLImageElement | null, to: HTMLImageElement, ratio: number): void {
    const { ctx, canvas } = this;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (from) {
      ctx.globalAlpha = 1 - ratio;
      this.drawFit(from);
    }
    ctx.globalAlpha = from ? ratio : 1;
    this.drawFit(to);
    ctx.globalAlpha = 1;
  }

  private drawFit(img: HTMLImageElement): void {
    const { ctx, canvas } = this;
    if (!img.naturalWidth || !img.naturalHeight) return;
    const scale = Math.min(canvas.width / img.naturalWidth, canvas.height / img.naturalHeight);
    const w = img.naturalWidth * scale;
    const h = img.naturalHeight * scale;
    ctx.drawImage(img, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
  }
}
