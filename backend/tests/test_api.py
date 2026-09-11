import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app import main, memory_api
from app.runtime import RuntimeStore


class SettingsMemoryApiTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / '.test-output'
        root.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(temp.cleanup)
        runtime = RuntimeStore(Path(temp.name))
        llm = MagicMock(close=AsyncMock())
        client = MagicMock(aclose=AsyncMock())
        patches = [patch.object(main, 'RuntimeStore', return_value=runtime),
                   patch.object(main, 'Gemini', return_value=llm),
                   patch.object(main.httpx, 'AsyncClient', return_value=client),
                   patch.object(memory_api, 'recover_pending')]
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        session = self.client.post('/session', json={}).json()
        self.headers = {'Authorization': 'Bearer ' + session['session_token']}
        self.controller = main.app.state.controller
        self.controller.memory.call = AsyncMock(return_value={'items': [], 'next_offset': None})

    def test_settings_and_memory_require_session_and_internal_token_stays_private(self):
        self.assertEqual(self.client.get('/settings').status_code, 401)
        self.assertEqual(self.client.get('/memories/raw').status_code, 401)
        self.assertEqual(self.client.get('/internal/memory/organize/snapshot', headers=self.headers).status_code, 403)
        body = self.client.get('/settings', headers=self.headers).json()
        self.assertNotIn('database_url', body)
        self.assertNotIn('gemini_api_key', body)
        self.assertNotIn('internal_token', body)

    def test_options_validation_and_persistence(self):
        response = self.client.patch('/settings', headers=self.headers, json={'quiet': True, 'reply_tokens': 768})
        self.assertEqual(response.status_code, 200)
        reloaded = RuntimeStore(self.controller.runtime.path.parent)
        self.assertTrue(reloaded.options.quiet)
        self.assertEqual(reloaded.options.reply_tokens, 768)
        self.assertEqual(self.client.patch('/settings', headers=self.headers, json={'daily_call_limit': 4}).status_code, 422)
        self.assertEqual(self.client.patch('/settings', headers=self.headers, json={'gemini_api_key': 'not-a-key'}).status_code, 422)

    def test_memory_mutation_requires_confirmation_and_idle_state(self):
        route = '/memories/raw/' + str(uuid4())
        body = {'revision': 1, 'value': '訂正した内容', 'impact_token': 'a' * 64}
        self.assertEqual(self.client.patch(route, headers=self.headers, json=body).status_code, 422)
        self.controller.memory.call.assert_not_called()
        self.controller.active = {'turn_id': 'busy'}
        try:
            self.assertEqual(self.client.patch(route, headers=self.headers, json=body | {'confirmed': True}).status_code, 409)
        finally:
            self.controller.active = None
        self.assertEqual(self.client.patch(route, headers=self.headers, json=body | {'confirmed': True}).status_code, 200)
        self.assertFalse(self.controller.editing)

    def test_invalid_memory_key_never_reaches_internal_api(self):
        self.assertEqual(self.client.get('/memories/raw/not-a-uuid/impact', headers=self.headers).status_code, 422)
        self.assertEqual(self.client.get('/memories/persona/not-an-item/impact', headers=self.headers).status_code, 422)
        self.controller.memory.call.assert_not_called()

    def test_reminder_ack_requires_session_and_forwards_valid_id(self):
        reminder_id = str(uuid4())
        route = f'/reminders/{reminder_id}/ack'
        self.assertEqual(self.client.post(route).status_code, 401)
        self.controller.memory.call.assert_not_awaited()
        self.assertEqual(self.client.post(route, headers=self.headers).status_code, 200)
        self.controller.memory.call.assert_awaited_once_with('POST', route)
