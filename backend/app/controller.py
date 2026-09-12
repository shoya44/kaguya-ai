import asyncio
import logging
import time
from contextlib import suppress

from .errors import ChatError
from .mood import Mood, think_delay
from .tuning import FACE_REFRESH_TICKS, PROACTIVE_COMPOSE_SECONDS
from .proactive import Proactive, tokyo_now
from .jobs import Jobs
from . import pc, relationship, tools

class Controller:
    """One active turn and at most one unsaved answer, owned by the process.

    Socket disconnects do not abort a turn. The same ID may be used to recover
    the result. Never automatically regenerate after an ambiguous save failure.
    """
    def __init__(self, memory, llm, broadcast, runtime, mind=None, living=None):
        self.memory = memory
        self.llm = llm
        self.broadcast = broadcast
        self.active = None
        self.task = None
        self.phase = 'idle'
        self.cancel_requested = False
        self.unsaved = None
        self.editing = False
        self.voice_active = False
        self.runtime = runtime
        # かぐやの「いまの状態」。端末ごとではなくここで決めて全端末へ配る。
        self.living = living
        # Optional experimental dependency. Core chat never imports Mind internals.
        self.mind = mind
        # 表情は端末ではなくここで決める。MindがONならMind、OFFならこの簡易判定。
        self.mood = Mood()
        self.mind_face = ''
        self.last_face = ''
        self.mood_ticks = 0
        self.last_living = None
        self.proactive = Proactive(runtime)
        self.jobs = Jobs(memory, llm, runtime, self)
        self.presence = {}
        self.periodic_task = None
        self.reminder_ticks = 0
        self.last_chat_at = time.monotonic()
        self.next_jobs_check = self.last_chat_at + 300
        self.partial_answer = ''
        self.references = []
        self.first_text_ms = None
        self.turn_started = 0
        self.last_progress_at = 0

    def start(self):
        self.periodic_task = asyncio.create_task(self.periodic())

    def face(self, now=None) -> str:
        """いま画面に出すべき表情。MindがONならMindの気分を優先する。"""
        return self.mind_face or self.mood.current(now or tokyo_now())

    async def emit_living(self) -> None:
        """活動・元気さを全端末へ配る。かぐやは1人なので、どの画面でも同じ行動になる。"""
        if not self.living:
            return
        state = self.living.state(tokyo_now())
        if state != self.last_living:
            self.last_living = state
            await self.broadcast({'type': 'living.changed', **state})

    async def emit_mood(self, refresh=False) -> None:
        """表情が変わったときだけ全端末へ配信する。PCとiPhoneで同じ顔になる。"""
        now = tokyo_now()
        if refresh:
            # Mindの感情は半減期が長いので、毎tick読み直す必要はない。
            self.mind_face = self.mind.face(now) if self.mind else ''
        face = self.face(now)
        if face != self.last_face:
            self.last_face = face
            await self.broadcast({'type': 'mood.changed', 'mood': face})

    async def periodic(self):
        while True:
            await asyncio.sleep(5)
            try:
                monotonic_now = asyncio.get_running_loop().time()
                visible = any(until > monotonic_now for until in self.presence.values())
                event = self.proactive.tick(visible, bool(self.active or self.unsaved or self.editing),
                                            topic=lambda: self.mind.due_topic(tokyo_now()) if self.mind else '')
                if event:
                    await self.broadcast(await self.compose_proactive(event))
                self.mood_ticks += 1
                # Mindの感情は半減期が長いので、読み直しは1分に1回でよい。
                # 起動直後の1回目は読む（それまではOFF相当の簡易判定になるため）。
                await self.emit_mood(refresh=self.mood_ticks % FACE_REFRESH_TICKS == 1)
                await self.emit_living()
                await self.deliver_reminders()

                await self.maybe_organize()
            except Exception:
                # Keep the timer alive; details shown by settings/jobs, no raw data logged.
                self.jobs.status = '定期処理を実行できませんでした。設定・保存先を確認してください。'

    async def compose_proactive(self, event: dict) -> dict:
        """定型文のままでは毎朝同じ挨拶になるので、LLMで言い換えてから送る。

        言い換えに失敗したら定型文をそのまま使う。声かけは会話ターンではないため、
        ここでの失敗を利用者へ出さない（出しても直せることがない）。
        """
        text = str(event.get('text') or '')
        mood = self.mind.snapshot(tokyo_now()).get('mood', '') if self.mind else ''
        try:
            async with asyncio.timeout(PROACTIVE_COMPOSE_SECONDS):
                spoken = await self.llm.small_talk(text, event.get('slot', ''), event.get('kind', ''),
                                                   event.get('topic', ''), str(mood or ''))
        except Exception:
            # CancelledErrorはBaseException側なので、ここでは拾わない（停止できる）。
            spoken = ''
        if spoken:
            text = spoken
            self.proactive.compose(text)
        # 画面へ送るのは従来どおり type と text だけ。言い換えの材料は内部に留める。
        return {'type': event['type'], 'text': text}

    async def maybe_organize(self):
        now = time.monotonic()
        if (now < self.next_jobs_check or now - self.last_chat_at < 300
                or not self.runtime.options.auto_jobs or not self.jobs.due()
                or self.jobs.running or self.active or self.unsaved or self.editing):
            return
        self.next_jobs_check = now + 900
        summary = await self.memory.call('GET', '/summary')
        # start() rechecks ownership after the database await.
        if summary['pending'] or self.jobs.weekly_due():
            self.jobs.start()

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
            await self.broadcast({'type': 'reminder.due', 'id': str(item['id']), 'text': item['message']})

    def state(self):
        return {'type': 'state.changed', 'state': 'thinking' if self.active else 'idle',
                'turn_id': self.active['turn_id'] if self.active else None,
                'phase': self.phase, 'partial': self.partial_answer,
                'text': self.active['text'] if self.active else None,
                'client_id': self.active['client_id'] if self.active else None,
                'references': self.references if self.active else [],
                # 端末ごとのlocalStorageではなく、ここを「最後に会った時刻」の基準にする。
                # PCで話した直後にiPhoneを開いて「ちょっと寝てた」と言わないため。
                'last_activity': self.proactive.last_activity.isoformat()}

    async def progress(self, text):
        self.partial_answer = text
        now = time.monotonic()
        if self.first_text_ms is None and text:
            self.first_text_ms = round((now - self.turn_started) * 1000)
        if now - self.last_progress_at >= .1:
            self.last_progress_at = now
            await self.broadcast({'type': 'chat.progress', 'turn_id': self.active['turn_id'],
                                  'partial': text})

    async def send(self, turn: dict, emit):
        if self.editing:
            await emit(ChatError('busy', '音声通話を終了してから送信してください。' if self.voice_active else '記憶の変更完了を待ってください。').event(turn['turn_id']))
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

        # Reserve without yielding; job cancellation happens inside the owned task.
        self.active = dict(turn)
        self.last_chat_at = time.monotonic()
        self.turn_started = self.last_chat_at
        self.partial_answer = ''
        self.references = []
        self.first_text_ms = None
        self.last_progress_at = 0
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
            if self.jobs.running:
                await self.jobs.pause_for_chat()
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
            await self.broadcast({'type': 'chat.accepted', 'turn_id': turn_id,
                                  'text': turn['text'], 'client_id': turn['client_id']})
            if self.living:
                self.living.seen(tokyo_now(), counted=False)
            hint = relationship.style_feedback(turn['text'])
            if hint:
                # 接し方と同じ場所へ残す。演出用なので、保存できなくても会話は続ける。
                with suppress(ChatError):
                    await self.memory.call('POST', '/persona/style', json={'value': hint})
            self.mood.react(turn['text'], tokyo_now())
            mind_context = self.mind.before_reply(turn['text'], tokyo_now()) if self.mind else {}
            await self.emit_mood(refresh=True)
            proactive = self.proactive.activity()
            if self.cancel_requested:
                raise asyncio.CancelledError
            self.phase = 'generating'
            await self.broadcast(self.state())
            # PC操作は任意パス/任意コマンドを実行せず、ファイルタブへ安全に引き渡す。
            # BATは既存の prepare -> ユーザー確認 -> run を必ず通る。
            pc_action = pc.chat_action(turn['text'])
            if pc_action:
                await self.broadcast({'type': 'pc.open', 'target_client_id': str(turn['client_id']),
                                      **pc_action['event']})
                answer = pc_action['reply']
            else:
                answer = await tools.direct_reply(turn['text'], self.memory)
            if answer is None:
                # 重い相談・眠そうな時間帯だけ一拍置いてから書き始める。
                # 天気などの即答（direct_reply）と、ファイルタブへの引き継ぎには挟まない。
                delay = think_delay(turn['text'], str(mind_context.get('現在の気分', '')))
                if delay:
                    await asyncio.sleep(delay)
                context = await self.memory.context()
                hint = ' '.join(row['text'] for row in context[-2:])[:2000]
                recalled = await self.memory.call('GET', '/recall', params={'text': turn['text'], 'context': hint})
                recalled['relationship'] = relationship.context(self.living) if self.living else {}
                if mind_context:
                    recalled['mind'] = mind_context
                self.references = ([{'label': row['topic_key'], 'text': row['summary']}
                                    for row in recalled.get('wisdom', [])[:5]]
                                   + [{'label': row['key'], 'text': row['value']}
                                      for row in recalled.get('persona', []) if row['key'] != 'base_personality'])
                await self.broadcast(self.state())
                answer = await self.llm.reply(context, turn['text'], recalled, proactive,
                                              self.runtime.options.reply_tokens, memory=self.memory, on_text=self.progress)
            self.partial_answer = answer
            self.phase = 'saving'
            await self.broadcast(self.state())
            if self.cancel_requested:
                raise asyncio.CancelledError
            self.unsaved = (turn, answer)
            try:
                await self.memory.complete(turn_id, answer)
            except ChatError:
                await self.broadcast(self.unsaved_event())
                return
            self.unsaved = None
            if self.living:
                self.living.seen(tokyo_now(), counted=True)
            if self.mind:
                self.mind.after_reply(turn['text'], answer, tokyo_now())
            self.proactive.last_activity = tokyo_now()
            await self.broadcast({'type': 'chat.completed', 'turn_id': turn_id,
                                  'text': turn['text'], 'answer': answer, 'references': self.references,
                                  'elapsed_ms': round((time.monotonic() - self.turn_started) * 1000),
                                  'first_text_ms': self.first_text_ms})
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
            logging.getLogger('uvicorn.error').info('chat timing: first_text_ms=%s total_ms=%s',
                self.first_text_ms, round((time.monotonic() - self.turn_started) * 1000))
            self.last_chat_at = time.monotonic()
            self.active = None
            self.phase = 'idle'
            self.partial_answer = ''
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
            if self.living:
                self.living.seen(tokyo_now(), counted=True)
            if self.mind:
                self.mind.after_reply(turn['text'], answer, tokyo_now())
            await self.broadcast({'type': 'chat.completed', 'turn_id': turn_id,
                                  'text': turn['text'], 'answer': answer, 'references': self.references})
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
