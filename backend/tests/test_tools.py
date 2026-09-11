import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from types import SimpleNamespace

from app import tools
from app.proactive import JST


class ToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 11, 20, 15, tzinfo=JST)
        self.memory = SimpleNamespace(call=AsyncMock(return_value={'ok': True}))

    def test_naive_time_is_japan_time_and_range_is_limited(self):
        self.assertEqual(tools.parse_due('2026-09-12T09:00:00', self.now).tzinfo, JST)
        self.assertEqual(tools.parse_due('2026-09-12T09:00:00+09:00', self.now).hour, 9)
        for value in ('2026-09-10T09:00:00', '2030-01-01T09:00:00', 'あした', None):
            with self.assertRaises(ValueError):
                tools.parse_due(value, self.now)

    async def test_reminder_is_stored_once_with_absolute_time(self):
        result = await tools.run('set_reminder', {'at': '2026-09-12T09:00:00', 'message': '薬を飲む'},
                                 self.memory, self.now)
        self.assertTrue(result['ok'])
        method, path = self.memory.call.await_args.args
        self.assertEqual((method, path), ('POST', '/reminders'))
        self.assertEqual(self.memory.call.await_args.kwargs['json']['message'], '薬を飲む')

    async def test_bad_input_and_failures_never_raise(self):
        self.assertFalse((await tools.run('set_reminder', {'at': '2026-09-12T09:00:00', 'message': ' '},
                                          self.memory, self.now))['ok'])
        self.assertFalse((await tools.run('remember', {'topic': '飲み物'}, self.memory, self.now))['ok'])
        self.assertFalse((await tools.run('unknown', {}, self.memory, self.now))['ok'])
        self.memory.call.side_effect = RuntimeError('db down')
        failed = await tools.run('remember', {'topic': '飲み物', 'fact': 'お茶が好き'}, self.memory, self.now)
        self.assertFalse(failed['ok'])
