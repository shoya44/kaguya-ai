import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import SecretStr

from app import tools
from app.errors import ChatError
from app.llm import Gemini


class FastReplyTests(unittest.TestCase):
    def test_weather_can_finish_without_second_llm(self):
        outcome = {
            'ok': True,
            '場所': '東京 / 東京都',
            '現在': {'天気': '晴れ', '気温C': 24.0, '体感C': 24.0, '降水mm': 0},
            '今日': {'天気': '晴れ', '最高気温C': 28, '最低気温C': 20, '降水確率%': 10},
            '明日': {'天気': '雨', '最高気温C': 25, '最低気温C': 19, '降水確率%': 70},
        }
        self.assertIn('明日は雨', tools.fast_reply('weather', outcome, '明日の天気は？'))
        self.assertIn('傘はたぶん大丈夫', tools.fast_reply('weather', outcome, '今日傘いる？'))

    def test_reminder_and_calendar_have_local_confirmations(self):
        reminder = tools.fast_reply('set_reminder', {
            'ok': True, '予約時刻': '2026-09-12 09:00', '内容': '薬飲んで',
        }, '')
        self.assertIn('声かけるね', reminder)
        calendar = tools.fast_reply('calendar', {
            'ok': True,
            'event': {'title': '歯医者', 'start': '2026-09-12T15:00:00+09:00'},
        }, '')
        self.assertIn('歯医者', calendar)

    def test_reference_and_project_results_still_need_llm(self):
        self.assertIsNone(tools.fast_reply('reference_search', {'matches': []}, ''))
        self.assertIsNone(tools.fast_reply('project_search', {'matches': []}, ''))

    def test_plain_clock_reminder_does_not_also_offer_calendar(self):
        names = {item['name'] for item in tools.declarations_for('明日9時に薬って教えて')}
        self.assertIn('set_reminder', names)
        self.assertNotIn('calendar', names)

    def test_voice_reminder_does_not_also_offer_settings(self):
        names = {item['name'] for item in tools.declarations_for('明日9時に薬って声かけて')}
        self.assertIn('set_reminder', names)
        self.assertNotIn('calendar', names)
        self.assertNotIn('app_settings', names)


class ForcedToolTests(unittest.IsolatedAsyncioTestCase):
    """頼まれた予約を、道具を呼ばずに「セットした」と答えてしまうのを止める。"""

    @staticmethod
    def llm():
        return Gemini(SimpleNamespace(gemini_api_key=SecretStr(''), gemini_model='test-model',
                                      llm_timeout_seconds=5, max_output_tokens=512))

    async def config_for(self, text):
        llm = self.llm()
        seen = {}

        async def request(contents, config):
            seen['config'] = config
            raise ChatError('api_error', 'stop here')

        llm._request = request
        with self.assertRaises(ChatError):
            await llm.reply([], text, {}, None, 512, memory=SimpleNamespace(), on_text=None)
        return seen['config']

    async def test_a_plain_reminder_request_must_call_the_tool(self):
        config = await self.config_for('15:30にリマインドして。仕事のやる気が出る声をかけて。')
        self.assertEqual(str(config.tool_config.function_calling_config.mode), 'FunctionCallingConfigMode.ANY')
        self.assertEqual(config.tool_config.function_calling_config.allowed_function_names, ['set_reminder'])
        # 道具を渡した回は、実行してから答えることも本文で伝える。
        self.assertIn('実行してから答える', config.system_instruction)

    async def test_a_question_about_the_past_is_left_to_the_model(self):
        config = await self.config_for('15時に何て言ってたっけ？')
        self.assertIsNone(config.tool_config)

    async def test_a_reminder_and_a_memory_in_one_sentence_can_both_run(self):
        config = await self.config_for('明日9時に薬って教えて。このこと覚えて')
        self.assertEqual(sorted(config.tool_config.function_calling_config.allowed_function_names),
                         ['remember', 'set_reminder'])

    async def test_writing_up_the_result_drops_both_the_tools_and_the_requirement(self):
        """呼び出し必須を残したまま道具を外すと、呼べる道具が無いのに呼べと言うことになる。"""
        llm = self.llm()
        seen = []

        def call(name, args):
            return SimpleNamespace(name=name, args=args, id=name)

        async def request(contents, config):
            seen.append((config.tools, config.tool_config))
            if len(seen) == 1:
                return SimpleNamespace(function_calls=[call('set_reminder', {'at': '2030-01-01T09:00', 'message': '薬'}),
                                                       call('remember', {'topic': '薬', 'fact': '毎朝9時'})],
                                       candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]))])
            return SimpleNamespace(text='予約したよ', candidates=[SimpleNamespace(
                content=SimpleNamespace(parts=[SimpleNamespace(text='予約したよ', thought=False)]),
                finish_reason='STOP')], function_calls=None)

        llm._request = request
        memory = SimpleNamespace(call=AsyncMock(return_value={'ok': True}))
        answer = await llm.reply([], '明日9時に薬って教えて。このこと覚えて', {}, None, 512,
                                 memory=memory, on_text=None)
        self.assertEqual(answer, '予約したよ')
        self.assertIsNotNone(seen[0][1])            # 1回目は呼び出しを必須にする
        self.assertEqual(seen[1], (None, None))     # 2回目は道具も必須指定も外す

    async def test_remembering_is_still_the_model_s_call(self):
        config = await self.config_for('これを覚えて：お茶が好き')
        self.assertIsNone(config.tool_config)


class ThinkingConfigTests(unittest.TestCase):
    @staticmethod
    def client_for(model):
        settings = SimpleNamespace(gemini_api_key=SecretStr(''), gemini_model=model)
        return Gemini(settings)

    @staticmethod
    def dump(config):
        if hasattr(config, 'model_dump'):
            return config.model_dump(mode='json', exclude_none=True)
        return vars(config)

    def test_gemini3_uses_low_or_compatible_small_budget(self):
        config = self.client_for('gemini-3.6-flash')._chat_thinking_config()
        self.assertIsNotNone(config)
        data = self.dump(config)
        level = str(data.get('thinking_level', '')).lower()
        self.assertTrue(level.endswith('low') or data.get('thinking_budget') == 1024)

    def test_gemini25_flash_disables_thinking(self):
        config = self.client_for('gemini-2.5-flash')._chat_thinking_config()
        self.assertEqual(self.dump(config).get('thinking_budget'), 0)


if __name__ == '__main__':
    unittest.main()
