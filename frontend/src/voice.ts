type Session = { sessionToken: string };

// 音量はこの端末だけのもの。PCのスピーカーとiPhoneでは同時に別の大きさが要る。
// かぐやの気分や活動をサーバに置いたのは端末間で別人にならないためだが、
// 音量はその逆で、端末ごとに違って当然のもの。
const VOLUME_KEY = 'kaguya.voiceVolume';
const MUTED_KEY = 'kaguya.voiceMuted';
// 音量を変えた瞬間に値を飛ばすと、再生中の音がプツッと鳴る。短くならす。
const VOLUME_RAMP = 0.015;

export class VoiceChat {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private socket: WebSocket | null = null;
  private capture: AudioWorkletNode | null = null;
  private gain: GainNode | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private nextAudio = 0;
  private maxLead = 10;
  private generation = 0;
  private active = false;
  private timer: number | undefined;
  private remainingTimer: number | undefined;
  private deadline = 0;
  private micMuted = false;
  private volume = 1;
  private muted = false;
  private button = document.getElementById('voice-toggle') as HTMLButtonElement;
  private volumeBox = document.getElementById('voice-volume') as HTMLDivElement | null;
  private slider = document.getElementById('voice-gain') as HTMLInputElement | null;
  private muteButton = document.getElementById('voice-mute') as HTMLButtonElement | null;
  private micButton = document.getElementById('voice-mic') as HTMLButtonElement | null;
  private reconnectButton = document.getElementById('voice-reconnect') as HTMLButtonElement | null;

  constructor(private base: string, private session: () => Promise<Session>, private onActive: (active: boolean) => void) {
    const blocked = VoiceChat.unavailable();
    if (blocked) {
      this.button.disabled = true;
      this.status(blocked);
    }
    this.button.addEventListener('click', () => {
      if (this.active) this.stop('通話終了');
      else void this.start();
    });
    this.reconnectButton?.addEventListener('click', () => void this.start());
    this.micButton?.addEventListener('click', () => {
      this.micMuted = !this.micMuted;
      this.stream?.getAudioTracks().forEach(track => { track.enabled = !this.micMuted; });
      this.showMic();
    });
    window.addEventListener('pagehide', () => {
      if (this.active) this.stop('画面を離れたため通話を終了しました。');
    });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && this.active) this.stop('画面を離れたため通話を終了しました。');
    });
    this.setupVolume();
  }

  /** 端末に覚えた音量を読み、つまみと消音ボタンを繋ぐ。 */
  private setupVolume(): void {
    let stored: string | null = null;
    let mutedStored: string | null = null;
    try {
      stored = localStorage.getItem(VOLUME_KEY);
      mutedStored = localStorage.getItem(MUTED_KEY);
    } catch { /* 読めなくても既定値で通話はできる */ }
    const percent = Number(stored);
    // 覚えた値が無い・壊れているときは、これまでと同じ音量（そのまま）にする。
    this.volume = Number.isFinite(percent) && stored !== null
      ? Math.min(1, Math.max(0, percent / 100)) : 1;
    this.muted = mutedStored === '1';
    if (this.slider) this.slider.value = String(Math.round(this.volume * 100));
    this.showMuted();

    this.slider?.addEventListener('input', () => {
      this.volume = Math.min(1, Math.max(0, Number(this.slider!.value) / 100));
      // つまみを動かしたなら消音は解除する。動かしても無音のままだと壊れて見える。
      this.muted = false;
      this.showMuted();
      this.applyVolume();
      this.remember();
    });
    this.muteButton?.addEventListener('click', () => {
      this.muted = !this.muted;
      this.showMuted();
      this.applyVolume();
      this.remember();
    });
  }

  private level(): number {
    return this.muted ? 0 : this.volume;
  }

  private applyVolume(): void {
    const context = this.context;
    if (!this.gain || !context) return;
    this.gain.gain.setTargetAtTime(this.level(), context.currentTime, VOLUME_RAMP);
  }

  private showMuted(): void {
    this.volumeBox?.setAttribute('data-muted', String(this.muted));
    const text = this.muted ? 'かぐやの音声の消音を解除' : 'かぐやの音声を消音';
    this.muteButton?.setAttribute('aria-label', text);
    this.muteButton?.setAttribute('title', text);
    this.muteButton?.setAttribute('aria-pressed', String(this.muted));
  }

  private remember(): void {
    try {
      localStorage.setItem(VOLUME_KEY, String(Math.round(this.volume * 100)));
      localStorage.setItem(MUTED_KEY, this.muted ? '1' : '0');
    } catch { /* 覚えられなくても通話は続けられる */ }
  }

  private status(text: string): void { document.getElementById('voice-status')!.textContent = text; }

  private showMic(): void {
    if (!this.micButton) return;
    this.micButton.textContent = this.micMuted ? 'マイクを入れる' : 'マイクを切る';
    this.micButton.setAttribute('aria-pressed', String(this.micMuted));
    this.micButton.setAttribute('aria-label', this.micMuted ? 'マイクはオフ。自分の声を送信する' : 'マイクはオン。自分の声を止める');
  }

  private updateRemaining(): void {
    const seconds = Math.max(0, Math.ceil((this.deadline - Date.now()) / 1000));
    const minutes = Math.ceil(seconds / 60);
    const text = seconds <= 60
      ? 'まもなく終了（残り1分以内）。終了後に再開できます。' : `残り約${minutes}分（最大10分）`;
    const label = document.getElementById('voice-remaining')!;
    if (label.textContent !== text) label.textContent = text;
  }

  private label(text: string, active: boolean): void {
    this.button.setAttribute('aria-label', text);
    this.button.setAttribute('title', text);
    this.button.dataset.active = String(active);
  }

  static unavailable(): string {
    if (!window.isSecureContext) return 'この接続ではブラウザがマイクを使えません。PCアプリ、または pc_setup.bat で設定した ts.net のHTTPS接続を使ってください。';
    if (!navigator.mediaDevices?.getUserMedia) return 'このブラウザはマイクに対応していません。';
    return '';
  }

  async start(): Promise<void> {
    if (this.active) return;
    const blocked = VoiceChat.unavailable();
    if (blocked) { this.status(blocked); return; }
    this.active = true;
    if (this.reconnectButton) this.reconnectButton.hidden = true;
    this.micMuted = false;
    this.showMic();
    this.onActive(true);
    const generation = ++this.generation;
    this.label('通話を終了', true);
    this.status('接続中…');
    try {
      this.context = new AudioContext();
      await this.context.resume();
      if (generation !== this.generation) return;
      // 届いた音声はすべてここを通してから出力へ送る。音量はここ1箇所で決まる。
      this.gain = this.context.createGain();
      this.gain.gain.value = this.level();
      this.gain.connect(this.context.destination);
      if (this.volumeBox) this.volumeBox.hidden = false;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      }});
      if (generation !== this.generation) { stream.getTracks().forEach(track => track.stop()); return; }
      this.stream = stream;
      if (this.micButton) this.micButton.hidden = false;
      stream.getTracks().forEach(track => track.addEventListener('ended', () => {
        if (generation === this.generation) this.stop('マイク終了');
      }));
      await this.context!.audioWorklet.addModule('/pcm-worklet.js');
      const current = await this.session();
      if (generation !== this.generation) return;
      const socket = new WebSocket(this.base.replace(/^http/, 'ws') + '/voice');
      this.socket = socket;
      socket.binaryType = 'arraybuffer';
      const timeout = window.setTimeout(() => {
        if (generation === this.generation) this.stop('接続できませんでした。');
      }, 30000);
      socket.addEventListener('open', () => socket.send(JSON.stringify({ token: current.sessionToken })));
      socket.addEventListener('message', event => {
        if (generation !== this.generation) return;
        if (event.data instanceof ArrayBuffer) { this.play(event.data); return; }
        const message = JSON.parse(event.data);
        if (message.type === 'ready') {
          window.clearTimeout(timeout);
          this.maxLead = message.local_voice ? 30 : 10;
          const context = this.context!;
          this.capture = new AudioWorkletNode(context, 'pcm-capture');
          this.capture.port.onmessage = event => {
            if (socket.readyState !== WebSocket.OPEN) return;
            if (socket.bufferedAmount > 128000) { this.stop('通信が遅れています。'); return; }
            socket.send(event.data);
          };
          const mute = context.createGain(); mute.gain.value = 0;
          context.createMediaStreamSource(stream).connect(this.capture).connect(mute).connect(context.destination);
          this.status('通話中');
          this.deadline = Date.now() + 600000;
          this.updateRemaining();
          this.remainingTimer = window.setInterval(() => this.updateRemaining(), 1000);
          this.timer = window.setTimeout(() => this.stop('10分で通話を終了しました。'), 600000);
        } else if (message.type === 'interrupted') {
          this.clearPlayback();
        } else if (message.type === 'error') {
          this.stop(message.message);
        } else if (message.type === 'notice') {
          this.status(message.message);
        }
      });
      socket.addEventListener('close', () => {
        window.clearTimeout(timeout);
        if (generation === this.generation) this.stop(this.deadline && Date.now() >= this.deadline - 2000
          ? '10分で通話を終了しました。' : '音声接続が切れました。通話を再開できます。');
      });
      socket.addEventListener('error', () => {
        if (generation === this.generation) this.stop('音声接続に失敗しました。');
      });
    } catch {
      if (generation === this.generation) this.stop('音声を開始できません。');
    }
  }

  private play(bytes: ArrayBuffer): void {
    const context = this.context;
    if (!context || bytes.byteLength % 2) return;
    if (this.nextAudio - context.currentTime > this.maxLead) { this.stop('音声再生が遅れています。'); return; }
    const samples = new Int16Array(bytes);
    const buffer = context.createBuffer(1, samples.length, 24000);
    const output = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) output[i] = samples[i] / 32768;
    const source = context.createBufferSource(); source.buffer = buffer;
    source.connect(this.gain ?? context.destination);
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
    this.nextAudio = Math.max(context.currentTime + 0.02, this.nextAudio);
    source.start(this.nextAudio);
    this.nextAudio += buffer.duration;
  }

  private clearPlayback(): void {
    this.sources.forEach(source => { try { source.stop(); } catch { /* already ended */ } });
    this.sources.clear(); this.nextAudio = 0;
  }

  stop(message: string): void {
    const wasActive = this.active;
    ++this.generation;
    this.active = false;
    window.clearTimeout(this.timer);
    window.clearInterval(this.remainingTimer);
    this.deadline = 0;
    document.getElementById('voice-remaining')!.textContent = '';
    if (this.micButton) this.micButton.hidden = true;
    if (this.reconnectButton && wasActive) this.reconnectButton.hidden = !!VoiceChat.unavailable();
    this.maxLead = 10;
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    this.capture?.disconnect(); this.capture = null;
    this.gain?.disconnect(); this.gain = null;
    if (this.volumeBox) this.volumeBox.hidden = true;
    this.clearPlayback();
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({ type: 'stop' }));
    this.socket?.close(); this.socket = null;
    void this.context?.close().catch(() => {}); this.context = null;
    this.label('音声通話を開始', false); this.status(message);
    this.onActive(false);
  }
}
