type Session = { sessionToken: string };

export class VoiceChat {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private socket: WebSocket | null = null;
  private capture: AudioWorkletNode | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private nextAudio = 0;
  private maxLead = 10;
  private generation = 0;
  private active = false;
  private timer: number | undefined;
  private button = document.getElementById('voice-toggle') as HTMLButtonElement;

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
    window.addEventListener('pagehide', () => this.stop('通話終了'));
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && this.active) this.stop('通話終了');
    });
  }

  private status(text: string): void { document.getElementById('voice-status')!.textContent = text; }

  private label(text: string, active: boolean): void {
    this.button.setAttribute('aria-label', text);
    this.button.setAttribute('title', text);
    this.button.dataset.active = String(active);
  }

  static unavailable(): string {
    if (!window.isSecureContext) return 'この接続ではブラウザがマイクを使えません。PCアプリまたはHTTPS接続を使ってください。';
    if (!navigator.mediaDevices?.getUserMedia) return 'このブラウザはマイクに対応していません。';
    return '';
  }

  async start(): Promise<void> {
    if (this.active) return;
    const blocked = VoiceChat.unavailable();
    if (blocked) { this.status(blocked); return; }
    this.active = true;
    this.onActive(true);
    const generation = ++this.generation;
    this.label('通話を終了', true);
    this.status('接続中…');
    try {
      this.context = new AudioContext();
      await this.context.resume();
      if (generation !== this.generation) return;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      }});
      if (generation !== this.generation) { stream.getTracks().forEach(track => track.stop()); return; }
      this.stream = stream;
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
        if (generation === this.generation) this.stop('通話終了');
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
    source.connect(context.destination);
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
    ++this.generation;
    this.active = false;
    window.clearTimeout(this.timer);
    this.maxLead = 10;
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    this.capture?.disconnect(); this.capture = null;
    this.clearPlayback();
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({ type: 'stop' }));
    this.socket?.close(); this.socket = null;
    void this.context?.close().catch(() => {}); this.context = null;
    this.label('音声通話を開始', false); this.status(message);
    this.onActive(false);
  }
}
