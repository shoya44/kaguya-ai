import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from app import relationship
from app.persona import memory_prompt


JST = timezone(timedelta(hours=9))


def living(chats=0, days=()):
    """会った量だけを返す living_activity の代わり。"""
    return SimpleNamespace(familiarity=lambda: {'chats': chats, 'days': len(set(days))})


class RelationshipTests(unittest.TestCase):
    def test_style_feedback_only_learns_explicit_tone_feedback(self):
        self.assertIsNone(relationship.style_feedback('今日は仕事が大変だった'))
        self.assertIn('柔らか', relationship.style_feedback('その返しちょっと素っ気ない。もう少し柔らかくして'))
        self.assertIn('質問', relationship.style_feedback('毎回質問で終わる返しは減らしてほしい'))
        self.assertIn('繰り返さず', relationship.style_feedback('その言い方ちょっと苦手。やめてほしい'))

    def test_one_off_request_is_not_persisted(self):
        self.assertIsNone(relationship.style_feedback('今回だけ回答を短くして'))
        self.assertIsNone(relationship.style_feedback('この返事だけ詳しくして'))

    def test_familiarity_grows_with_the_number_of_conversations(self):
        self.assertIn('知り合ったばかり', relationship.familiarity(1))
        self.assertIn('少し慣れて', relationship.familiarity(10))
        self.assertIn('かなり慣れて', relationship.familiarity(50))
        self.assertIn('気心', relationship.familiarity(120))

    def test_context_carries_how_much_we_have_met(self):
        relation = relationship.context(living(120, ['2026-09-10', '2026-09-11', '2026-09-10']))
        self.assertIn('気心', relation['慣れ'])
        self.assertEqual(relation['利用日数'], 2)

    def test_style_feedback_reaches_the_prompt_through_persona(self):
        """話し方の希望は persona_character の1行なので、「接し方」として渡る。"""
        hint = relationship.style_feedback('その話し方好き。この感じでお願い')
        self.assertIsNotNone(hint)
        prompt = memory_prompt({'persona': [{'key': relationship.STYLE_KEY, 'value': hint}],
                                'relationship': relationship.context(living(3))})
        self.assertIn(relationship.STYLE_KEY, prompt)
        self.assertIn('親しみ', prompt)

    def test_the_prompt_no_longer_carries_the_hint_twice(self):
        relation = relationship.context(living(3))
        self.assertNotIn('直近の話し方フィードバック', relation)


if __name__ == '__main__':
    unittest.main()
