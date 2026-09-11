"""iPhone向けUI/UX改修で加えた振る舞いの回帰テスト。

外部APIもDBも使わない。Geminiはトランスポートを差し替えて、
ストリーミングの見え方だけを確認する。
"""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import SecretStr

from app import memory_store, tools
from app.controller import Controller
from app.errors import ChatError
from app.jobs import Jobs, periods
from app.llm import Gemini
from app.persona import memory_prompt
from app.proactive import tokyo_now
from app.runtime import RuntimeStore


def temp_store():
    root = Path(__file__).resolve().parents[2] / '.test-output'
    root.mkdir(exist_ok=True)
    temp = tempfile.TemporaryDirectory(dir=root)
    return temp, RuntimeStore(Path(temp.name))


class RecallTests(unittest.TestCase):
    def test_terms_ignore_width_and_case_differences(self):
        # 全角英数・大文字小文字の違いで取りこぼさない。
        self.assertIn('github', memory_store.recall_terms('ＧｉｔＨｕｂ'))
        self.assertEqual(memory_store.recall_terms('Coffee'), memory_store.recall_terms('ｃｏｆｆｅｅ'))

    def test_longer_terms_come_first_so_specific_words_survive_the_cut(self):
        # 上限で打ち切る際、手がかりの多い長い語から残す。
        terms = memory_store.recall_terms('確定申告の書類はどこ')
        self.assertEqual(terms[0], max(terms, key=len))
        self.assertGreaterEqual(len(terms[0]), len(terms[-1]))

    def test_two_character_slices_allow_japanese_partial_matches(self):
        self.assertIn('申告', memory_store.recall_terms('確定申告'))


class JobPacingTests(unittest.TestCase):
    def test_a_fresh_day_is_due_but_a_just_finished_attempt_is_not(self):
        temp, store = temp_store()
        with temp:
            jobs = Jobs(None, None, store, None)
            now = tokyo_now()
            self.assertTrue(jobs.due(now))
            store.record(job_attempt_at=now.isoformat())
            self.assertFalse(jobs.due(now))
            # 15分あければ再開する。少量ずつ進めるための間隔。
            self.assertTrue(jobs.due(now + timedelta(minutes=16)))

    def test_the_daily_api_budget_stops_further_attempts(self):
        temp, store = temp_store()
        with temp:
            jobs = Jobs(None, None, store, None)
            now = tokyo_now()
            for _ in range(store.options.daily_call_limit):
                store.reserve_call(now.date().isoformat())
            self.assertFalse(jobs.due(now))


class OrganizeTimingTests(unittest.IsolatedAsyncioTestCase):
    def controller(self, store, pending):
        memory = SimpleNamespace(call=AsyncMock(return_value={'pending': pending, 'recent': []}))
        controller = Controller(memory, SimpleNamespace(), AsyncMock(), store)
        controller.jobs.start = lambda manual=False: setattr(controller, 'started', True)
        controller.started = False
        return controller

    async def test_no_organizing_while_the_conversation_is_still_warm(self):
        temp, store = temp_store()
        with temp:
            controller = self.controller(store, pending=5)
            await controller.maybe_organize()
            self.assertFalse(controller.started)
            controller.memory.call.assert_not_awaited()

    async def test_pending_conversations_are_organized_once_the_chat_goes_quiet(self):
        temp, store = temp_store()
        with temp:
            controller = self.controller(store, pending=5)
            controller.last_chat_at -= 400
            controller.next_jobs_check = 0
            await controller.maybe_organize()
            self.assertTrue(controller.started)

    async def test_nothing_pending_means_no_api_call_at_all(self):
        temp, store = temp_store()
        with temp:
            store.record(weekly_done=periods(tokyo_now())[1])
            controller = self.controller(store, pending=0)
            controller.last_chat_at -= 400
            controller.next_jobs_check = 0
            await controller.maybe_organize()
            self.assertFalse(controller.started)


class ScheduleShortcutTests(unittest.IsolatedAsyncioTestCase):
    def memory(self, items):
        return SimpleNamespace(call=AsyncMock(return_value={'items': items}))

    async def test_checking_todays_schedule_answers_without_calling_gemini(self):
        memory = self.memory([{'title': '歯医者', 'start': '2026-09-12T15:00:00+09:00'}])
        answer = await tools.direct_reply('今日の予定は？', memory)
        self.assertIn('歯医者', answer)
        method, path = memory.call.await_args.args
        self.assertEqual((method, path), ('GET', '/calendar'))

    async def test_an_empty_day_still_answers_locally(self):
        self.assertEqual(await tools.direct_reply('明日なにか予定ある？', self.memory([])),
                         'かぐやの予定表には、その期間の予定は入ってないよ。今のところ空っぽ。')

    async def test_adding_a_plan_is_left_to_the_model(self):
        memory = SimpleNamespace(call=AsyncMock())
        self.assertIsNone(await tools.direct_reply('明日15時に歯医者の予定を入れて', memory))
        memory.call.assert_not_awaited()

    async def test_the_word_plan_in_small_talk_does_not_open_the_calendar(self):
        memory = SimpleNamespace(call=AsyncMock())
        self.assertIsNone(await tools.direct_reply('だいたい予定通りに進んでるよ', memory))
        memory.call.assert_not_awaited()

    def test_ranges_follow_the_day_the_user_named(self):
        now = tokyo_now().replace(hour=13, minute=30)
        today = tools._schedule_range('今日の予定', now)
        tomorrow = tools._schedule_range('明日の予定', now)
        self.assertEqual(today[1], tomorrow[0])


class ToolGateTests(unittest.TestCase):
    def names(self, text):
        return {item['name'] for item in tools.declarations_for(text)}

    def test_a_question_with_only_a_date_is_not_treated_as_a_reminder(self):
        # 「教えて」＋日付だけでリマインダー定義を渡すと、雑談までストリーミングできなくなる。
        self.assertEqual(self.names('今日のおすすめの本を教えて'), set())

    def test_a_clock_time_still_registers_a_reminder(self):
        self.assertIn('set_reminder', self.names('明日9時に薬って教えて'))

    def test_an_explicit_reminder_word_works_without_a_clock_time(self):
        self.assertIn('set_reminder', self.names('明日リマインドして'))


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_chat_streams_partial_text_before_the_final_answer(self):
        pieces = ['こん', 'にちは', '。元気？']

        def chunk(text, reason=None):
            part = SimpleNamespace(text=text, thought=False)
            return SimpleNamespace(candidates=[SimpleNamespace(
                content=SimpleNamespace(parts=[part]), finish_reason=reason)])

        async def stream(**_kwargs):
            async def generate():
                for index, piece in enumerate(pieces):
                    yield chunk(piece, 'STOP' if index == len(pieces) - 1 else None)
            return generate()

        llm = Gemini(SimpleNamespace(gemini_api_key=SecretStr(''), gemini_model='test-model',
                                     llm_timeout_seconds=5))
        llm.client = SimpleNamespace(aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=stream)))
        seen = []

        async def collect(text):
            seen.append(text)

        answer = await llm.reply([], 'やあ', {}, None, 512, memory=SimpleNamespace(), on_text=collect)
        self.assertEqual(answer, 'こんにちは。元気？')
        # 部分表示は累積テキストで届く。表示側は毎回置き換えるだけでよい。
        self.assertEqual(seen, ['こん', 'こんにちは', 'こんにちは。元気？'])

    async def test_a_truncated_stream_is_reported_instead_of_silently_saved(self):
        async def stream(**_kwargs):
            async def generate():
                yield SimpleNamespace(candidates=[SimpleNamespace(
                    content=SimpleNamespace(parts=[SimpleNamespace(text='途中まで', thought=False)]),
                    finish_reason='MAX_TOKENS')])
            return generate()

        llm = Gemini(SimpleNamespace(gemini_api_key=SecretStr(''), gemini_model='test-model',
                                     llm_timeout_seconds=5))
        llm.client = SimpleNamespace(aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=stream)))

        async def collect(_text):
            return None

        with self.assertRaises(ChatError) as caught:
            await llm.reply([], 'やあ', {}, None, 512, memory=SimpleNamespace(), on_text=collect)
        self.assertEqual(caught.exception.code, 'output_limit')

    async def test_a_tool_request_keeps_the_non_streaming_path(self):
        llm = Gemini(SimpleNamespace(gemini_api_key=SecretStr(''), gemini_model='test-model',
                                     llm_timeout_seconds=5))
        llm._stream = AsyncMock()
        llm._request = AsyncMock(side_effect=ChatError('api_error', 'stop here'))

        async def collect(_text):
            return None

        with self.assertRaises(ChatError):
            await llm.reply([], 'これを覚えて：お茶が好き', {}, None, 512,
                            memory=SimpleNamespace(), on_text=collect)
        llm._stream.assert_not_awaited()


class SavingPhaseTests(unittest.IsolatedAsyncioTestCase):
    def test_the_saving_broadcast_carries_the_finished_text(self):
        # 部分表示は100msごとに間引くので最後の差分が落ちうる。保存フェーズの
        # 状態通知が完成した本文を運ぶことで、表示が途中で止まらない。
        temp, store = temp_store()
        with temp:
            controller = Controller(SimpleNamespace(), SimpleNamespace(), AsyncMock(), store)
            controller.active = {'turn_id': 't1', 'text': 'やあ', 'client_id': 'c1'}
            controller.partial_answer = 'こんにち'
            controller.phase = 'generating'
            self.assertEqual(controller.state()['partial'], 'こんにち')
            controller.partial_answer = 'こんにちは、元気？'
            controller.phase = 'saving'
            state = controller.state()
            self.assertEqual(state['phase'], 'saving')
            self.assertEqual(state['partial'], 'こんにちは、元気？')


class PromptTests(unittest.TestCase):
    def test_one_off_requests_are_not_treated_as_lasting_preferences(self):
        prompt = memory_prompt({'wisdom': [], 'persona': []})
        self.assertIn('「今回だけ」の依頼は今回の返答だけに適用する', prompt)

    def test_ambiguous_requests_ask_instead_of_guessing_from_memory(self):
        self.assertIn('記憶から決めつけず短く確認する', memory_prompt())


if __name__ == '__main__':
    unittest.main()
