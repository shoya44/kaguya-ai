import unittest
from datetime import datetime, timezone, timedelta

from app import relationship
from app.persona import memory_prompt


JST = timezone(timedelta(hours=9))


class FakeStore:
    def __init__(self):
        self.data = {'ledger': {}}

    def record(self, **changes):
        self.data['ledger'].update(changes)


class BrokenStore(FakeStore):
    def record(self, **changes):
        raise OSError('disk unavailable')


class RelationshipTests(unittest.TestCase):
    def test_style_feedback_only_learns_explicit_tone_feedback(self):
        self.assertIsNone(relationship.style_feedback('今日は仕事が大変だった'))
        self.assertIn('柔らか', relationship.style_feedback('その返しちょっと素っ気ない。もう少し柔らかくして'))
        self.assertIn('質問', relationship.style_feedback('毎回質問で終わる返しは減らしてほしい'))
        self.assertIn('繰り返さず', relationship.style_feedback('その言い方ちょっと苦手。やめてほしい'))

    def test_one_off_request_is_not_persisted(self):
        self.assertIsNone(relationship.style_feedback('今回だけ回答を短くして'))
        self.assertIsNone(relationship.style_feedback('この返事だけ詳しくして'))

    def test_success_count_and_active_days_grow_without_duplicate_day(self):
        store = FakeStore()
        relationship.record_success(store, datetime(2026, 9, 12, 9, 0, tzinfo=JST))
        relationship.record_success(store, datetime(2026, 9, 12, 21, 0, tzinfo=JST))
        relationship.record_success(store, datetime(2026, 9, 13, 8, 0, tzinfo=JST))
        self.assertEqual(store.data['ledger']['relationship_chats'], 3)
        self.assertEqual(len(store.data['ledger']['relationship_days']), 2)

    def test_feedback_is_available_to_same_turn_prompt(self):
        store = FakeStore()
        hint = relationship.capture_feedback(store, 'その話し方好き。この感じでお願い',
                                             datetime(2026, 9, 12, 9, 0, tzinfo=JST))
        self.assertIsNotNone(hint)
        relation = relationship.context(store)
        prompt = memory_prompt({'relationship': relation})
        self.assertIn('直近の話し方フィードバック', prompt)
        self.assertIn('親しみ', prompt)

    def test_familiarity_changes_locally_without_llm(self):
        store = FakeStore()
        store.data['ledger'] = {'relationship_chats': 120, 'relationship_days': ['2026-09-10', '2026-09-11']}
        relation = relationship.context(store)
        self.assertIn('気心', relation['慣れ'])
        self.assertEqual(relation['利用日数'], 2)

    def test_relationship_storage_failure_never_breaks_chat_path(self):
        store = BrokenStore()
        self.assertIsNotNone(relationship.capture_feedback(
            store, 'その返し好き', datetime(2026, 9, 12, 9, 0, tzinfo=JST)))
        relationship.record_success(store, datetime(2026, 9, 12, 9, 0, tzinfo=JST))
        self.assertIn('慣れ', relationship.context(store))


if __name__ == '__main__':
    unittest.main()
