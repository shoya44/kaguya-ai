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


class GeminiResponseSchemaTests(unittest.TestCase):
    """Geminiへ渡すスキーマが、APIの受け付ける形に収まっているかを見る。

    ここを Pydantic の型から自動生成すると extra='forbid'（additionalProperties）や
    Field の制約が混ざり、400で毎回失敗する。実際それで整理が一度も通らず、
    原文が数百件たまっていた。ネットワークには出ず、送る形だけを確かめる。
    """

    def fields(self, schema):
        """スキーマ全体に出てくるキーを集める。"""
        found = set()
        stack = [schema]
        while stack:
            node = stack.pop()
            dumped = node.model_dump(exclude_none=True)
            found |= set(dumped)
            if node.properties:
                stack.extend(node.properties.values())
            if node.items:
                stack.append(node.items)
        return found

    def test_schema_avoids_fields_gemini_rejects(self):
        from app.llm import PERSONA_SCHEMA, WISDOM_SCHEMA
        # additional_properties は「そんなフィールドは無い」と400になる。
        # max_items は入れ子の配列に付けると同じく400になる。
        rejected = {'additional_properties', 'max_items', 'min_items',
                    'max_length', 'min_length', 'minimum', 'maximum', 'default'}
        for name, schema in (('wisdom', WISDOM_SCHEMA), ('persona', PERSONA_SCHEMA)):
            with self.subTest(schema=name):
                self.assertEqual(self.fields(schema) & rejected, set())

    def test_schema_matches_the_model_that_validates_the_reply(self):
        """送る形と、受け取ってから検証する型がずれていないこと。"""
        from app.llm import PERSONA_SCHEMA, WISDOM_SCHEMA
        from app.memory_store import PersonaCandidate, WisdomItem
        item = WISDOM_SCHEMA.properties['items'].items
        self.assertEqual(set(item.properties), set(WisdomItem.model_fields))
        self.assertEqual(set(PERSONA_SCHEMA.properties), set(PersonaCandidate.model_fields))
