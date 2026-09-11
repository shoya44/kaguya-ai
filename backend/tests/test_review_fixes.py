import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app import memory_store, project_inspector, tools
from app.controller import Controller


class ReviewFixTests(unittest.IsolatedAsyncioTestCase):
    def controller(self):
        runtime = SimpleNamespace(data={'ledger': {}}, record=MagicMock(),
                                  options=SimpleNamespace(reply_tokens=1024))
        memory = SimpleNamespace(begin=AsyncMock(return_value={'status': 'pending'}),
                                 context=AsyncMock(return_value=[]), call=AsyncMock(return_value={}),
                                 complete=AsyncMock(), fail=AsyncMock())
        return Controller(memory, SimpleNamespace(reply=AsyncMock(return_value='reply')),
                          AsyncMock(), runtime)

    @staticmethod
    def turn(id):
        return {'turn_id': id, 'client_id': id, 'text': 'こんにちは', 'input_mode': 'text'}

    async def test_two_clients_during_job_cancellation_admit_only_one_turn(self):
        controller = self.controller()
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause():
            entered.set()
            await release.wait()

        controller.jobs = SimpleNamespace(running=True, pause_for_chat=AsyncMock(side_effect=pause))
        first = asyncio.create_task(controller.send(self.turn('first'), AsyncMock()))
        await asyncio.wait_for(entered.wait(), 1)
        emit = AsyncMock()
        second = asyncio.create_task(controller.send(self.turn('second'), emit))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        await controller.task
        controller.memory.begin.assert_awaited_once_with(self.turn('first'))
        controller.jobs.pause_for_chat.assert_awaited_once()
        self.assertEqual(emit.await_args.args[0]['code'], 'busy')
        self.assertIsNone(controller.active)

    async def test_direct_answer_skips_history_recall_and_llm_but_is_saved(self):
        controller = self.controller()
        with patch('app.controller.tools.direct_reply', AsyncMock(return_value='weather')):
            await controller.send(self.turn('first'), AsyncMock())
            await controller.task
        controller.memory.context.assert_not_awaited()
        controller.memory.call.assert_not_awaited()
        controller.llm.reply.assert_not_awaited()
        controller.memory.complete.assert_awaited_once_with('first', 'weather')

    async def test_weather_routing_preserves_settings_smalltalk_and_reminders(self):
        memory = SimpleNamespace(call=AsyncMock())
        with patch('app.tools.quick_tools.run', AsyncMock(return_value={})) as run:
            for text in ('天気の場所を横浜にして', '傘を買った', 'いい天気だね',
                         '明日9時に天気を教えて', '天気が良かったことを覚えて'):
                self.assertIsNone(await tools.direct_reply(text, memory), text)
            run.assert_not_awaited()
            for text in ('今日の天気は？', '今日の天気を教えて', '傘いる？', '雨降るかな'):
                await tools.direct_reply(text, memory)
            self.assertEqual(run.await_count, 4)

    def test_reminder_poll_does_not_mark_delivered_and_ack_is_idempotent(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [{'id': 'one', 'message': 'test'}]
        first = memory_store.take_due_reminders(conn, '2026-09-12T10:00:00+09:00')
        self.assertEqual(first, memory_store.take_due_reminders(conn, '2026-09-12T10:01:00+09:00'))
        self.assertNotIn('UPDATE', conn.execute.call_args.args[0])
        memory_store.acknowledge_reminder(conn, 'one')
        sql, params = conn.execute.call_args.args
        self.assertIn('delivered_at IS NULL', sql)
        self.assertEqual(params, ('one',))

    def test_search_excludes_legacy_and_test_output_but_explicit_legacy_read_works(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'docs').mkdir()
            (root / '.test-output').mkdir()
            (root / 'README.md').write_text('current target', encoding='utf-8')
            legacy = 'docs/kaguya_ai_handoff_v2.md'
            (root / legacy).write_text('legacy target', encoding='utf-8')
            (root / '.test-output/result.txt').write_text('test target', encoding='utf-8')
            with patch.object(project_inspector, 'ROOT', root):
                matches = project_inspector.project_search('target')['matches']
                self.assertEqual([row['path'] for row in matches], ['README.md'])
                self.assertTrue(project_inspector.project_read(legacy)['ok'])
