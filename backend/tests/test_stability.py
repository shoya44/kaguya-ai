import asyncio
import contextlib
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import tools, weather_tool
from app.jobs import Jobs
from app.runtime import RuntimeStore

import pgtemp


class DirectWeatherTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_weather_question_skips_llm_path(self):
        memory = SimpleNamespace(call=AsyncMock(return_value={'options': {'weather_location': '東京'}}))
        result = {
            'ok': True,
            '場所': '東京 / 東京都',
            '現在': {'天気': '晴れ', '気温C': 24.0, '体感C': 24.0, '降水mm': 0},
            '今日': {'天気': '晴れ', '最高気温C': 28, '最低気温C': 20, '降水確率%': 10},
            '明日': {'天気': '雨', '最高気温C': 25, '最低気温C': 19, '降水確率%': 70},
        }
        with patch('app.quick_tools.fetch_weather', AsyncMock(return_value=result)) as fetch:
            answer = await tools.direct_reply('今日の天気は？', memory)
        self.assertIn('24.0℃', answer)
        fetch.assert_awaited_once_with('東京')

    async def test_plain_cold_smalltalk_does_not_force_direct_weather(self):
        memory = SimpleNamespace(call=AsyncMock())
        self.assertIsNone(await tools.direct_reply('今日は寒いね', memory))
        memory.call.assert_not_awaited()

    async def test_recent_weather_cache_is_used_on_external_failure(self):
        cached = {
            'ok': True, '場所': '東京 / 東京都',
            '現在': {'天気': '晴れ', '気温C': 24.0}, '今日': {}, '明日': {},
        }
        key = '東京'.lower()
        weather_tool._WEATHER_CACHE[key] = (time.monotonic() - 1, cached)
        try:
            with patch('app.weather_tool._resolve', AsyncMock(side_effect=ValueError('temporary'))):
                result = await weather_tool.weather('東京')
            self.assertTrue(result['キャッシュ利用'])
            self.assertEqual(result['現在']['気温C'], 24.0)
        finally:
            weather_tool._WEATHER_CACHE.pop(key, None)


class JobPriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_can_cancel_running_memory_job(self):
        if not pgtemp.available():
            self.skipTest(pgtemp.reason())
        with contextlib.closing(pgtemp.database()) as db:
            store = RuntimeStore(db)
            started = asyncio.Event()

            async def memory_call(method, path, **kwargs):
                if path == '/organize/snapshot':
                    return {'raw': [{'status': 'completed'}], 'wisdom': []}
                return {'processed': 0}

            async def organize(_snapshot):
                started.set()
                await asyncio.sleep(30)

            controller = SimpleNamespace(active=None, unsaved=None, editing=False, broadcast=AsyncMock())
            memory = SimpleNamespace(call=AsyncMock(side_effect=memory_call))
            llm = SimpleNamespace(organize=AsyncMock(side_effect=organize), update_persona=AsyncMock())
            jobs = Jobs(memory, llm, store, controller)
            jobs.start(manual=True)
            await asyncio.wait_for(started.wait(), timeout=1)
            await jobs.pause_for_chat()
            self.assertFalse(jobs.running)
            self.assertIn('会話を優先', jobs.status)


if __name__ == '__main__':
    unittest.main()
