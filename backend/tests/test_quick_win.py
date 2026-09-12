import contextlib
import tempfile
import unittest
from pathlib import Path

from app import tools
from app.personal_store import CalendarStore, ReferenceLibrary
from app.runtime import Options

import pgtemp


class ToolRouterTests(unittest.TestCase):
    def names(self, text):
        return {item['name'] for item in tools.declarations_for(text)}

    def test_plain_chat_sends_no_tool_schema(self):
        self.assertEqual(self.names('今日はなんとなく眠いな'), set())

    def test_explicit_weather_skips_function_schema_and_other_tools_still_route(self):
        # 明示的な天気質問はdirect_replyで処理するためGeminiへschemaを送らない。
        self.assertNotIn('weather', self.names('今日の天気どう？傘いる？'))
        self.assertIn('calendar', self.names('明日15時の予定を教えて'))
        self.assertIn('set_reminder', self.names('明日9時に薬って教えて'))
        self.assertIn('remember', self.names('コーヒーが好きって覚えておいて'))

    def test_reference_and_project_tools_are_separate(self):
        self.assertIn('reference_search', self.names('参照資料から勤怠ルールを探して'))
        project = self.names('自分の仕様をソースから確認して')
        self.assertIn('project_search', project)
        self.assertNotIn('reference_search', project)


class LocalStoreTests(unittest.TestCase):
    @unittest.skipUnless(pgtemp.available(), pgtemp.reason())
    def test_calendar_add_list_remove(self):
        with contextlib.closing(pgtemp.database()) as db:
            store = CalendarStore(db)
            item = store.add('歯医者', '2026-09-12T15:00:00+09:00')
            rows = store.list('2026-09-12T00:00:00+09:00', '2026-09-13T00:00:00+09:00')
            self.assertEqual([row['title'] for row in rows], ['歯医者'])
            result = store.remove(item['id'])
            self.assertTrue(result['removed'])
            self.assertEqual(store.list('2026-09-12T00:00:00+09:00', '2026-09-13T00:00:00+09:00'), [])

    def test_reference_search_and_list(self):
        with tempfile.TemporaryDirectory() as temp:
            library = ReferenceLibrary(Path(temp))
            (library.path / 'work.md').write_text('締め日は毎月15日です。\n次の行', encoding='utf-8')
            (library.path / 'skip.exe').write_text('15日', encoding='utf-8')
            listed = library.search('')
            self.assertEqual(listed['files'], ['work.md'])
            result = library.search('15日')
            self.assertEqual(result['matches'][0]['path'], 'work.md')

    def test_old_settings_gain_weather_default(self):
        options = Options.model_validate({'quiet': False, 'auto_jobs': True, 'proactive_minutes': 60,
                                          'daily_call_limit': 3, 'reply_tokens': 1024,
                                          'always_on_top': True, 'font_size': 14})
        self.assertEqual(options.weather_location, '東京')


if __name__ == '__main__':
    unittest.main()
