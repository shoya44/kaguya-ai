export type AvatarState = 'idle' | 'thinking' | 'talking' | 'greeting' | 'sleeping' | 'organizing';
type TimeSlot = 'morning' | 'day' | 'evening' | 'night';
type LifeMood = 'normal' | 'happy' | 'sleepy' | 'sulky' | 'worried' | 'bored';
type LifeActivity = 'idle' | 'reading' | 'working' | 'playing' | 'snacking' | 'daydreaming' | 'sleeping';
// 一回性の動き。定常のゆれと違い、出来事に対して一度だけ返す。
// nod=受け取った / hop=嬉しい / droop=しゅんとする / perk=顔を上げる
// settle=座り直す / sink=寝入る / stretch=伸びをする
export type Nudge = 'nod' | 'hop' | 'droop' | 'perk' | 'settle' | 'sink' | 'stretch';

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
// 一回性の動き。rank が高いものだけが、実行中の動きに割り込める。
// 呼吸と同じく、床から浮くのは hop だけ。跳ねる以外は接地したまま伸縮させる。
const NUDGES: Record<Nudge, { transform: string; ms: number; rank: number }> = {
  // 座り直し。何より弱い。話しかけられている最中に割り込んではいけない。
  settle: { transform: 'rotate(-0.6deg) scaleY(0.997)', ms: 620, rank: 1 },
  sink: { transform: 'scaleY(0.98)', ms: 900, rank: 2 },
  nod: { transform: 'scaleY(0.985)', ms: 260, rank: 3 },
  perk: { transform: 'scaleY(1.025)', ms: 380, rank: 3 },
  stretch: { transform: 'scaleY(1.04)', ms: 760, rank: 3 },
  // 跳ねるときだけは足が床を離れる。
  hop: { transform: 'translateY(-9px) scaleY(1.01)', ms: 340, rank: 4 },
  droop: { transform: 'scaleY(0.975) rotate(-1deg)', ms: 420, rank: 4 },
};
// 一回性の動きを続けて出さない下限。これより短い間隔で重ねるとガタガタする。
const NUDGE_GAP_MS = 320;

// 手持ち無沙汰な間の身じろぎ。呼吸だけだと一定周期のループに見える。
// 活動の選び方（living.py）は乱数を避けているが、ここは間隔がばらつくことに
// 意味がある。毎回同じ秒数で身じろぎすると、それ自体が機械の周期になる。
const IDLE_BREAK_MIN_MS = 30_000;
const IDLE_BREAK_MAX_MS = 90_000;

// 伏せている絵。接地面が広いので、動かすと本人ではなく絵全体が浮いて見える。
const LYING = new Set(['/sprites/sleep.png', '/sprites/bored.png', '/sprites/daydream.png']);
// 一呼吸の長さ。吸うより吐くほうが長い。同じ長さで往復すると振り子に見える。
const BREATH_IN_MS = 1_400;
const BREATH_OUT_MS = 2_500;
// 画面に出ているキャラの高さ（style.cssの#avatar）。測れないときだけ使う。
const AVATAR_CSS_PX = 160;

const IDLE_ROTATE_MS = 45_000;
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
  private nudgeRank = 0;
  private nudgeEndedAt = 0;
  private breakTimer: number | null = null;

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
      // しばらく居なかった相手が戻ってきた。living.tsの「おかえり」と動きを揃える。
      if (detail.reentry) this.react('perk');
      // 気分が「変わった」ことにだけ反応する。同じ気分が届き続けても跳ねない。
      const nudge = this.lifeMood !== before ? MOOD_NUDGE[this.lifeMood] : undefined;
      if (nudge) this.react(nudge);
      else if (!detail.reentry) this.updateMotion();
    });
    this.rotate();
    this.draw();
    this.updateMotion();
    this.scheduleIdleBreak();
  }

  setState(state: AvatarState, quiet = false): void {
    if (this.state === state && this.quiet === quiet) return;
    const wasAsleep = this.looksAsleep();
    this.state = state;
    this.quiet = quiet;
    this.frame = 0;
    this.rotate();
    this.draw();
    // 眠りの出入りそのものに動きを与える。絵だけ入れ替わると寝落ちに見えない。
    const asleep = this.looksAsleep();
    if (!wasAsleep && asleep) this.react('sink');
    else if (wasAsleep && !asleep) this.react('stretch');
    else this.updateMotion();
  }

  /**
   * 出来事に対して一度だけ動く。終わったら普段の呼吸へ戻る。
   *
   * 出来事は続けて届く。「返事が来た直後に気分が変わる」のような重なりで
   * 動きを次々と上書きすると、生きているというより落ち着きがなく見える。
   * 強い動きだけが割り込めることにして、弱い動きは見送る。
   */
  react(nudge: Nudge): void {
    const style = this.canvas.style;
    if (!style) return;
    const move = NUDGES[nudge];
    // 動いている最中。より強い動きでなければ、いまの動きを最後まで見せる。
    if (this.nudgeTimer !== null && move.rank <= this.nudgeRank) return;
    // 直前の動きが終わった直後。同じか弱い動きなら間を置く。
    if (this.nudgeTimer === null && move.rank <= this.nudgeRank
        && Date.now() - this.nudgeEndedAt < NUDGE_GAP_MS) return;

    if (this.nudgeTimer !== null) window.clearTimeout(this.nudgeTimer);
    if (this.motionTimer !== null) window.clearTimeout(this.motionTimer);
    this.motionTimer = null;
    this.nudgeRank = move.rank;
    style.transition = `transform ${move.ms}ms cubic-bezier(0.34, 1.4, 0.64, 1)`;
    style.transform = move.transform;
    this.nudgeTimer = window.setTimeout(() => {
      this.nudgeTimer = null;
      this.nudgeEndedAt = Date.now();
      this.updateMotion();
    }, move.ms);
  }

  /** 眠っているように見えているか。絵で判断するので、生活の演出と食い違わない。 */
  private looksAsleep(): boolean {
    return resolve(this.frames()[0]) === '/sprites/sleep.png';
  }

  /** 手持ち無沙汰な間だけ身じろぎする。話しかけられている最中はしない。 */
  private scheduleIdleBreak(): void {
    if (this.breakTimer !== null) window.clearTimeout(this.breakTimer);
    const wait = IDLE_BREAK_MIN_MS + Math.random() * (IDLE_BREAK_MAX_MS - IDLE_BREAK_MIN_MS);
    this.breakTimer = window.setTimeout(() => {
      this.breakTimer = null;
      if (this.state === 'idle' && !this.quiet && !this.looksAsleep()) this.react('settle');
      this.scheduleIdleBreak();
    }, wait);
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

  /** 一呼吸ぶんの胸の膨らみ（px）と傾き（度）。姿勢と気分で変わる。 */
  private breath(): { lift: number; tilt: number } {
    // 伏せている姿勢は接地面が広い。大きく伸ばすと床ごと動いて見える。
    if (LYING.has(resolve(this.frames()[0]))) return { lift: 0.6, tilt: 0 };
    if (this.state === 'talking' || this.state === 'greeting' || this.lifeMood === 'happy') {
      return { lift: 3, tilt: 0.4 };
    }
    if (this.state === 'thinking') return { lift: 1.2, tilt: 0.5 };
    // すねているときは横へ傾いだまま。持ち上がりは抑える。
    if (this.lifeMood === 'sulky') return { lift: 0.8, tilt: -0.9 };
    // 心配しているときは動きを小さくする。弾むと軽く見える。
    if (this.lifeMood === 'worried') return { lift: 1, tilt: 0 };
    return { lift: 2, tilt: 0 };
  }

  private updateMotion(): void {
    // 一回性の動きの途中なら触らない。終わったときに改めてここへ戻ってくる。
    if (this.nudgeTimer !== null) return;
    if (this.motionTimer !== null) window.clearTimeout(this.motionTimer);
    this.motionTimer = null;
    const style = (this.canvas as HTMLCanvasElement & { style?: CSSStyleDeclaration }).style;
    if (!style) return;
    // 支点を足元に置く。中心を軸にすると、傾くたびに座った足や床が左右へ振れる。
    style.transformOrigin = '50% 100%';

    const step = () => {
      this.motionFlip = !this.motionFlip;
      const { lift, tilt } = this.breath();
      // 吸うほうを短く、吐くほうを長く。息を吐ききったところが基準の姿勢。
      const ms = this.motionFlip ? BREATH_IN_MS : BREATH_OUT_MS;
      style.transition = `transform ${ms}ms ${this.motionFlip
        ? 'cubic-bezier(0.37, 0, 0.63, 1)' : 'cubic-bezier(0.33, 0, 0.4, 1)'}`;
      // 動かすのは縦の伸びだけ。持ち上げると足や床まで一緒に浮いてしまう。
      // 膨らみのpxを、いま表示されている高さに対する伸び率へ読み替える。
      const height = this.canvas.clientHeight || AVATAR_CSS_PX;
      style.transform = this.motionFlip
        ? `scaleY(${(1 + lift / height).toFixed(4)}) rotate(${tilt}deg)`
        : 'scaleY(1) rotate(0deg)';
      this.motionTimer = window.setTimeout(step, ms);
    };
    step();
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
