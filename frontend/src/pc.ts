type Api = (path: string, init?: RequestInit) => Promise<any>;

export class PCPanel {
  private request = 0;

  constructor(private api: Api, _base: string) {
    void _base;
    window.addEventListener('pc.open', () => { void this.refresh(); });
    document.querySelectorAll('[data-panel]').forEach(button => button.addEventListener('click', () => {
      if ((button as HTMLElement).dataset.panel === 'pc') void this.refresh();
    }));
  }

  private status(text: string): void { document.getElementById('pc-status')!.textContent = text; }

  static duration(value: unknown): string {
    const total = Math.round(Number(value));
    if (!Number.isFinite(total) || total <= 0) return '';
    const pad = (n: number) => String(n).padStart(2, '0');
    const minutes = `${Math.floor(total / 60) % 60}:${pad(total % 60)}`;
    return total >= 3600 ? `${Math.floor(total / 3600)}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}` : minutes;
  }

  private showRemote(url: string): void {
    const line = document.getElementById('pc-remote')!;
    line.replaceChildren();
    if (!url) return;
    const link = document.createElement('a');
    link.href = url; link.textContent = url; link.rel = 'noreferrer';
    line.append(link);
  }

  async refresh(): Promise<void> {
    const request = ++this.request;
    this.status('読み込み中…');
    const list = document.getElementById('pc-folders')!;
    list.replaceChildren();
    try {
      const status = await this.api('/pc/status');
      if (request !== this.request) return;
      this.showRemote(String(status.remote_url || ''));
      const folders = Array.isArray(status.video_folders) ? status.video_folders : [];
      for (const folder of folders) {
        const row = document.createElement('article');
        row.className = 'memory-card';
        const name = document.createElement('strong');
        name.textContent = String(folder);
        row.append(name);
        list.append(row);
      }
      if (!folders.length) list.textContent = 'フォルダ未登録';
      this.status('');
    } catch (error) {
      if (request === this.request) this.status(error instanceof Error ? error.message : String(error));
    }
  }
}
