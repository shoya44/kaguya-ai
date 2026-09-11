export type AvatarState = 'idle' | 'thinking' | 'talking' | 'greeting' | 'sleeping' | 'organizing';
type TimeSlot = 'morning' | 'day' | 'evening' | 'night';

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
const IDLE_ROTATE_MS = 45000;

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

export class Avatar {
  private ctx: CanvasRenderingContext2D;
  private state: AvatarState = 'idle';
  private frame = 0;
  private timer: number | null = null;

  constructor(private canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('2D context unavailable');
    this.ctx = ctx;
    this.rotate();
    this.draw();
  }

  setState(state: AvatarState): void {
    if (this.state === state) return;
    this.state = state;
    this.frame = 0;
    this.rotate();
    this.draw();
  }

  private frames(): string[] {
    return this.state === 'idle' ? IDLE_BY_TIME[timeSlot()] : SPRITES[this.state];
  }

  private rotate(): void {
    if (this.timer !== null) window.clearInterval(this.timer);
    this.timer = null;
    if (this.frames().length < 2) {
      if (this.state === 'idle') {
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
