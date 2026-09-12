type Api = (path: string, init?: RequestInit) => Promise<any>;

export class PCPanel {
  private offset = 0;
  private next: number | null = null;
  private request = 0;
  private query = '';
  private poll: number | undefined;
  private player = document.getElementById('pc-video') as HTMLVideoElement;
  private videoButtons: HTMLButtonElement[] = [];
  private commandButtons = new Map<string, HTMLButtonElement>();

  constructor(private api: Api, private base: string) {
    document.getElementById('pc-filter')!.addEventListener('submit', event => {
      event.preventDefault(); this.offset = 0;
      this.query = (document.getElementById('pc-search') as HTMLInputElement).value;
      void this.refresh();
    });
    document.getElementById('pc-next')!.addEventListener('click', () => {
      if (this.next !== null) { this.offset = this.next; void this.refresh(); }
    });
    document.getElementById('pc-prev')!.addEventListener('click', () => {
      this.offset = Math.max(0, this.offset - 30); void this.refresh();
    });
    this.player.addEventListener('error', () => this.status('再生できません。形式の非対応・接続切れ・リンク期限切れの可能性があります。動画を選び直してください。'));
    window.addEventListener('pc.open', event => {
      const detail = (event as CustomEvent).detail || {};
      this.query = String(detail.query || ''); this.offset = 0;
      (document.getElementById('pc-search') as HTMLInputElement).value = this.query;
      void this.refresh().then(() => {
        if (detail.command_id) this.commandButtons.get(String(detail.command_id))?.click();
        else if (detail.autoload_video && this.videoButtons.length === 1) this.videoButtons[0].click();
      });
    });
    document.querySelectorAll('[data-panel]').forEach(button => button.addEventListener('click', () => {
      if ((button as HTMLElement).dataset.panel === 'pc') { void this.refresh(); }
      else this.player.pause();
    }));
    window.addEventListener('pagehide', () => { window.clearTimeout(this.poll); this.player.pause(); });
  }

  private status(text: string): void { document.getElementById('pc-status')!.textContent = text; }

  /** 1:02:03 / 12:34 の形。読めなかった動画は長さを出さない。 */
  private static duration(value: unknown): string {
    const total = Math.round(Number(value));
    if (!Number.isFinite(total) || total <= 0) return '';
    const pad = (n: number) => String(n).padStart(2, '0');
    const minutes = `${Math.floor(total / 60) % 60}:${pad(total % 60)}`;
    return total >= 3600 ? `${Math.floor(total / 3600)}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}` : minutes;
  }

  /** iPhoneから開くURL。控え忘れても、ここを見れば分かるようにしておく。 */
  private showRemote(url: string): void {
    const line = document.getElementById('pc-remote')!;
    line.replaceChildren();
    if (!url) {
      line.textContent = 'iPhoneから使うURLは未設定です。PCで pc_setup.bat を実行し、1（Tailscaleを導入）→ 2（HTTPS接続を有効化）を選んでください。';
      return;
    }
    line.append('iPhoneから開くURL：');
    const link = document.createElement('a');
    link.href = url; link.textContent = url; link.rel = 'noreferrer';
    line.append(link);
  }

  async refresh(): Promise<void> {
    const request = ++this.request;
    this.status('PCの内容を読み込み中…');
    this.next = null;
    (document.getElementById('pc-next') as HTMLButtonElement).disabled = true;
    (document.getElementById('pc-prev') as HTMLButtonElement).disabled = true;
    const list = document.getElementById('pc-videos')!; list.replaceChildren();
    const actions = document.getElementById('pc-commands')!; actions.replaceChildren();
    this.videoButtons = [];
    this.commandButtons.clear();
    try {
      const [status, videos] = await Promise.all([this.api('/pc/status'),
        this.api('/pc/videos?' + new URLSearchParams({ q: this.query, offset: String(this.offset) }))]);
      if (request !== this.request) return;
      this.showRemote(String(status.remote_url || ''));
      for (const item of videos.items) {
        const button = document.createElement('button'); button.type = 'button';
        const length = PCPanel.duration(item.seconds);
        button.textContent = length ? `${item.name}（${length}）` : item.name;
        button.addEventListener('click', async () => {
          button.disabled = true;
          try {
            const ticket = await this.api('/pc/videos/ticket', { method: 'POST', body: JSON.stringify({ folder: item.folder, path: item.path }) });
            this.player.src = this.base + ticket.url; this.player.hidden = false;
            document.getElementById('pc-now-playing')!.textContent = item.name;
            this.player.load();
            this.player.scrollIntoView({ block: 'nearest' });
            this.status('再生ボタンを押してください。');
          } catch (error) { this.status(String(error)); } finally { button.disabled = false; }
        });
        this.videoButtons.push(button);
        list.append(button);
      }
      if (!videos.items.length) list.textContent = status.video_folders.length ? '該当するMP4動画がありません。' : 'PC側の「連携設定」で動画フォルダを追加してください。';
      for (const command of status.commands) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = command.name;
        button.addEventListener('click', async () => {
          if (button.disabled) return;
          button.disabled = true;
          try {
            const prepared = await this.api(`/pc/commands/${encodeURIComponent(command.id)}/prepare`, { method: 'POST' });
            if (!window.confirm(`${prepared.name}\n${prepared.description}\n最大${prepared.timeout_seconds}秒。PCで実行しますか？完了済みの変更は取り消せません。`)) return;
            await this.api('/pc/commands/run', { method: 'POST', body: JSON.stringify({ confirmation: prepared.confirmation, confirmed: true }) });
            await this.jobs();
          } catch (error) { this.status(String(error)); } finally { button.disabled = false; }
        });
        this.commandButtons.set(String(command.id), button);
        const row = document.createElement('article'); row.className = 'memory-card';
        const description = document.createElement('p'); description.textContent = command.description;
        row.append(button, description); actions.append(row);
      }
      if (!status.commands.length) actions.textContent = 'BATは未登録です。PC側の「連携設定」で追加できます。';
      this.next = videos.next_offset;
      (document.getElementById('pc-next') as HTMLButtonElement).disabled = this.next === null;
      (document.getElementById('pc-prev') as HTMLButtonElement).disabled = this.offset === 0;
      this.status(videos.truncated ? 'ファイル数が多いため最初の10,000ファイルを検索しました。対象フォルダを絞ってください。' : '動画は再生ボタン、BATは確認後に実行できます。');
      await this.jobs();
    } catch (error) { if (request === this.request) this.status(String(error)); }
  }

  private async jobs(): Promise<void> {
    window.clearTimeout(this.poll);
    const body = await this.api('/pc/jobs');
    const list = document.getElementById('pc-jobs')!; list.replaceChildren();
    const labels: Record<string, string> = { running: '実行中', completed: '終了', failed: '失敗', stopped: '停止' };
    for (const job of body.items) {
      const item = document.createElement('li');
      item.textContent = `${job.name}：${labels[job.status] || job.status} ${job.exit_code === null ? '' : `（終了コード ${job.exit_code}）`} — ${job.message}`;
      list.append(item);
    }
    if (body.items.some((job: any) => job.status === 'running')) {
      this.poll = window.setTimeout(() => { void this.jobs().catch(() => this.status('実行状況を取得できません。「表示・更新」で確認してください。')); }, 2000);
    }
  }
}
