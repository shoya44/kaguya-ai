import unittest

from app import tools


class SoftToolReplyTests(unittest.TestCase):
    def test_calendar_empty_reply_is_warm_but_local(self):
        text = tools.fast_reply('calendar', {'ok': True, 'items': []}, '今日の予定は？')
        self.assertIn('予定表', text)
        self.assertIn('空っぽ', text)

    def test_reminder_confirmation_has_character_tone(self):
        text = tools.fast_reply('set_reminder', {
            'ok': True, '予約時刻': '2026-09-12 09:00', '内容': '薬飲んで',
        }, '')
        self.assertIn('おっけー', text)
        self.assertIn('任せて', text)

    def test_weather_still_returns_facts_without_llm(self):
        outcome = {
            'ok': True,
            '場所': '東京 / 東京都',
            '現在': {'天気': '晴れ', '気温C': 24.0},
            '今日': {'降水確率%': 10},
            '明日': {'天気': '雨', '最高気温C': 25, '最低気温C': 19, '降水確率%': 70},
        }
        text = tools.fast_reply('weather', outcome, '明日の天気は？')
        self.assertIn('明日は雨', text)
        self.assertIn('傘', text)

    def test_unknown_rich_results_still_go_to_llm(self):
        self.assertIsNone(tools.fast_reply('reference_search', {'ok': True, 'matches': []}, ''))


if __name__ == '__main__':
    unittest.main()
