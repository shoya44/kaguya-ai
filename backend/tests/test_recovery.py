import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app import main, memory_api

import pgtemp


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        if not pgtemp.available():
            self.skipTest(pgtemp.reason())
        # lifespanは設定をDBから読む。テスト用のクラスタへ向け直す。
        opened = []
        patched = patch.object(main, 'Database',
                               side_effect=lambda _dsn: opened.append(pgtemp.new()) or opened[-1])
        patched.start()
        self.addCleanup(patched.stop)
        # lifespanが閉じ損ねた接続を残さない。ResourceWarningで気付けるようにしておく。
        self.addCleanup(lambda: [db.close() for db in opened])
        self.addCleanup(pgtemp.database().close)

    async def test_stalled_socket_does_not_block_other_clients_and_is_closed(self):
        app = SimpleNamespace(state=SimpleNamespace())
        client = MagicMock(aclose=AsyncMock())
        llm = MagicMock(close=AsyncMock())
        slow = MagicMock(send_json=AsyncMock(side_effect=lambda _: None), close=AsyncMock())

        async def stall(_event):
            await asyncio.Event().wait()

        slow.send_json.side_effect = stall
        fast = MagicMock(send_json=AsyncMock(), close=AsyncMock())
        timeout = asyncio.timeout
        with (patch.object(main.httpx, 'AsyncClient', return_value=client),
              patch.object(main, 'Gemini', return_value=llm),
              patch.object(memory_api, 'recover_pending')):
            async with main.lifespan(app):
                app.state.connections.update((slow, fast))
                with patch.object(main.asyncio, 'timeout', side_effect=lambda seconds: timeout(min(seconds, .01))):
                    await app.state.controller.broadcast({'type': 'test'})
                fast.send_json.assert_awaited_once_with({'type': 'test'})
                slow.close.assert_awaited_once_with(code=1011)
                self.assertNotIn(slow, app.state.connections)
                self.assertIn(fast, app.state.connections)

    async def test_recovery_finishes_before_lifespan_is_ready(self):
        app = SimpleNamespace(state=SimpleNamespace())
        client = MagicMock(aclose=AsyncMock())
        llm = MagicMock(close=AsyncMock())
        with (patch.object(main.httpx, 'AsyncClient', return_value=client),
              patch.object(main, 'Gemini', return_value=llm),
              patch.object(memory_api, 'recover_pending') as recover):
            async with main.lifespan(app):
                recover.assert_called_once_with(main.settings)
                self.assertIsNone(app.state.controller.active)
            client.aclose.assert_awaited_once()

    async def test_failed_recovery_does_not_advertise_readiness(self):
        app = SimpleNamespace(state=SimpleNamespace())
        client = MagicMock(aclose=AsyncMock())
        llm = MagicMock(close=AsyncMock())
        with (patch.object(main.httpx, 'AsyncClient', return_value=client),
              patch.object(main, 'Gemini', return_value=llm),
              patch.object(memory_api, 'recover_pending', side_effect=RuntimeError('unavailable'))):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                async with main.lifespan(app):
                    self.fail('Requests must not be accepted before recovery')
            client.aclose.assert_awaited_once()

    async def test_public_history_forwards_cursor_and_limit_to_internal_api(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={'items': [], 'next_cursor': None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url='http://test') as client:
            memory = memory_api.MemoryClient(client)
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
                controller=SimpleNamespace(memory=memory))))
            await main.get_history(request, cursor='older', limit=50, _client_id=None)
        self.assertEqual(requests[0].url.params['limit'], '50')
        self.assertEqual(requests[0].url.params['cursor'], 'older')

    def test_recovery_only_updates_pending_user_messages(self):
        connection = MagicMock()
        with patch.object(memory_api, 'database_connection', return_value=connection):
            memory_api.recover_pending(SimpleNamespace())
        sql = connection.__enter__.return_value.execute.call_args.args[0]
        self.assertIn("SET status='failed'", sql)
        self.assertIn("role='user' AND status='pending'", sql)


if __name__ == '__main__':
    unittest.main()
