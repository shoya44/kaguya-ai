"""Bounded daily/weekly jobs. Manual and scheduled runs use the same path."""
import asyncio
from datetime import timedelta

from .errors import ChatError
from .proactive import tokyo_now


def periods(now):
    date = (now - timedelta(hours=3)).date()
    sunday = date - timedelta(days=(date.weekday() + 1) % 7)
    return date.isoformat(), sunday.isoformat()


class Jobs:
    def __init__(self, memory, llm, store, controller):
        self.memory, self.llm, self.store, self.controller = memory, llm, store, controller
        self.task = None
        self.status = '待機中'

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def due(self, now=None):
        daily, weekly = periods(now or tokyo_now())
        ledger = self.store.data['ledger']
        return ledger.get('daily_attempt') != daily or ledger.get('weekly_attempt') != weekly

    def start(self, manual=False):
        if self.running or self.controller.active or self.controller.unsaved or self.controller.editing:
            raise ChatError('busy', '会話・保存・整理の完了を待ってください。')
        self.task = asyncio.create_task(self.run(manual))

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
                # A conversation that starts during a snapshot fetch takes priority.
                self.store.record(daily_attempt=ledger.get('daily_attempt'), weekly_attempt=ledger.get('weekly_attempt'))
                self.status = '会話のため整理を保留しました。自動整理は会話後に再開します。'
                return False
            return True

        try:
            # Tell the UI that a run started; only the end was announced before,
            # so the screen (and the character) could not show work in progress.
            await self.controller.broadcast({'type': 'jobs.changed', 'status': self.status, 'running': True})
            # Persist attempts before work. Failures are not retried automatically.
            self.store.record(daily_attempt=daily, weekly_attempt=weekly)
            processed = 0
            # Leave one call available for a due weekly update.
            max_batches = max(1, self.store.options.daily_call_limit - int(weekly_due))
            for _ in range(max_batches):
                if not can_continue():
                    return
                snap = await self.memory.call('GET', '/organize/snapshot')
                if not can_continue():
                    return
                if not snap['raw']:
                    break
                if any(row['status'] != 'cancelled' for row in snap['raw']):
                    if not self.store.reserve_call(tokyo_now().date().isoformat()):
                        self.status = '本日の整理API上限に達しました。原文は保持しています。'
                        return
                    result = await self.llm.organize(snap)
                else:
                    result = {'items': []}
                saved = await self.memory.call('POST', '/organize/commit', json={'snapshot': snap, 'result': result})
                processed += saved['processed']
            if not self.controller.active and not self.controller.editing and weekly_due:
                snap = await self.memory.call('GET', '/weekly/snapshot')
                if not can_continue():
                    return
                if snap['wisdom'] and snap['persona']:
                    if not self.store.reserve_call(tokyo_now().date().isoformat()):
                        self.status = '知恵化は完了。接し方の更新は本日のAPI上限で保留中です。'
                        return
                    result = await self.llm.update_persona(snap)
                    await self.memory.call('POST', '/weekly/commit', json={'snapshot': snap, 'result': result})
                self.store.record(weekly_done=weekly)
            await self.memory.call('POST', '/cleanup')
            self.store.record(daily_done=daily)
            self.status = f'整理完了：{processed}件のユーザー発言を処理しました。'
        except asyncio.CancelledError:
            self.status = '整理を中断しました。未確定の原文は保持しています。'
            raise
        except ChatError as exc:
            self.status = f'整理失敗：{exc.message} 未処理の原文は保持しています。'
        except Exception:
            self.status = '整理結果の検証または保存に失敗しました。未処理の原文は保持しています。'
        finally:
            self.store.record(last_job_status=self.status)
            await self.controller.broadcast({'type': 'jobs.changed', 'status': self.status, 'running': False})

    async def close(self):
        if self.running:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
