import asyncio
from contextlib import suppress

from .errors import ChatError
from .proactive import Proactive, tokyo_now
from .jobs import Jobs


class Controller:
    """One active turn and at most one unsaved answer, owned by the process.

    Socket disconnects do not abort a turn. The same ID may be used to recover
    the result. Never automatically regenerate after an ambiguous save failure.
    """
    def __init__(self, memory, llm, broadcast, runtime):
        self.memory = memory
        self.llm = llm
        self.broadcast = broadcast
        self.active = None
        self.task = None
        self.phase = 'idle'
        self.cancel_requested = False
        self.unsaved = None
        self.editing = False
        self.runtime = runtime
        self.proactive = Proactive(runtime)
        self.jobs = Jobs(memory, llm, runtime, self)
        self.presence = {}
        self.periodic_task = None
        self.reminder_ticks = 0

    def start(self):
        self.periodic_task = asyncio.create_task(self.periodic())

    async def periodic(self):
        started = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(5)
            try:
                now = asyncio.get_running_loop().time()
                visible = any(until > now for until in self.presence.values())
                event = self.proactive.tick(visible, bool(self.active or self.unsaved or self.editing))
                if event:
                    await self.broadcast(event)
                await self.deliver_reminders()
                if (now - started >= 60 and self.runtime.options.auto_jobs and self.jobs.due()
                        and not self.jobs.running and not self.active and not self.unsaved and not self.editing):
                    self.jobs.start()
            except Exception:
                # Keep the timer alive; details shown by settings/jobs, no raw data logged.
                self.jobs.status = '定期処理を実行できませんでした。設定・保存先を確認してください。'

    async def deliver_reminders(self):
        """予約された声かけを配信する。静音・非表示に関わらず出す（本人が時刻を
        指定したものなので、勝手に握り潰さない）。アプリが止まっていた間に過ぎた
        分も、ここで next tick にまとめて拾われる。"""
        self.reminder_ticks += 1
        # 5秒ごとにDBへ問い合わせるのは重いので、30秒に1度だけ見る。
        if self.reminder_ticks % 6:
            return
        try:
            due = await self.memory.call('POST', '/reminders/due',
                                         json={'now': tokyo_now().isoformat()})
        except ChatError:
            # DBが一時的に落ちているだけなら、次の周回で拾えばよい。
            return
        for item in due['items']:
            await self.broadcast({'type': 'reminder.due', 'text': item['message']})

    def state(self):
        return {'type': 'state.changed', 'state': 'thinking' if self.active else 'idle',
                'turn_id': self.active['turn_id'] if self.active else None}

    async def send(self, turn: dict, emit):
        if self.editing:
            await emit(ChatError('busy', '記憶の変更完了を待ってください。').event(turn['turn_id']))
            return
        if self.active:
            if turn['turn_id'] == self.active['turn_id']:
                if any(turn[k] != self.active[k] for k in ('text', 'client_id', 'input_mode')):
                    await emit(ChatError('turn_conflict', '会話IDの内容が一致しません。').event(turn['turn_id']))
                else:
                    await emit(self.state())
            else:
                await emit(ChatError('busy', '返事を待ってね。').event(turn['turn_id']))
            return
        if self.unsaved:
            await emit(self.unsaved_event())
            return
        # Reserve before the first await: no race between clients.
        self.active = dict(turn)
        self.cancel_requested = False
        self.phase = 'preparing'
        self.task = asyncio.create_task(self._run(dict(turn)))

    def unsaved_event(self):
        turn, answer = self.unsaved
        return {'type': 'chat.error', 'turn_id': turn['turn_id'],
                'code': 'save_failed', 'message': '回答を保存できませんでした。保存のみ再試行してください。',
                'text': turn['text'], 'answer': answer, 'client_id': turn['client_id'],
                'retry_after': None}

    async def _mark_failed(self, turn_id, status):
        try:
            await self.memory.fail(turn_id, status)
        except ChatError:
            await self.broadcast(ChatError('status_save_failed', '失敗状態も保存できませんでした。DB復旧後に同じ会話を再試行してください。').event(turn_id))

    async def _run(self, turn):
        turn_id = turn['turn_id']
        admitted = False
        try:
            await self.broadcast(self.state())
            if '静かにしてて' in turn['text']:
                self.runtime.update({'quiet': True})
                await self.broadcast({'type': 'settings.changed', 'options': self.runtime.options.model_dump()})
            row = await self.memory.begin(turn)
            if row['status'] == 'completed':
                await self.broadcast({'type': 'chat.completed', 'turn_id': turn_id,
                                      'text': row['text'], 'answer': row['answer']})
                return
            if row['status'] in ('failed', 'cancelled'):
                raise ChatError('retry_required', '前回は完了しませんでした。再試行ボタンで同じ発言を送れます。')
            admitted = True
            # Share the persisted input so another connected view sees it.
            await self.broadcast({'type': 'chat.accepted', 'turn_id': turn_id,
                                  'text': turn['text'], 'client_id': turn['client_id']})
            context = await self.memory.context()
            # 「それどう思う？」のように指示語だけの発言でも狙った知恵を引けるよう、
            # 直前2往復のユーザー発言を手がかりに足す（今回の発言を先頭に置く）。
            hint = ' '.join([turn['text']] + [row['text'] for row in context[-2:]])[:2000]
            recalled = await self.memory.call('GET', '/recall', params={'text': hint})
            proactive = self.proactive.activity()
            if self.cancel_requested:
                raise asyncio.CancelledError
            self.phase = 'generating'
            answer = await self.llm.reply(context, turn['text'], recalled, proactive,
                                          self.runtime.options.reply_tokens, memory=self.memory)
            self.phase = 'saving'
            if self.cancel_requested:
                raise asyncio.CancelledError
            self.unsaved = (turn, answer)
            try:
                await self.memory.complete(turn_id, answer)
            except ChatError:
                await self.broadcast(self.unsaved_event())
                return
            self.unsaved = None
            self.proactive.last_activity = tokyo_now()
            await self.broadcast({'type': 'chat.completed', 'turn_id': turn_id,
                                  'text': turn['text'], 'answer': answer})
        except asyncio.CancelledError:
            if admitted and not self.unsaved:
                await self._mark_failed(turn_id, 'cancelled')
            await self.broadcast(ChatError('cancelled', '生成を停止しました。外部API側の処理や利用枠の消費が止まる保証はありません。').event(turn_id))
        except ChatError as exc:
            if admitted:
                await self._mark_failed(turn_id, 'failed')
            await self.broadcast(exc.event(turn_id))
        except Exception:
            if admitted and not self.unsaved:
                await self._mark_failed(turn_id, 'failed')
            await self.broadcast(ChatError('internal_error', '処理に失敗しました。再試行できます。').event(turn_id))
        finally:
            self.active = None
            self.phase = 'idle'
            await self.broadcast(self.state())

    async def cancel(self, turn_id, client_id, emit):
        if not self.active or self.active['turn_id'] != turn_id:
            return
        if self.active['client_id'] != client_id:
            await emit(ChatError('not_owner', '送信した端末から停止してください。').event(turn_id))
            return
        if self.phase == 'saving':
            await emit(ChatError('saving', '回答の保存中です。完了を待ってください。').event(turn_id))
            return
        self.cancel_requested = True
        if self.phase == 'generating':
            self.task.cancel()

    async def retry_save(self, turn_id, emit):
        if self.active:
            await emit(ChatError('busy', '処理の完了を待ってください。').event(turn_id))
            return
        if not self.unsaved or self.unsaved[0]['turn_id'] != turn_id:
            await emit(ChatError('answer_unavailable', '未保存の回答はありません。履歴を再読み込みしてください。').event(turn_id))
            return
        turn, answer = self.unsaved
        self.active = turn
        self.phase = 'saving'
        try:
            await self.broadcast(self.state())
            await self.memory.complete(turn_id, answer)
            self.unsaved = None
            await self.broadcast({'type': 'chat.completed', 'turn_id': turn_id,
                                  'text': turn['text'], 'answer': answer})
        except ChatError:
            await emit(self.unsaved_event())
        finally:
            self.active = None
            self.phase = 'idle'
            await self.broadcast(self.state())

    async def close(self):
        if self.periodic_task:
            self.periodic_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.periodic_task
        await self.jobs.close()
        if self.task and not self.task.done():
            if self.phase == 'generating':
                self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
