import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { isTauri } from './tauri';

type Api = (path: string, init?: RequestInit) => Promise<any>;
type Row = Record<string, any>;

const mindNames: Row = { happiness: 'うれしさ', curiosity: '好奇心', boredom: '退屈',
  affection: '親しみ', jealousy: '嫉妬', concern: '心配', interactions: 'やり取りの回数' };
const mindFields: Row = { name: '名前', value: '値', valence: '好み（0＝苦手・100＝好き）',
  confidence: '確信度', evidence: '根拠の数', text: '表現', count: '登場回数',
  subject: '対象', relation: '関係', object: '相手・内容', strength: '関連の強さ',
  topic: '話題', kind: '種類', quote: 'きっかけの言葉', asked: '声をかけた回数',
  opened_at: '記録した日時', due_at: '声かけの目安', last_asked_at: '最後に声をかけた日時',
  resolved_at: '解決した日時', last_seen_at: '最後に登場した日時', updated_at: '更新日時', key: '項目' };

function mindValue(category: string, key: string, value: any): string {
  if (value === null || value === undefined || value === '') {
    return key === 'resolved_at' ? '未解決' : key === 'last_asked_at' ? 'まだ声をかけていません' : '未登録';
  }
  if (key.endsWith('_at')) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('ja-JP', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  }
  if (key === 'name' && category === 'emotions' || key === 'key' && category === 'meta') {
    return mindNames[value] || String(value);
  }
  if (key === 'kind') return ({ plan: '予定', concern: '気がかり' } as Row)[value] || String(value);
  if (key === 'relation') return ({ likes: '好き', dislikes: '苦手' } as Row)[value] || String(value);
  if (typeof value === 'number') {
    const number = (n: number) => n.toLocaleString('ja-JP', { maximumFractionDigits: 1 });
    if (['valence', 'confidence', 'strength'].includes(key)) return `${number(value * 100)} / 100`;
    if (category === 'emotions' && key === 'value') return `${number(value)} / 100`;
    if (['count', 'asked'].includes(key)) return `${number(value)}回`;
    if (key === 'evidence') return `${number(value)}件`;
    return number(value);
  }
  return String(value);
}

export class Controls {
  private layer = 'wisdom';
  private offset = 0;
  private nextOffset: number | null = null;
  private memoryRequest = 0;
  private action: (() => Promise<void>) | null = null;
  private dialog = document.getElementById('memory-dialog') as HTMLDialogElement;

  constructor(private api: Api, private onOptions?: (options: Row) => void) {
    document.querySelectorAll<HTMLButtonElement>('[data-panel]').forEach(button => {
      button.addEventListener('click', () => this.open(button.dataset.panel!));
    });
    document.getElementById('quiet-btn')!.addEventListener('click', () => this.toggleQuiet());
    document.getElementById('organize-btn')!.addEventListener('click', () => this.perform(async () => {
      await this.api('/jobs/run', { method: 'POST' });
      this.message('記憶の整理を開始しました。完了すると結果を表示します。');
      await this.refreshSettings();
    }));
    document.getElementById('memory-filter')!.addEventListener('submit', event => {
      event.preventDefault();
      this.layer = (document.getElementById('memory-layer') as HTMLSelectElement).value;
      this.offset = 0;
      this.perform(() => this.refreshMemories());
    });
    document.getElementById('memory-layer')!.addEventListener('change', () => {
      this.layer = (document.getElementById('memory-layer') as HTMLSelectElement).value;
      this.offset = 0;
      this.perform(() => this.refreshMemories());
    });
    document.getElementById('memory-next')!.addEventListener('click', () => {
      if (this.nextOffset !== null) { this.offset = this.nextOffset; this.perform(() => this.refreshMemories()); }
    });
    document.getElementById('memory-prev')!.addEventListener('click', () => {
      this.offset = Math.max(0, this.offset - 30); this.perform(() => this.refreshMemories());
    });
    document.getElementById('dialog-cancel')!.addEventListener('click', () => this.dialog.close());
    document.getElementById('dialog-confirm')!.addEventListener('click', () => this.perform(async () => {
      const button = document.getElementById('dialog-confirm') as HTMLButtonElement;
      button.disabled = true;
      try {
        await this.action?.();
        this.dialog.close();
        this.message('変更を保存しました。');
        if (!(document.getElementById('memories-panel') as HTMLElement).hidden) await this.refreshMemories();
      } finally { button.disabled = false; }
    }));
    if (isTauri()) {
      listen<string>('ui.action', event => {
        if (event.payload === 'quiet') this.toggleQuiet();
        else this.open(event.payload);
      }).catch(() => this.message('トレイ操作を接続できませんでした。画面上のボタンを使ってください。'));
    }
  }

  private perform(action: () => Promise<void>): void {
    action().catch(error => this.message(error instanceof Error ? error.message : String(error)));
  }

  private statusTimer: number | null = null;

  // 「設定を保存しました」等が画面に残り続けないよう、一定時間で消す。
  // ダイアログ内のエラーは操作の文脈が続くため残す。
  private message(text: string): void {
    document.getElementById('controls-status')!.textContent = text;
    document.getElementById('dialog-error')!.textContent = this.dialog.open ? text : '';
    if (this.statusTimer !== null) window.clearTimeout(this.statusTimer);
    this.statusTimer = window.setTimeout(() => {
      document.getElementById('controls-status')!.textContent = '';
    }, 6000);
  }

  open(panel: string): void {
    for (const name of ['chat', 'settings', 'memories', 'help', 'pc']) {
      document.getElementById(name === 'chat' ? 'chat' : `${name}-panel`)!.hidden = name !== panel;
    }
    document.getElementById('character-view')!.hidden = panel !== 'chat';
    document.querySelectorAll<HTMLButtonElement>('[data-panel]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.panel === panel));
    });
    if (panel === 'settings') this.perform(() => this.refreshSettings());
    if (panel === 'memories') this.perform(() => this.refreshMemories());
  }

  async refreshSettings(): Promise<void> {
    const body = await this.api('/settings');
    await this.applyOptions(body.options);
    document.getElementById('job-status')!.textContent = jobStatusLine(body);
    document.getElementById('model-info')!.textContent = `モデル：${body.model || '未設定'} ／ API設定：${body.configured ? 'あり' : 'なし'}`;
    (document.getElementById('organize-btn') as HTMLButtonElement).disabled = body.jobs.running;
    this.renderReminders(body.reminders ?? []);
  }

  private renderReminders(items: Row[]): void {
    const list = document.getElementById('reminder-list')!;
    list.replaceChildren();
    if (!items.length) {
      list.textContent = '予約はありません。';
      return;
    }
    for (const item of items) {
      const row = document.createElement('div');
      row.className = 'reminder-row';
      const when = document.createElement('strong');
      when.textContent = new Date(item.due_at).toLocaleString('ja-JP', {
        month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
      });
      const what = document.createElement('span');
      what.textContent = item.message;
      row.append(when, what, this.button('取り消す', async () => {
        await this.api(`/reminders/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
        await this.refreshSettings();
        this.message('予約を取り消しました。');
      }));
      list.append(row);
    }
  }

  async applyOptions(options: Row): Promise<void> {
    // 設定画面に入力欄は置いていない。値はサーバーの既定値をそのまま使い、
    // 画面に効くもの（文字サイズ・静音・最前面）だけをここで反映する。
    this.onOptions?.(options);
    document.documentElement.style.setProperty('--font-size', `${options.font_size}px`);
    // アイコンボタンなので中身（SVG）は入れ替えない。色（aria-pressed）で
    // 現在の状態を、ラベルで押したときの動作を示す。
    const quietBtn = document.getElementById('quiet-btn')!;
    const quietLabel = options.quiet ? '声かけを再開する' : '静かにする';
    quietBtn.setAttribute('aria-label', quietLabel);
    quietBtn.setAttribute('title', quietLabel);
    quietBtn.setAttribute('aria-pressed', String(!!options.quiet));
    if (options.quiet) document.getElementById('proactive-bubble')!.hidden = true;
    if (isTauri()) await invoke('window_topmost', { enabled: options.always_on_top });
  }

  toggleQuiet(): void {
    this.perform(async () => {
      const body = await this.api('/settings');
      const changed = await this.api('/settings', { method: 'PATCH', body: JSON.stringify({ quiet: !body.options.quiet }) });
      await this.applyOptions(changed.options);
      this.message(changed.options.quiet ? '声かけを停止しました。会話は続けられます。' : '声かけを再開しました。設定した間隔が経ってから話しかけます。');
    });
  }

  async refreshMemories(): Promise<void> {
    const request = ++this.memoryRequest;
    const layer = this.layer;
    const offset = this.offset;
    const q = (document.getElementById('memory-search') as HTMLInputElement).value;
    const list = document.getElementById('memory-list')!;
    list.replaceChildren();
    list.textContent = '記憶を読み込み中…';
    list.setAttribute('aria-busy', 'true');
    this.nextOffset = null;
    (document.getElementById('memory-next') as HTMLButtonElement).disabled = true;
    (document.getElementById('memory-prev') as HTMLButtonElement).disabled = true;
    document.getElementById('memory-page')!.textContent = '';
    // 概要の取得失敗だけで、個別の記憶まで閲覧不能にしない。
    const summary = this.refreshSummary().catch(() => {
      if (request === this.memoryRequest) {
        document.getElementById('memory-summary')!.textContent = '記憶の整理状況を取得できませんでした。';
      }
    });
    let body;
    try {
      body = await this.api(`/memories/${layer}?${new URLSearchParams({ q, offset: String(offset) })}`);
      await summary;
    } catch (error) {
      if (request !== this.memoryRequest) return;
      list.textContent = '記憶を取得できませんでした。「表示・更新」で再試行してください。';
      throw error;
    } finally {
      if (request === this.memoryRequest) list.setAttribute('aria-busy', 'false');
    }
    if (request !== this.memoryRequest) return;
    list.replaceChildren();
    for (const row of body.items as Row[]) {
      const card = document.createElement('article'); card.className = 'memory-card';
      if (this.layer === 'mind') {
        const categories: Row = { emotions: '感情', traits: '好み・傾向', phrases: 'よく使う表現',
          graph_edges: '関連情報', open_loops: '気にかけている話題', meta: '内部記録' };
        const title = document.createElement('strong');
        const name = ['emotions', 'meta'].includes(row.category) ? mindNames[row.title] || row.title
          : row.category === 'graph_edges' ? `${row.data.subject} → ${mindValue(row.category, 'relation', row.data.relation)} → ${row.data.object}` : row.title;
        title.textContent = `${categories[row.category] || row.category} ／ ${name}`;
        const data = document.createElement('dl'); data.className = 'mind-fields';
        // 表示順は内容→数値→日時。未知の項目も末尾に残す。
        const keys = [...Object.keys(mindFields).filter(key => key in row.data),
          ...Object.keys(row.data).filter(key => !(key in mindFields))];
        for (const key of keys) {
          const label = document.createElement('dt'); label.textContent = mindFields[key] || key;
          if (row.category === 'emotions' && key === 'value') label.textContent = '強さ';
          if (row.category === 'meta' && row.data.key === 'interactions' && key === 'value') label.textContent = '回数';
          const value = document.createElement('dd'); value.textContent = mindValue(row.category, key, row.data[key]);
          data.append(label, value);
        }
        card.append(title, data);
        list.append(card);
        continue;
      }
      const title = document.createElement('strong');
      const personaLabels: Row = { reply_style: '返答の長さ・話し方', addressing: 'あなたの呼び方', support_style: '相談するときの接し方', base_personality: 'かぐやの基本性格' };
      title.textContent = this.layer === 'raw' ? `${row.role === 'user' ? 'あなた' : 'かぐや'} ／ ${row.status}`
        : row.topic_key || personaLabels[row.key] || row.key;
      const value = document.createElement('p'); value.textContent = row.content || row.summary || String(row.value);
      const info = document.createElement('small');
      info.textContent = `${new Date(row.created_at || row.updated_at).toLocaleString('ja-JP')} ／ 更新番号 ${row.revision}${row.locked ? ' ／ 自動更新から保護' : ''}`;
      card.append(title, value, info);
      if (row.evidence || row.source_wisdom_ids || row.processing_reason) {
        const details = document.createElement('details');
        const summary = document.createElement('summary'); summary.textContent = '根拠・処理情報';
        const pre = document.createElement('pre');
        pre.textContent = JSON.stringify({ kind: row.kind, support_level: row.support_level, evidence: row.evidence,
          source_wisdom_ids: row.source_wisdom_ids, previous_value: row.previous_value,
          processed_at: row.processed_at, processing_reason: row.processing_reason }, null, 2);
        details.append(summary, pre); card.append(details);
      }
      if (row.key !== 'base_personality') {
        const actions = document.createElement('div'); actions.className = 'button-row';
        // 層ごとに役割が違うので操作名も変える。知恵＝訂正／もう当てはまらない、
        // 接し方＝変更／元に戻す、会話履歴＝訂正／削除。
        const editLabel = this.layer === 'persona' ? '変更' : '訂正';
        const dropLabel = this.layer === 'wisdom' ? 'もう当てはまらない' : '削除・関連記憶も取消';
        if (this.layer !== 'raw' || row.role === 'user') actions.append(this.button(editLabel, () => this.edit(row, false)));
        if (this.layer !== 'persona') actions.append(this.button(dropLabel, () => this.edit(row, true)));
        if (this.layer === 'persona' && row.previous_value !== null) {
          actions.append(this.button('元に戻す', async () => {
            this.confirm('直前の接し方へ戻しますか？', `戻す内容：${row.previous_value}。自動更新から保護します。`, async () => {
              await this.api(`/memories/persona/${encodeURIComponent(row.key)}/restore`, {
                method: 'POST', body: JSON.stringify({ revision: row.revision, confirmed: true }),
              });
            });
          }));
        }
        card.append(actions);
      }
      list.append(card);
    }
    if (!body.items.length) list.textContent = '該当する記憶はありません。';
    this.nextOffset = body.next_offset;
    (document.getElementById('memory-next') as HTMLButtonElement).disabled = this.nextOffset === null;
    (document.getElementById('memory-prev') as HTMLButtonElement).disabled = this.offset === 0;
    document.getElementById('memory-page')!.textContent = body.items.length
      ? `${offset + 1}〜${offset + body.items.length}件目` : '0件';
  }

  async refreshSummary(): Promise<void> {
    const body = await this.api('/memory-summary');
    // 件数だけを出す。整理の進み方や自動整理の可否は設定タブの担当。
    document.getElementById('memory-summary')!.textContent = `長期記憶反映待ち：${body.pending ?? 0}件`;
    const list = document.getElementById('recent-memory-list')!;
    list.replaceChildren();
    for (const row of body.recent) {
      const item = document.createElement('li');
      item.textContent = `${row.topic_key}：${row.summary}`;
      list.appendChild(item);
    }
    if (!body.recent.length) list.textContent = '長期記憶はまだありません。「これを覚えて」と話しかけられます。';
  }

  private button(label: string, action: () => Promise<void>): HTMLButtonElement {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
    button.addEventListener('click', () => {
      if (button.disabled) return;
      button.disabled = true;
      this.perform(async () => {
        try { await action(); } finally { button.disabled = false; }
      });
    });
    return button;
  }

  private confirm(title: string, description: string, action: () => Promise<void>): void {
    document.getElementById('dialog-title')!.textContent = title;
    document.getElementById('dialog-description')!.textContent = description;
    document.getElementById('dialog-error')!.textContent = '';
    document.getElementById('memory-value')!.hidden = true;
    document.getElementById('memory-lock-label')!.hidden = true;
    this.action = action; this.dialog.showModal();
  }

  private async edit(row: Row, deleting: boolean): Promise<void> {
    const layer = this.layer;
    const key = encodeURIComponent(row.id || row.key);
    const effect = await this.api(`/memories/${layer}/${key}/impact`);
    const current = effect.selected;
    const description = `対象：${current.content || current.summary || current.value}\n` +
      `影響：元の会話 ${effect.raw_turns.length}往復、知恵 ${effect.wisdom_ids.length}件、接し方 ${effect.persona_keys.length}件。\n` +
      (layer === 'raw' && !deleting ? '元発言は訂正して未処理に戻し、古い回答と派生記憶を削除します。' :
        '古い情報の復活を防ぐため、根拠の会話・関連する知恵を削除し、派生した接し方と過去値も取り消します。') +
      '\nこの操作は取り消せません。';
    const title = deleting ? (layer === 'wisdom' ? 'この記憶はもう当てはまりませんか？' : '記憶を削除しますか？')
      : layer === 'persona' ? 'かぐやの接し方を変更する' : '記憶を訂正する';
    this.confirm(title, description, async () => {
      await this.api(`/memories/${layer}/${key}`, { method: 'PATCH', body: JSON.stringify({
        revision: current.revision, confirmed: true, delete: deleting,
        impact_token: effect.impact_token,
        value: (document.getElementById('memory-value') as HTMLTextAreaElement).value,
        locked: (document.getElementById('memory-lock') as HTMLInputElement).checked,
      }) });
    });
    const input = document.getElementById('memory-value') as HTMLTextAreaElement;
    input.hidden = deleting; input.value = current.content || current.summary || String(current.value);
    input.maxLength = layer === 'raw' ? 2000 : 400;
    document.getElementById('memory-lock-label')!.hidden = deleting || layer !== 'persona';
    (document.getElementById('memory-lock') as HTMLInputElement).checked = true;
  }
}

/** 設定画面に出す1行。前回いつ・どうなったか・今日どれだけ使ったか。 */
function jobStatusLine(body: any): string {
  const jobs = body.jobs ?? {};
  if (jobs.running) return '整理中…';
  const used = `本日 ${jobs.calls_today ?? 0} 回（自動は ${body.options?.auto_call_limit ?? '-'} 回まで）`;
  const result = jobs.last_status || jobs.status;
  if (!result) return `まだ整理していません。${used}`;
  // いつのものか分からないと、「上限に達しました」が今のことなのか
  // 何日も前のことなのか判断できない。
  return `${whenLabel(jobs.last_at)}${result}／${used}`;
}

function whenLabel(value: string | undefined): string {
  if (!value) return '';
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return '';
  const now = new Date();
  const sameDay = at.toDateString() === now.toDateString();
  const time = `${at.getHours()}:${String(at.getMinutes()).padStart(2, '0')}`;
  return sameDay ? `今日 ${time}　` : `${at.getMonth() + 1}/${at.getDate()} ${time}　`;
}
