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

from app import memory_store
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


class PromptTests(unittest.TestCase):
    def test_one_off_requests_are_not_treated_as_lasting_preferences(self):
        prompt = memory_prompt({'wisdom': [], 'persona': []})
        self.assertIn('「今回だけ」の依頼は今回の返答だけに適用する', prompt)

    def test_ambiguous_requests_ask_instead_of_guessing_from_memory(self):
        self.assertIn('記憶から決めつけず短く確認する', memory_prompt())


if __name__ == '__main__':
    unittest.main()
