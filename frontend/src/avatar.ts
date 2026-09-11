export type AvatarState = 'idle' | 'thinking' | 'talking' | 'greeting' | 'sleeping' | 'organizing';

// 状態ごとに使う画像。待機中だけは「何かして過ごしている」様子を出すため
// 複数枚を持ち、それ以外は1状態＝1枚で固定する（状態が変わったときにだけ
// 描き替わるので、意味のない点滅にはならない）。
const SPRITES: Record<AvatarState, string[]> = {
  idle: ['/sprites/wave.png', '/sprites/book.png', '/sprites/laptop.png', '/sprites/cards.png'],
  thinking: ['/sprites/think.png'],   // 返事を考えている
  talking: ['/sprites/talk.png'],     // 返事を表示した直後
  greeting: ['/sprites/laugh.png'],   // 自発的な声かけ
  sleeping: ['/sprites/sleep.png'],   // 静音中、または長時間会話がない
  organizing: ['/sprites/write.png'], // 記憶の整理ジョブを実行中
};

// 待機中の切り替え間隔。短いとスライドショーに見えるため、ゆっくり回す。
const IDLE_ROTATE_MS = 45000;

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
// 状態が変わった瞬間に空白が出ないよう、全画像を先に読み込んでおく。
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
    // 会話から戻ったときは必ず1枚目（手を振る）から始める。
    this.frame = 0;
    this.rotate();
    this.draw();
  }

  /** 複数枚を持つ状態のあいだだけタイマーを動かす。 */
  private rotate(): void {
    if (this.timer !== null) window.clearInterval(this.timer);
    this.timer = null;
    const frames = SPRITES[this.state];
    if (frames.length < 2) return;
    this.timer = window.setInterval(() => {
      this.frame = (this.frame + 1) % frames.length;
      this.draw();
    }, IDLE_ROTATE_MS);
  }

  private draw(): void {
    const { ctx, canvas } = this;
    const frames = SPRITES[this.state];
    const img = loadImage(frames[this.frame] ?? frames[0]);
    const render = () => {
      // "contain"で収める：スプライトは正方形ではない（例: wave.pngは418x409）ため、
      // 正方形のcanvasに引き伸ばすと歪む。
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
