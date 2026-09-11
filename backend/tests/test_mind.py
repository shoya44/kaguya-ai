import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.mind import KaguyaMind
from app.persona import memory_prompt


JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 12, 21, 0, tzinfo=JST)


class MindTests(unittest.TestCase):
    def test_off_is_noop_and_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            enabled = {'value': False}
            path = Path(tmp) / 'mind.db'
            mind = KaguyaMind(path, lambda: enabled['value'])
            self.assertEqual(mind.before_reply('こんにちは', NOW), {})
            self.assertEqual(mind.snapshot(NOW), {'enabled': False, 'status': 'off'})
            self.assertFalse(path.exists())

    def test_on_reacts_and_can_grow_self_preference_from_own_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            mind = KaguyaMind(Path(tmp) / 'mind.db', lambda: True)
            before = mind.before_reply('かぐや、ありがとう。プリンって好き？', NOW)
            self.assertIn('現在の気分', before)
            mind.after_reply('プリンって好き？', 'あたしはプリンが好きだよ。かなり好きかも。', NOW)
            after = mind.before_reply('プリン好きだったよね？', NOW + timedelta(minutes=1))
            self.assertTrue(any('プリン：好き' in item for item in after['自分の好み']))
            snap = mind.snapshot(NOW + timedelta(minutes=1))
            self.assertEqual(snap['status'], 'ok')
            self.assertEqual(snap['traits'][0]['name'], 'プリン')
            self.assertGreaterEqual(snap['stats']['edges'], 1)

    def test_repeated_short_phrase_becomes_candidate_but_is_not_executed_here(self):
        with tempfile.TemporaryDirectory() as tmp:
            mind = KaguyaMind(Path(tmp) / 'mind.db', lambda: True)
            for minute in range(3):
                mind.after_reply('朝のやつ', 'うん。', NOW + timedelta(minutes=minute))
            snap = mind.snapshot(NOW + timedelta(minutes=3))
            self.assertEqual(snap['shortcut_candidates'][0]['text'], '朝のやつ')
            self.assertEqual(snap['shortcut_candidates'][0]['count'], 3)

    def test_fail_open_returns_empty_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            mind = KaguyaMind(Path(tmp) / 'mind.db', lambda: True)

            def broken(_now):
                raise RuntimeError('broken experimental store')

            mind.store.emotions = broken
            self.assertEqual(mind.before_reply('こんにちは', NOW), {})
            self.assertEqual(mind.last_error, 'RuntimeError')

    def test_mind_guidance_is_only_added_when_context_exists(self):
        off_prompt = memory_prompt({})
        self.assertNotIn('Kaguya Mindの情報がある場合', off_prompt)
        on_prompt = memory_prompt({'mind': {'現在の気分': 'ご機嫌'}})
        self.assertIn('Kaguya Mindの情報がある場合', on_prompt)
        self.assertIn('ご機嫌', on_prompt)


if __name__ == '__main__':
    unittest.main()
