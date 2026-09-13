import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import update_awareness as updates
from app.controller import Controller
from app.persona import memory_prompt
from app.runtime import RuntimeStore
from app.voice import Transcript
import pgtemp


class UpdateAwarenessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        if not pgtemp.available():
            self.skipTest(pgtemp.reason())
        self.db = pgtemp.database()
        self.addCleanup(self.db.close)
        self.runtime = RuntimeStore(self.db)

    def test_greeting_only_and_quiet_does_not_forget_the_update(self):
        self.assertTrue(updates.context(self.runtime, 'こんにちは！')['紹介してよい'])
        self.assertTrue(updates.context(self.runtime)['紹介してよい'])
        for text in ('こんにちは、仕事がつらい', '疲れた', '何が変わった？', '明日の予定を教えて'):
            self.assertFalse(updates.context(self.runtime, text)['紹介してよい'])
        self.runtime.update({'quiet': True})
        self.assertFalse(updates.context(self.runtime, 'こんにちは')['紹介してよい'])
        self.assertEqual(updates.context(self.runtime, '何が変わった？')['変更内容'], list(updates.UPDATE_NOTES))

    def test_saved_introduction_survives_restart_and_new_id_becomes_pending(self):
        updates.acknowledge(self.runtime, 'おかえり。')
        self.assertTrue(updates.context(self.runtime, 'ただいま')['紹介してよい'])
        updates.acknowledge(self.runtime, updates.INTRO + '。')
        restarted = RuntimeStore(self.db)
        self.assertFalse(updates.context(restarted, 'ただいま')['紹介してよい'])
        with patch.object(updates, 'UPDATE_ID', 'test-next-release'):
            self.assertTrue(updates.context(restarted, 'ただいま')['紹介してよい'])

    async def test_failed_conversation_save_does_not_acknowledge_but_retry_does(self):
        memory = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError('test')))
        controller = Controller(memory, SimpleNamespace(), AsyncMock(), self.runtime)
        with self.assertRaises(RuntimeError):
            await controller._complete('test-turn', updates.INTRO)
        self.assertNotIn('introduced_update_id', self.runtime.data['ledger'])
        memory.complete.side_effect = None
        await controller._complete('test-turn', updates.INTRO)
        self.assertEqual(RuntimeStore(self.db).data['ledger']['introduced_update_id'], updates.UPDATE_ID)

    async def test_auxiliary_ledger_failure_does_not_fail_saved_conversation(self):
        memory = SimpleNamespace(complete=AsyncMock())
        controller = Controller(memory, SimpleNamespace(), AsyncMock(), self.runtime)
        with patch.object(self.runtime, 'record', side_effect=RuntimeError('test')):
            with self.assertLogs('app.update_awareness', level='WARNING'):
                await controller._complete('test-turn', updates.INTRO)
        memory.complete.assert_awaited_once()
        self.assertNotIn('introduced_update_id', self.runtime.data['ledger'])

    async def test_voice_acknowledges_only_a_complete_saved_introduction(self):
        controller = SimpleNamespace(memory=SimpleNamespace(begin=AsyncMock(), complete=AsyncMock()),
                                     runtime=self.runtime, living=None, mind=None, broadcast=AsyncMock())
        transcript = Transcript(controller, 'test-client')
        for interrupted in (True, False):
            transcript.text, transcript.answer = 'こんにちは', updates.INTRO
            await transcript.save(interrupted=interrupted)
            self.assertEqual(updates.context(self.runtime, 'こんにちは')['紹介してよい'], interrupted)

    def test_update_remains_available_for_questions_after_introduction(self):
        updates.acknowledge(self.runtime, updates.INTRO)
        recalled = {'app_update': updates.context(self.runtime, '何が変わった？')}
        values = json.loads(memory_prompt(recalled).splitlines()[-1])
        self.assertEqual(values['かぐやの更新メモ']['変更内容'], list(updates.UPDATE_NOTES))
        self.assertFalse(values['かぐやの更新メモ']['紹介してよい'])
