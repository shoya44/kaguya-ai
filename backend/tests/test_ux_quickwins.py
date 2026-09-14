"""相談の継続と整理結果。DB・Geminiを使わず実際の方針とジョブを検証する。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import reply_hints
from app.errors import ChatError
from app.jobs import Jobs, MANUAL_BATCHES, periods
from app.persona import memory_prompt
from app.proactive import tokyo_now


class ConsultationTests(unittest.TestCase):
    def test_explicit_requests_and_bounded_continuation(self):
        for text in ('AとBを比較して', '判断材料を出して'):
            self.assertEqual(reply_hints.intent(text), 'consult')
        history = [{'text': 'AとBを比較して', 'answer': '予算は？'}]
        self.assertEqual(reply_hints.intent('予算は3万円', history), 'consult')
        history.append({'text': '予算は3万円', 'answer': '期限は？'})
        self.assertEqual(reply_hints.intent('期限は来月', history), 'consult')
        self.assertEqual(reply_hints.intent('今日は暑いね', history), 'chat')
        self.assertEqual(reply_hints.intent('それなら聞いてほしいだけ', history), 'listen')
        self.assertEqual(reply_hints.intent('予算は3万円'), 'chat')
        self.assertEqual(reply_hints.intent('どうしよう、決められない'), 'chat')
        self.assertEqual(reply_hints.intent('その場合は？', [{'text': '今日は暑いね'}]), 'chat')

    def test_consultation_suppresses_random_shortening(self):
        recalled = {'mind': {'現在の気分': '眠そう'}, 'relationship': {'慣れ': '少し慣れてきた'}}
        with patch('app.persona.tone_hint', return_value='今回は一言で返してよい。') as tone:
            prompt = memory_prompt(recalled, text='予算は3万円', history=[{'text': '比較して'}])
            tone.assert_not_called()
        values = json.loads(prompt.splitlines()[-1])
        self.assertIn('具体案と理由', values['今回の返し方'])
        self.assertEqual(values['応答方針の参考'], reply_hints.RESPONSE_PLAN['consult'])
        self.assertNotIn('今回は一言', prompt)


class JobResultTests(unittest.IsolatedAsyncioTestCase):
    async def run_job(self, snapshots, summary):
        async def call(method, path, **kwargs):
            if path == '/organize/snapshot':
                return {'raw': snapshots.pop(0) if snapshots else [], 'wisdom': []}
            if path == '/organize/commit':
                return {'processed': 1, 'updated': 0}
            if path == '/summary':
                if isinstance(summary, Exception):
                    raise summary
                return summary
            return {}

        store = SimpleNamespace(data={'ledger': {'weekly_done': periods(tokyo_now())[1]}},
                                options=SimpleNamespace(auto_jobs=True), record=Mock(),
                                reserve_call=Mock(return_value=True), clear_failures=Mock(), note_failure=Mock())
        jobs = Jobs(SimpleNamespace(call=call), SimpleNamespace(organize=AsyncMock(return_value={'items': []})),
                    store, SimpleNamespace(active=None, unsaved=None, editing=False, broadcast=AsyncMock()))
        await jobs.run(manual=True)
        return jobs, store

    async def test_processed_is_distinct_from_updated_and_remaining(self):
        jobs, store = await self.run_job([[{'status': 'completed'}]] * MANUAL_BATCHES, {'pending': 7})
        self.assertIn(f'会話{MANUAL_BATCHES}件／記憶更新0件（延べ）／残り7件', jobs.status)
        self.assertIn('今回の処理完了', jobs.status)
        self.assertEqual(store.record.call_args.kwargs['last_job_status'], jobs.status)

    async def test_empty_queue_reports_zero(self):
        jobs, _ = await self.run_job([], {'pending': 0})
        self.assertIn('会話0件／記憶更新0件（延べ）／残り0件', jobs.status)

    async def test_summary_failure_does_not_report_committed_work_as_failed(self):
        jobs, store = await self.run_job([[{'status': 'completed'}]], ChatError('offline', 'offline'))
        self.assertIn('会話1件', jobs.status)
        self.assertIn('残件数は取得できません', jobs.status)
        store.note_failure.assert_not_called()
