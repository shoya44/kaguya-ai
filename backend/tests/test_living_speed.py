import unittest
from types import SimpleNamespace

from pydantic import SecretStr

from app import tools
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
