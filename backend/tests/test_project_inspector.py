import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import project_inspector, tools


class ProjectInspectorTests(unittest.IsolatedAsyncioTestCase):
    # 本番のROOTはresolve()済み。差し替えるROOTも同じにしておかないと、Windowsの
    # 8.3短縮名（C:\Users\RUNNER~1\...）のように解決後の名前が変わる環境で、
    # リポジトリ外と判定されて全件スキップになる。
    def test_search_returns_context_and_skips_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'src').mkdir()
            (root / 'src' / 'sample.py').write_text('one\ntwo\ndef target():\n    return 1\nfive\nsix\n', encoding='utf-8')
            (root / 'node_modules').mkdir()
            (root / 'node_modules' / 'hidden.py').write_text('target', encoding='utf-8')
            with patch.object(project_inspector, 'ROOT', root):
                result = project_inspector.project_search('target')
            self.assertTrue(result['ok'])
            self.assertEqual(len(result['matches']), 1)
            self.assertEqual(result['matches'][0]['path'], 'src/sample.py')
            self.assertIn('def target()', result['matches'][0]['context'])

    def test_read_limits_requested_range(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'notes.md').write_text('\n'.join(f'line {n}' for n in range(1, 30)), encoding='utf-8')
            with patch.object(project_inspector, 'ROOT', root):
                result = project_inspector.project_read('notes.md', 5, 8)
            self.assertTrue(result['ok'])
            self.assertEqual((result['start_line'], result['end_line']), (5, 8))
            self.assertIn('5: line 5', result['content'])
            self.assertNotIn('9: line 9', result['content'])

    def test_tools_are_registered_explicitly(self):
        names = {item['name'] for item in tools.DECLARATIONS}
        self.assertTrue({'project_status', 'project_search', 'project_read'} <= names)

    def test_everyday_ways_of_asking_about_herself_reach_the_source(self):
        """自分のことを尋ねる普通の言い方で、読みに行く道具が渡ること。

        道具が渡らないと、プロンプトの範囲だけで答える＝推測になる。
        """
        asked = [
            'モーション増えた？', 'フォルダの中どうなってる？', '最近できるようになった機能は？',
            'かぐやの仕様を教えて', 'READMEに何が書いてある？', '自分のことわかる？',
            'まばたきの実装は？', 'どうやって動いてるの？', 'どう作られてるの？',
            'ディレクトリ構成は？', '変更履歴を見せて', 'スプライトは何枚ある？',
        ]
        for text in asked:
            with self.subTest(text=text):
                names = {item['name'] for item in tools.declarations_for(text)}
                self.assertIn('project_search', names)

    def test_small_talk_still_streams(self):
        """雑談の回に関数定義を付けない。付くとその回はストリーミングできない。"""
        small_talk = [
            '今日はなんとなく眠いな', 'ありがとう、助かった', 'ちょっと疲れた', 'おやすみ',
            '明日の天気は？', 'おはよう', 'お腹すいた', 'それ面白いね',
        ]
        for text in small_talk:
            with self.subTest(text=text):
                names = {item['name'] for item in tools.declarations_for(text)}
                self.assertNotIn('project_search', names)

    def test_readme_lists_every_word_that_reaches_the_source(self):
        """READMEの一覧とコードの一覧を揃える。片方だけ直すと案内が嘘になる。"""
        readme = (Path(__file__).resolve().parents[2] / 'README.md').read_text(encoding='utf-8')
        section = readme.split('## かぐや自身のことを聞く')[1].split('## 音声会話')[0]
        for word in tools.PROJECT_WORDS + tools.PROJECT_WORDS_ASCII:
            with self.subTest(word=word):
                self.assertIn(word, section)

    async def test_tools_run_dispatches_project_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'README.md').write_text('Kaguya self inspection', encoding='utf-8')
            with patch.object(project_inspector, 'ROOT', root):
                result = await tools.run('project_read', {'path': 'README.md'}, None)
            self.assertTrue(result['ok'])
            self.assertIn('Kaguya self inspection', result['content'])


if __name__ == '__main__':
    unittest.main()
