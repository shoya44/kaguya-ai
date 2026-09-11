import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app import main, memory_api


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
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
