"""Bounded daily/weekly jobs. Manual and scheduled runs use the same path."""
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta

from .errors import ChatError
from .proactive import tokyo_now


# 自動整理1回で続けるバッチ数。会話が始まれば各バッチの先頭で中断する。
AUTO_BATCHES = 3
# 手動の「今すぐ整理」1回ぶん。これで足りなければもう一度押す。
MANUAL_BATCHES = 5
# 件数が下限に届かなくても、これだけ経った原文は整理する。少ししか話さなかった
# 週の数件が、いつまでも知恵にならないのを避ける。
ORGANIZE_MAX_WAIT = timedelta(hours=24)


def periods(now):
    date = (now - timedelta(hours=3)).date()
    sunday = date - timedelta(days=(date.weekday() + 1) % 7)
    return date.isoformat(), sunday.isoformat()


class Jobs:
    def __init__(self, memory, llm, store, controller):
        self.memory, self.llm, self.store, self.controller = memory, llm, store, controller
        self.task = None
        self.status = '待機中'
        self.cancel_reason = None

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def due(self, now=None):
        now = now or tokyo_now()
        ledger = self.store.data['ledger']
        day = now.date().isoformat()
        used = ledger.get('calls', 0) if ledger.get('call_day') == day else 0
        last = ledger.get('job_attempt_at')
        # 同じ理由で失敗し続ける日は自動では叩かない。原因を直してから
        # 「今すぐ整理」を押せば、手動はいつでも動く。
        if self.store.failing(day):
            return False
        return used < self.store.options.auto_call_limit and (not last or
            now - datetime.fromisoformat(last) >= timedelta(minutes=15))

    def worth_organizing(self, summary) -> bool:
        """いま自動整理を始める価値があるか。

        1回の整理は最大60件をまとめて扱うので、数件のために呼ぶと1件あたりの
        API消費が跳ね上がる。ある程度たまるまで待つ。

        ただし件数だけで待つと、あまり話さなかった週の数件が置き去りになる。
        古くなったものは件数に関わらず整理する。
        """
        pending = int(summary.get('pending') or 0)
        if not pending:
            return False
        if pending >= self.store.options.organize_min_rows:
            return True
        oldest = summary.get('oldest_pending')
        if not oldest:
            return False
        if isinstance(oldest, str):
            oldest = datetime.fromisoformat(oldest)
        return tokyo_now() - oldest >= ORGANIZE_MAX_WAIT

    def weekly_due(self):
        return self.store.data['ledger'].get('weekly_done') != periods(tokyo_now())[1]

    def start(self, manual=False):
        if self.running or self.controller.active or self.controller.unsaved or self.controller.editing:
            raise ChatError('busy', '会話・保存・整理の完了を待ってください。')
        self.cancel_reason = None
        self.task = asyncio.create_task(self.run(manual))

    async def pause_for_chat(self):
        """ユーザー会話を最優先にし、実行中の整理APIをキャンセルして待つ。"""
        if not self.running:
            return
        self.cancel_reason = '会話を優先して記憶整理を中断しました。未処理の原文は保持しています。'
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    async def run(self, manual=False):
        now = tokyo_now()
        daily, weekly = periods(now)
        ledger = self.store.data['ledger']
        weekly_due = ledger.get('weekly_done') != weekly
        self.status = '記憶を整理中'

        def can_continue():
            if not manual and not self.store.options.auto_jobs:
                self.status = '自動整理を停止しました。未処理の原文は保持しています。'
                return False
            if self.controller.active or self.controller.unsaved or self.controller.editing:
                self.store.record(daily_attempt=ledger.get('daily_attempt'), weekly_attempt=ledger.get('weekly_attempt'))
                self.status = '会話のため整理を保留しました。未処理の原文は保持しています。'
                return False
            return True

        try:
            await self.controller.broadcast({'type': 'jobs.changed', 'status': self.status, 'running': True})
            # 同じ自動処理を何度も繰り返さないため、試行日は先に記録する。
            self.store.record(daily_attempt=daily, weekly_attempt=weekly, job_attempt_at=now.isoformat())
            processed = 0
            if manual:
                # 手動は1日の上限で止めないが、1回押すたびに延々と呼び続けない。
                # 足りなければもう一度押せばよい。押した瞬間に何十回も呼ぶ作りだと
                # 押しづらくなる。
                max_batches = MANUAL_BATCHES
            else:
                # 自動実行でも数回は続ける。1回だけだと、15分間隔・会話の切れ目という
                # 条件と重なって1日あたり数十件しか進まない。各回の先頭で
                # can_continue() を見るので、話しかけられた時点で止まる。
                max_batches = max(1, min(AUTO_BATCHES,
                                         self.store.options.auto_call_limit - int(weekly_due)))
            for _ in range(max_batches):
                if not can_continue():
                    return
                snap = await self.memory.call('GET', '/organize/snapshot')
                if not can_continue():
                    return
                if not snap['raw']:
                    break
                if any(row['status'] != 'cancelled' for row in snap['raw']):
                    day = tokyo_now().date().isoformat()
                    if not self.store.reserve_call(day, limited=not manual):
                        self.status = '本日の自動整理の上限に達しました。原文は保持しています。'
                        return
                    try:
                        result = await self.llm.organize(snap)
                    except BaseException:
                        # 使えなかった枠は戻す。戻さないと、直らない理由で
                        # 失敗し続けたときに1日の枠が処理ゼロのまま溶ける。
                        self.store.release_call(day)
                        raise
                else:
                    result = {'items': []}
                saved = await self.memory.call('POST', '/organize/commit', json={'snapshot': snap, 'result': result})
                processed += saved['processed']
            if not self.controller.active and not self.controller.editing and weekly_due:
                snap = await self.memory.call('GET', '/weekly/snapshot')
                if not can_continue():
                    return
                if snap['wisdom'] and snap['persona']:
                    day = tokyo_now().date().isoformat()
                    if not self.store.reserve_call(day, limited=not manual):
                        self.status = '知恵化は完了。接し方の更新は本日の自動整理の上限で保留中です。'
                        return
                    try:
                        result = await self.llm.update_persona(snap)
                    except BaseException:
                        self.store.release_call(day)
                        raise
                    await self.memory.call('POST', '/weekly/commit', json={'snapshot': snap, 'result': result})
                # よくある状態を性格として残す。LLMを呼ばないので利用枠を使わない。
                await self.update_disposition()
                self.store.record(weekly_done=weekly)
            await self.memory.call('POST', '/cleanup')
            self.store.record(daily_done=daily)
            self.store.clear_failures(now.date().isoformat())
            self.status = f'整理完了：{processed}件のユーザー発言を処理しました。'
        except asyncio.CancelledError:
            # 会話を優先して止めただけ。失敗ではないので数えない。
            self.status = self.cancel_reason or '整理を中断しました。未処理の原文は保持しています。'
            raise
        except ChatError as exc:
            self.store.note_failure(now.date().isoformat())
            self.status = f'整理失敗：{exc.message} 未処理の原文は保持しています。'
        except ValueError:
            self.store.note_failure(now.date().isoformat())
            self.status = '整理失敗：Geminiの整理結果が規定形式ではありませんでした。未処理の原文は保持しています。'
        except Exception:
            self.store.note_failure(now.date().isoformat())
            self.status = '整理失敗：内部処理または保存に失敗しました。未処理の原文は保持しています。'
        finally:
            self.cancel_reason = None
            # 日付も残す。設定画面はその日の結果だけを出し、古い状態を残さない。
            self.store.record(last_job_status=self.status, last_job_day=periods(tokyo_now())[0])
            await self.controller.broadcast({'type': 'jobs.changed', 'status': self.status, 'running': False})

    async def update_disposition(self) -> str:
        """「同じ状態が繰り返されると性格になる」を週1回だけ反映する。

        まだ傾向が決まらないうちは何も書かない。演出用なので、書けなくても
        整理そのものは失敗させない。
        """
        mind = self.controller.mind
        line = mind.disposition() if mind else ''
        if not line:
            return ''
        with suppress(Exception):
            await self.memory.call('POST', '/persona/style',
                                   json={'key': 'disposition', 'value': line})
        return line

    async def close(self):
        if self.running:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
