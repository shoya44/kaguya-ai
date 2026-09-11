import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.mind import KaguyaMind
from app.persona import memory_prompt


JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 12, 21, 0, tzinfo=JST)


class MindTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.minds: list[KaguyaMind] = []

    def tearDown(self):
        # Mindは接続を開いたまま保持する。Windowsは使用中のファイルを削除できないため、
        # 一時ディレクトリを片付ける前に必ず閉じる（tearDownはaddCleanupより先に走る）。
        for mind in self.minds:
            mind.close()

    def mind(self, enabled=lambda: True) -> KaguyaMind:
        mind = KaguyaMind(self.directory / f'mind{len(self.minds) + 1}.db', enabled)
        self.minds.append(mind)
        return mind

    def test_off_is_noop_and_does_not_create_database(self):
        enabled = {'value': False}
        mind = self.mind(lambda: enabled['value'])
        path = mind.path
        self.assertEqual(mind.before_reply('こんにちは', NOW), {})
        self.assertEqual(mind.snapshot(NOW), {'enabled': False, 'status': 'off'})
        self.assertFalse(path.exists())

    def test_on_reacts_and_can_grow_self_preference_from_own_reply(self):
        mind = self.mind()
        before = mind.before_reply('かぐや、ありがとう。プリンって好き？', NOW)
        self.assertIn('現在の気分', before)
        mind.after_reply('プリンって好き？', 'あたしはプリンが好きだよ。かなり好きかも。', NOW)
        after = mind.before_reply('プリン好きだったよね？', NOW + timedelta(minutes=1))
        self.assertTrue(any('プリン：好き' in item for item in after['自分の好み']))
        snap = mind.snapshot(NOW + timedelta(minutes=1))
        self.assertEqual(snap['status'], 'ok')
        self.assertEqual(snap['traits'][0]['name'], 'プリン')
        self.assertGreaterEqual(snap['stats']['edges'], 1)

    def test_negated_quoted_and_hypothetical_preferences_are_not_learned(self):
        mind = self.mind()
        for answer in ('あたしはプリンが好きじゃないよ。',
                       'あたしはプリンが好きって言ったら変かな。',
                       '将弥が「あたしは犬が好きだよ」って言ってたね。'):
            mind.after_reply('ねえ', answer, NOW)
        self.assertEqual(mind.snapshot(NOW)['traits'], [])

    def test_user_phrases_are_not_offered_as_kaguya_own_wording(self):
        mind = self.mind()
        for minute in range(3):
            mind.after_reply('朝のやつ', 'うん。', NOW + timedelta(minutes=minute))
        context = mind.before_reply('朝のやつ', NOW + timedelta(minutes=3))
        self.assertNotIn('よく使う言い方', context)
        self.assertEqual(mind.snapshot(NOW + timedelta(minutes=3))['shortcut_candidates'][0]['text'], '朝のやつ')

    def test_open_loop_is_raised_only_after_the_plan_is_over(self):
        mind = self.mind()
        mind.after_reply('明日面接があるんだ', 'そっか、頑張ってね。', NOW)
        same_day = mind.before_reply('ねえ', NOW + timedelta(hours=2))
        self.assertNotIn('気にかけていること', same_day)
        later = mind.before_reply('ただいま', NOW + timedelta(days=1, hours=1))
        self.assertTrue(any('面接' in item for item in later['気にかけていること']))

    def test_open_loop_closes_when_the_user_returns_to_it(self):
        mind = self.mind()
        mind.after_reply('明日面接があるんだ', 'うん。', NOW)
        mind.after_reply('面接、受かったよ', 'よかったね。', NOW + timedelta(days=1, hours=2))
        context = mind.before_reply('ねえ', NOW + timedelta(days=1, hours=3))
        self.assertNotIn('気にかけていること', context)
        self.assertEqual(mind.snapshot(NOW + timedelta(days=1, hours=3))['stats']['open_loops'], 0)

    def test_open_loop_is_not_repeated_forever(self):
        mind = self.mind()
        mind.after_reply('明日面接があるんだ', 'うん。', NOW)
        raised = 0
        for day in range(1, 6):
            moment = NOW + timedelta(days=day, hours=1)
            if mind.before_reply('ただいま', moment).get('気にかけていること'):
                raised += 1
                mind.after_reply('ただいま', '面接どうだった？', moment)
        # 返事がなくても2回までで諦める。何日も同じことを聞き続けない。
        self.assertEqual(raised, 2)
        self.assertNotIn('気にかけていること', mind.before_reply('ねえ', NOW + timedelta(days=9)))

    def test_a_topic_taken_for_a_nudge_is_not_also_raised_in_the_conversation(self):
        mind = self.mind()
        mind.after_reply('明日面接があるんだ', 'うん。', NOW)
        later = NOW + timedelta(days=1, hours=1)
        self.assertEqual(mind.due_topic(later), '面接')
        # 声かけで聞いた直後に、会話側でも同じ話題を持ち出さない。
        self.assertNotIn('気にかけていること', mind.before_reply('ただいま', later))
        self.assertEqual(mind.due_topic(later), '')

    def test_a_nudge_topic_also_stops_after_two_tries(self):
        mind = self.mind()
        mind.after_reply('明日面接があるんだ', 'うん。', NOW)
        taken = [mind.due_topic(NOW + timedelta(days=day, hours=1)) for day in range(1, 6)]
        self.assertEqual([value for value in taken if value], ['面接', '面接'])

    def test_no_topic_when_nothing_is_pending(self):
        self.assertEqual(self.mind().due_topic(NOW), '')
        self.assertEqual(self.mind(lambda: False).due_topic(NOW), '')

    def test_a_preference_that_never_settled_is_forgotten(self):
        mind = self.mind()
        mind.after_reply('ねえ', 'あたしはプリンが好きだよ。', NOW)
        self.assertEqual(mind.snapshot(NOW)['traits'][0]['name'], 'プリン')
        # 一度言っただけの好みは覚えていない。何か話した時点で片付く。
        mind.after_reply('ねえ', 'うん。', NOW + timedelta(days=31))
        self.assertEqual(mind.snapshot(NOW + timedelta(days=31))['traits'], [])

    def test_a_settled_preference_is_kept(self):
        mind = self.mind()
        for day in range(3):
            mind.after_reply('ねえ', 'あたしはプリンが好きだよ。', NOW + timedelta(days=day))
        mind.after_reply('ねえ', 'うん。', NOW + timedelta(days=60))
        traits = mind.snapshot(NOW + timedelta(days=60))['traits']
        self.assertEqual([row['name'] for row in traits], ['プリン'])

    def test_casual_sentences_do_not_become_open_loops(self):
        mind = self.mind()
        for text in ('今日は疲れたな', 'おはよう', '明日も普通に過ごすよ', 'ありがとう'):
            mind.after_reply(text, 'うん。', NOW)
        self.assertEqual(mind.snapshot(NOW)['stats']['open_loops'], 0)

    def test_repeated_short_phrase_becomes_candidate_but_is_not_executed_here(self):
        mind = self.mind()
        for minute in range(3):
            mind.after_reply('朝のやつ', 'うん。', NOW + timedelta(minutes=minute))
        snap = mind.snapshot(NOW + timedelta(minutes=3))
        self.assertEqual(snap['shortcut_candidates'][0]['text'], '朝のやつ')
        self.assertEqual(snap['shortcut_candidates'][0]['count'], 3)

    def test_fail_open_returns_empty_context_and_is_logged(self):
        mind = self.mind()

        def broken(_now):
            raise RuntimeError('broken experimental store')

        mind.store.emotions = broken
        with self.assertLogs('uvicorn.error', level='WARNING') as logs:
            self.assertEqual(mind.before_reply('こんにちは', NOW), {})
        self.assertEqual(mind.last_error, 'RuntimeError')
        self.assertIn('Kaguya Mind failed', logs.output[0])

    def test_reset_clears_growth_without_touching_anything_else(self):
        mind = self.mind()
        path = mind.path
        mind.after_reply('明日面接があるんだ', 'あたしはプリンが好きだよ。', NOW)
        self.assertTrue(path.exists())
        self.assertEqual(mind.snapshot(NOW)['traits'][0]['name'], 'プリン')

        mind.reset()
        self.assertFalse(path.exists())
        fresh = mind.snapshot(NOW)
        self.assertEqual(fresh['status'], 'ok')
        self.assertEqual(fresh['traits'], [])
        self.assertEqual(fresh['stats'], {'traits': 0, 'edges': 0, 'interactions': 0, 'open_loops': 0})
        # 削除後も同じインスタンスで学習を続けられる。
        mind.after_reply('ねえ', 'あたしは犬が好きだよ。', NOW)
        self.assertEqual(mind.snapshot(NOW)['traits'][0]['name'], '犬')

    def test_reset_is_allowed_while_the_feature_is_off(self):
        enabled = {'value': True}
        mind = self.mind(lambda: enabled['value'])
        path = mind.path
        mind.after_reply('ねえ', 'あたしはプリンが好きだよ。', NOW)
        enabled['value'] = False
        mind.reset()
        self.assertFalse(path.exists())

    def test_mind_guidance_is_only_added_when_context_exists(self):
        off_prompt = memory_prompt({})
        self.assertNotIn('Kaguya Mindの情報がある場合', off_prompt)
        on_prompt = memory_prompt({'mind': {'現在の気分': 'ご機嫌'}})
        self.assertIn('Kaguya Mindの情報がある場合', on_prompt)
        self.assertIn('ご機嫌', on_prompt)
        self.assertIn('毎回蒸し返さない', on_prompt)


if __name__ == '__main__':
    unittest.main()
