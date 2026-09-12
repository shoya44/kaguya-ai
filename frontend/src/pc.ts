type Api = (path: string, init?: RequestInit) => Promise<any>;
type Row = Record<string, any>;

const PAGE = 30;

export class PCPanel {
  private offset = 0;
  private next: number | null = null;
  private request = 0;
  private query = '';
  private poll: number | undefined;
  private player = document.getElementById('pc-video') as HTMLVideoElement;
  private fileButtons: HTMLButtonElement[] = [];
  private commandButtons = new Map<string, HTMLButtonElement>();

  constructor(private api: Api, private base: string) {
    document.getElementById('pc-filter')!.addEventListener('submit', event => {
      event.preventDefault();
      this.offset = 0;
      this.query = (document.getElementById('pc-search') as HTMLInputElement).value;
      void this.refresh();
    });
    document.getElementById('pc-next')!.addEventListener('click', () => {
      if (this.next !== null) { this.offset = this.next; void this.refresh(); }
    });
    document.getElementById('pc-prev')!.addEventListener('click', () => {
      this.offset = Math.max(0, this.offset - PAGE);
      void this.refresh();
    });
    this.player.addEventListener('error', () => this.status('再生できません。形式の非対応・接続切れ・リンク期限切れの可能性があります。選び直してください。'));
    window.addEventListener('pc.open', event => {
      const detail = (event as CustomEvent).detail || {};
      this.query = String(detail.query || '');
      this.offset = 0;
      (document.getElementById('pc-search') as HTMLInputElement).value = this.query;
      void this.refresh().then(() => {
        if (detail.command_id) this.commandButtons.get(String(detail.command_id))?.click();
        else if (detail.autoload_video && this.fileButtons.length === 1) this.fileButtons[0].click();
      });
    });
    document.querySelectorAll('[data-panel]').forEach(button => button.addEventListener('click', () => {
      if ((button as HTMLElement).dataset.panel === 'pc') void this.refresh();
      else this.player.pause();
    }));
    window.addEventListener('pagehide', () => { window.clearTimeout(this.poll); this.player.pause(); });
  }

  private status(text: string): void { document.getElementById('pc-status')!.textContent = text; }

  /** 1:02:03 / 12:34 の形。読めなかった動画は長さを出さない。 */
  static duration(value: unknown): string {
    const total = Math.round(Number(value));
    if (!Number.isFinite(total) || total <= 0) return '';
    const pad = (n: number) => String(n).padStart(2, '0');
    const minutes = `${Math.floor(total / 60) % 60}:${pad(total % 60)}`;
    return total >= 3600 ? `${Math.floor(total / 3600)}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}` : minutes;
  }

  /** エクスプローラーと同じ見え方にそろえる（1 KB = 1024 B）。 */
  static size(value: unknown): string {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes <= 0) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let index = 0;
    let size = bytes;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
    return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  }

  /** 「フォルダ名 ＞ 下位フォルダ」。直下のファイルはフォルダ名だけ。 */
  static breadcrumb(folder: string, relative: string): string {
    const parts = String(relative || '').split('/').slice(0, -1);
    return [folder, ...parts].join(' ＞ ');
  }

  /** iPhoneから開くURL。控え忘れても、ここを見れば分かるようにしておく。 */
  private showRemote(url: string): void {
    const line = document.getElementById('pc-remote')!;
    line.replaceChildren();
    if (!url) {
      line.textContent = '未設定です。PCで pc_setup.bat を実行し、1（Tailscaleを導入）→ 2（HTTPS接続を有効化）を選ぶと、ここにURLが出ます。';
      return;
    }
    const link = document.createElement('a');
    link.href = url;
    link.textContent = url;
    link.rel = 'noreferrer';
    line.append(link);
  }

  private fileRow(item: Row): HTMLButtonElement {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'row row-file';
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('aria-hidden', 'true');
    icon.classList.add('file-icon');
    const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    shape.setAttribute('d', 'M4 6h16v12H4zM10 9.5l5 2.5-5 2.5z');
    icon.append(shape);
    const text = document.createElement('span');
    text.className = 'row-text';
    const name = document.createElement('span');
    name.className = 'row-title';
    name.textContent = String(item.name);
    const meta = document.createElement('span');
    meta.className = 'row-meta';
    meta.textContent = [PCPanel.duration(item.seconds), PCPanel.size(item.size)].filter(Boolean).join(' ・ ');
    text.append(name, meta);
    button.append(icon, text);
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      button.disabled = true;
      try {
        const ticket = await this.api('/pc/videos/ticket', {
          method: 'POST', body: JSON.stringify({ folder: item.folder, path: item.path }),
        });
        document.getElementById('pc-player')!.hidden = false;
        this.player.src = this.base + ticket.url;
        document.getElementById('pc-now-playing')!.textContent = `再生中：${item.name}`;
        this.player.load();
        document.getElementById('pc-player')!.scrollIntoView({ block: 'nearest' });
        this.status('再生ボタンを押してください。');
      } catch (error) {
        this.status(error instanceof Error ? error.message : String(error));
      } finally {
        button.disabled = false;
      }
    });
    return button;
  }

  /** フォルダごとにまとめて出す。1件ずつ全経路を書くより、場所が分かりやすい。 */
  private renderFiles(items: Row[], folders: string[]): void {
    const list = document.getElementById('pc-files')!;
    list.replaceChildren();
    this.fileButtons = [];
    if (!items.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = !folders.length
        ? 'フォルダが未登録です。PCで pc_setup.bat を実行し、3（動画フォルダを登録）を選んでください。'
        : this.query ? '該当するMP4動画がありません。検索語を変えてみてください。' : 'このフォルダにMP4動画がありません。';
      list.append(empty);
      return;
    }
    let current = '';
    let group: HTMLElement | null = null;
    for (const item of items) {
      const crumb = PCPanel.breadcrumb(String(item.folder), String(item.path));
      if (crumb !== current) {
        current = crumb;
        const section = document.createElement('section');
        section.className = 'group';
        const heading = document.createElement('h2');
        heading.textContent = crumb;
        group = document.createElement('div');
        group.className = 'group-card';
        section.append(heading, group);
        list.append(section);
      }
      const row = this.fileRow(item);
      this.fileButtons.push(row);
      group!.append(row);
    }
  }

  private renderCommands(commands: Row[]): void {
    const section = document.getElementById('pc-commands-group') as HTMLElement;
    const list = document.getElementById('pc-commands')!;
    list.replaceChildren();
    this.commandButtons.clear();
    // 未登録なら見出しごと出さない。空の枠は「壊れている」ように見える。
    section.hidden = !commands.length;
    for (const command of commands) {
      const row = document.createElement('div');
      row.className = 'row';
      const text = document.createElement('span');
      text.className = 'row-text';
      const name = document.createElement('span');
      name.className = 'row-title';
      name.textContent = String(command.name);
      text.append(name);
      if (command.description) {
        const meta = document.createElement('span');
        meta.className = 'row-meta';
        meta.textContent = String(command.description);
        text.append(meta);
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '実行';
      button.addEventListener('click', async () => {
        if (button.disabled) return;
        button.disabled = true;
        try {
          const prepared = await this.api(`/pc/commands/${encodeURIComponent(command.id)}/prepare`, { method: 'POST' });
          if (!window.confirm(`${prepared.name}\n${prepared.description}\n最大${prepared.timeout_seconds}秒。PCで実行しますか？完了済みの変更は取り消せません。`)) return;
          await this.api('/pc/commands/run', { method: 'POST', body: JSON.stringify({ confirmation: prepared.confirmation, confirmed: true }) });
          await this.jobs();
        } catch (error) {
          this.status(error instanceof Error ? error.message : String(error));
        } finally {
          button.disabled = false;
        }
      });
      this.commandButtons.set(String(command.id), button);
      row.append(text, button);
      list.append(row);
    }
  }

  async refresh(): Promise<void> {
    const request = ++this.request;
    this.status('PCの内容を読み込み中…');
    this.next = null;
    const prev = document.getElementById('pc-prev') as HTMLButtonElement;
    const next = document.getElementById('pc-next') as HTMLButtonElement;
    prev.disabled = true;
    next.disabled = true;
    try {
      const [status, videos] = await Promise.all([
        this.api('/pc/status'),
        this.api('/pc/videos?' + new URLSearchParams({ q: this.query, offset: String(this.offset) })),
      ]);
      if (request !== this.request) return;
      const folders: string[] = Array.isArray(status.video_folders) ? status.video_folders : [];
      this.showRemote(String(status.remote_url || ''));
      this.renderFiles(videos.items || [], folders);
      this.renderCommands(Array.isArray(status.commands) ? status.commands : []);
      this.next = videos.next_offset ?? null;
      prev.disabled = this.offset === 0;
      next.disabled = this.next === null;
      document.getElementById('pc-page')!.textContent = videos.items.length
        ? `${this.offset + 1}〜${this.offset + videos.items.length}件目` : '';
      this.status(videos.truncated
        ? 'ファイル数が多いため最初の10,000件まで検索しました。検索語で絞り込んでください。' : '');
      await this.jobs();
    } catch (error) {
      if (request === this.request) this.status(error instanceof Error ? error.message : String(error));
    }
  }

  private async jobs(): Promise<void> {
    window.clearTimeout(this.poll);
    const body = await this.api('/pc/jobs');
    const list = document.getElementById('pc-jobs')!;
    list.replaceChildren();
    const labels: Record<string, string> = { running: '実行中', completed: '終了', failed: '失敗', stopped: '停止' };
    for (const job of body.items) {
      const item = document.createElement('li');
      item.textContent = `${job.name}：${labels[job.status] || job.status}${job.exit_code === null ? '' : `（終了コード ${job.exit_code}）`} — ${job.message}`;
      list.append(item);
    }
    if (body.items.some((job: Row) => job.status === 'running')) {
      this.poll = window.setTimeout(() => {
        void this.jobs().catch(() => this.status('実行状況を取得できません。検索ボタンで再読み込みしてください。'));
      }, 2000);
    }
  }
}
