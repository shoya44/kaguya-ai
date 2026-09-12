import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { isTauri } from './tauri';

type Api = (path: string, init?: RequestInit) => Promise<any>;
type Row = Record<string, any>;

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
    document.getElementById('options-form')!.addEventListener('submit', event => {
      event.preventDefault();
      this.perform(async () => {
        const form = new FormData(event.currentTarget as HTMLFormElement);
        const options = {
          quiet: form.has('quiet'), auto_jobs: form.has('auto_jobs'), mind_enabled: form.has('mind_enabled'),
          always_on_top: form.has('always_on_top'),
          proactive_minutes: Number(form.get('proactive_minutes')), daily_call_limit: Number(form.get('daily_call_limit')),
          reply_tokens: Number(form.get('reply_tokens')), font_size: Number(form.get('font_size')),
        };
        const result = await this.api('/settings', { method: 'PATCH', body: JSON.stringify(options) });
        await this.applyOptions(result.options);
        this.renderMind(result.mind);
        this.message('設定を保存しました。');
      });
    });
    // この1項目だけは切り替えた時点で反映する。かぐやの内面を動かすスイッチなので、
    // 保存ボタンを押し忘れて「入れたのに効かない」となるのを避ける。
    document.getElementById('mind-enabled')!.addEventListener('change', event => {
      const field = event.currentTarget as HTMLInputElement;
      const wanted = field.checked;
      this.perform(async () => {
        try {
          const result = await this.api('/settings', { method: 'PATCH', body: JSON.stringify({ mind_enabled: wanted }) });
          await this.applyOptions(result.options);
          this.renderMind(result.mind);
          this.message(wanted ? 'Kaguya Mindを使います。' : 'Kaguya Mindを止めました。');
        } catch (error) {
          field.checked = !wanted;
          throw error;
        }
      });
    });
    document.getElementById('mind-reset-btn')!.addEventListener('click', () => {
      this.confirm('Kaguya Mindの蓄積を消しますか？',
        'かぐやの感情・好み・気にかけていることだけを消します。会話・記憶・かぐやの接し方は消えません。',
        async () => {
          // 完了メッセージは共通の確認ダイアログ側が出す。
          const result = await this.api('/mind', { method: 'DELETE' });
          this.renderMind(result.mind);
        });
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
    for (const name of ['chat', 'settings', 'memories', 'help']) {
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
    this.renderMind(body.mind);
    document.getElementById('job-status')!.textContent = `${body.jobs.running ? '整理中' : body.jobs.last_status || body.jobs.status}（本日 ${body.jobs.calls_today}/${body.options.daily_call_limit} 回）`;
    document.getElementById('model-info')!.textContent = `モデル：${body.model || '未設定'} ／ API設定：${body.configured ? 'あり' : 'なし'}`;
    (document.getElementById('organize-btn') as HTMLButtonElement).disabled = body.jobs.running;
    this.renderReminders(body.reminders ?? []);
  }

  private renderMind(mind: Row | undefined): void {
    const target = document.getElementById('mind-status');
    if (!target) return;
    if (!mind?.enabled) {
      target.textContent = 'OFF：従来どおりの会話・記憶・Relationship Memoryで動作します。';
      return;
    }
    if (mind.status !== 'ok') {
      target.textContent = 'ON：Mindの状態を読み取れませんでした。会話本体はそのまま動作します。';
      return;
    }
    const traits = Array.isArray(mind.traits) ? mind.traits.slice(0, 4).map((item: Row) => `${item.name}=${item.stance}`).join('、') : '';
    const suffix = traits ? ` ／ 好み：${traits}` : ' ／ 好みはまだ育ち始めたところ';
    const open = Number(mind.stats?.open_loops ?? 0);
    const loops = open ? ` ／ 気にかけている話題：${open}件` : '';
    target.textContent = `ON：${mind.mood ?? 'いつも通り'} ／ ${mind.growth ?? ''}${suffix}${loops}`;
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
    this.onOptions?.(options);
    const form = document.getElementById('options-form') as HTMLFormElement;
    for (const [key, value] of Object.entries(options)) {
      const field = form.elements.namedItem(key) as HTMLInputElement | null;
      if (!field) continue;
      if (typeof value === 'boolean') field.checked = value;
      else field.value = String(value);
    }
    document.documentElement.style.setProperty('--font-size', `${options.font_size}px`);
    // 色（aria-pressed）で現在の状態、文言で押したときの動作を示す。
    const quietBtn = document.getElementById('quiet-btn')!;
    quietBtn.textContent = options.quiet ? '静音中（解除）' : '静かにする';
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
    await this.refreshSummary();
    const body = await this.api(`/memories/${layer}?${new URLSearchParams({ q, offset: String(offset) })}`);
    if (request !== this.memoryRequest) return;
    const list = document.getElementById('memory-list')!;
    list.replaceChildren();
    for (const row of body.items as Row[]) {
      const card = document.createElement('article'); card.className = 'memory-card';
      if (this.layer === 'mind') {
        const categories: Row = { emotions: '感情', traits: '好み・傾向', phrases: 'よく使う表現',
          graph_edges: '関連情報', open_loops: '気にかけている話題', meta: '内部記録' };
        const title = document.createElement('strong');
        title.textContent = `${categories[row.category] || row.category} ／ ${row.title}`;
        const data = document.createElement('pre');
        data.textContent = JSON.stringify(row.data, null, 2);
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
    document.getElementById('memory-page')!.textContent = `${this.offset + 1}件目から表示`;
  }

  async refreshSummary(): Promise<void> {
    const body = await this.api('/memory-summary');
    document.getElementById('memory-summary')!.textContent = body.running ? '会話は保存済み。長期記憶へ整理しています。'
      : body.pending ? `会話は保存済み。${body.pending}件が長期記憶への反映待ちです。${body.auto ? '会話のない時間に整理します。' : '自動整理は停止中です。'}`
      : '保存された会話の長期記憶への整理は完了しています。';
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
    button.addEventListener('click', () => this.perform(action)); return button;
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
