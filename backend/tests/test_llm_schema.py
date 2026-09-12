import json
import unittest
from types import SimpleNamespace
from uuid import uuid4

import httpx
from google import genai
from google.genai import types
from pydantic import SecretStr

from app.llm import Gemini


class SchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_sdk_serializes_and_parses_wisdom_schema_without_network(self):
        raw_id = str(uuid4())
        candidate = {'items': [{'topic_key': '飲み物', 'summary': 'お茶が好き', 'kind': 'explicit',
                               'importance': 3, 'evidence_ids': [raw_id]}]}
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'candidates': [{'content': {'role': 'model',
                'parts': [{'text': json.dumps(candidate)}]}, 'finishReason': 'STOP'}]})

        llm = Gemini(SimpleNamespace(gemini_api_key=SecretStr(''), gemini_model='test-model', llm_timeout_seconds=5))
        llm.client = genai.Client(api_key='TEST_ONLY_NOT_A_KEY', http_options=types.HttpOptions(
            async_client_args={'transport': httpx.MockTransport(respond)}, retry_options=types.HttpRetryOptions(attempts=1)))
        try:
            result = await llm.organize({'raw': [{'id': raw_id, 'content': 'お茶が好き', 'status': 'completed'}], 'wisdom': []})
            # toneを省いた応答でも通り、中立（0）として保存される。
            expected = {'items': [dict(candidate['items'][0], tone=0)]}
            self.assertEqual(result, expected)
            self.assertEqual(len(requests), 1)
            self.assertIn('responseSchema', requests[0]['generationConfig'])
        finally:
            await llm.close()
