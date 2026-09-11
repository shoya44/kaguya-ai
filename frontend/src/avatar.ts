export type AvatarState = 'idle' | 'thinking' | 'talking' | 'greeting' | 'sleeping' | 'organizing';
type TimeSlot = 'morning' | 'day' | 'evening' | 'night';
type LifeMood = 'normal' | 'happy' | 'sleepy' | 'sulky';
type LifeActivity = 'idle' | 'reading' | 'working' | 'playing' | 'snacking' | 'daydreaming' | 'sleeping';

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
  // 専用イラストを後から追加しやすいよう生活状態は分けておく。
  // v1では既存絵を流用し、未配置ファイルは参照しない。
  snacking: ['/sprites/wave.png'],
  daydreaming: ['/sprites/book.png'],
  sleeping: ['/sprites/sleep.png'],
};
const IDLE_ROTATE_MS = 45_000;
const MOTION_MS = 1_900;

function timeSlot(hour = new Date().getHours()): TimeSlot {
  if (5 <= hour && hour < 11) return 'morning';
  if (11 <= hour && hour < 17) return 'day';
  if (17 <= hour && hour < 23) return 'evening';
  return 'night';
}

const images = new Map<string, HTMLImageElement>();
function loadImage(src: string): HTMLImageElement {
  let img = images.get(src);
  if (!img) {
    img = new Image();
    img.src = src;
    images.set(src, img);
  }
  return img;
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

  constructor(private canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('2D context unavailable');
    this.ctx = ctx;
    window.addEventListener('kaguya-life', event => {
      const detail = (event as CustomEvent).detail ?? {};
      if (['normal', 'happy', 'sleepy', 'sulky'].includes(detail.mood)) this.lifeMood = detail.mood;
      if (Object.prototype.hasOwnProperty.call(LIFE_SPRITES, detail.activity)) this.lifeActivity = detail.activity;
      if (Number.isFinite(detail.energy)) this.lifeEnergy = Number(detail.energy);
      // main.tsの「長時間会話なし=睡眠」より、復帰直後の生活演出を優先する。
      // ただし静音中は従来どおり睡眠表示のままにする。
      if (this.state === 'idle' || this.state === 'sleeping') {
        this.frame = 0;
        this.rotate();
        this.draw();
      }
      this.updateMotion();
    });
    this.rotate();
    this.draw();
    this.updateMotion();
  }

  setState(state: AvatarState): void {
    if (this.state === state) return;
    this.state = state;
    this.frame = 0;
    this.rotate();
    this.draw();
    this.updateMotion();
  }

  private quietEnabled(): boolean {
    return document.getElementById('quiet-btn')?.getAttribute('aria-pressed') === 'true';
  }

  private livingOverridesSleeping(): boolean {
    return this.state === 'sleeping' && !this.quietEnabled()
      && this.lifeMood !== 'sleepy' && this.lifeActivity !== 'sleeping' && this.lifeEnergy >= 20;
  }

  private frames(): string[] {
    if (this.state === 'sleeping' && this.livingOverridesSleeping()) {
      return this.lifeMood === 'happy' ? ['/sprites/laugh.png']
        : this.lifeMood === 'sulky' ? ['/sprites/book.png']
        : this.lifeActivity !== 'idle' ? LIFE_SPRITES[this.lifeActivity]
        : IDLE_BY_TIME[timeSlot()];
    }
    if (this.state !== 'idle') return SPRITES[this.state];
    if (this.lifeMood === 'happy') return ['/sprites/laugh.png'];
    if (this.lifeMood === 'sleepy' || this.lifeActivity === 'sleeping' || this.lifeEnergy < 20) {
      return ['/sprites/sleep.png'];
    }
    if (this.lifeMood === 'sulky') return ['/sprites/book.png'];
    if (this.lifeActivity !== 'idle') return LIFE_SPRITES[this.lifeActivity];
    return IDLE_BY_TIME[timeSlot()];
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
    if (this.motionTimer !== null) window.clearInterval(this.motionTimer);
    this.motionTimer = null;
    const style = (this.canvas as HTMLCanvasElement & { style?: CSSStyleDeclaration }).style;
    if (!style) return;
    style.transition = `transform ${MOTION_MS - 150}ms ease-in-out`;

    const apply = () => {
      this.motionFlip = !this.motionFlip;
      const visuallySleeping = !this.livingOverridesSleeping()
        && (this.state === 'sleeping' || (this.state === 'idle' && (this.lifeMood === 'sleepy' || this.lifeActivity === 'sleeping')));
      if (visuallySleeping) {
        style.transform = this.motionFlip ? 'translateY(1px) scale(0.995)' : 'translateY(0) scale(1.005)';
      } else if (this.state === 'talking' || this.state === 'greeting' || this.lifeMood === 'happy') {
        style.transform = this.motionFlip ? 'translateY(-3px) rotate(-0.4deg)' : 'translateY(0) rotate(0.4deg)';
      } else if (this.state === 'thinking') {
        style.transform = this.motionFlip ? 'translateY(-1px) rotate(-0.5deg)' : 'translateY(0) rotate(0deg)';
      } else if (this.lifeMood === 'sulky') {
        style.transform = this.motionFlip ? 'translateX(-2px) rotate(-0.8deg)' : 'translateX(0) rotate(-0.2deg)';
      } else {
        style.transform = this.motionFlip ? 'translateY(-2px)' : 'translateY(0)';
      }
    };
    apply();
    this.motionTimer = window.setInterval(apply, MOTION_MS);
  }

  private draw(): void {
    const { ctx, canvas } = this;
    const frames = this.frames();
    const img = loadImage(frames[this.frame] ?? frames[0]);
    const render = () => {
      const scale = Math.min(canvas.width / img.naturalWidth, canvas.height / img.naturalHeight);
      const w = img.naturalWidth * scale;
      const h = img.naturalHeight * scale;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
    };
    if (img.complete) render();
    else img.addEventListener('load', render, { once: true });
  }
}
