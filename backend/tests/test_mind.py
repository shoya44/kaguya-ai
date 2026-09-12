import unittest
from datetime import datetime, timedelta, timezone

from app.mind import KaguyaMind
from app.persona import memory_prompt

import pgtemp


JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 12, 21, 0, tzinfo=JST)


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class MindTests(unittest.TestCase):
    def setUp(self):
        self.db = pgtemp.database()
        self.addCleanup(self.db.close)
        self.minds: list[KaguyaMind] = []

    def mind(self, enabled=lambda: True) -> KaguyaMind:
        # 1つのDBを共有する。Mindは他のテーブルに触らないので混ざらない。
        mind = KaguyaMind(self.db, enabled)
        self.minds.append(mind)
        return mind

    def rows(self, table: str) -> int:
        with self.db.session() as conn:
            return conn.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n']

    def test_off_is_noop_and_writes_nothing(self):
        enabled = {'value': False}
        mind = self.mind(lambda: enabled['value'])
        self.assertEqual(mind.before_reply('こんにちは', NOW), {})
        self.assertEqual(mind.snapshot(NOW), {'enabled': False, 'status': 'off'})
        self.assertEqual(self.rows('living_emotion'), 0)
        self.assertEqual(self.rows('persona_favorite'), 0)

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

    def test_negated_quoted_and_hypothetical_preferences_are_not_learned(self):
        mind = self.mind()
        for answer in ('あたしはプリンが好きじゃないよ。',
                       'あたしはプリンが好きって言ったら変かな。',
                       '将弥が「あたしは犬が好きだよ」って言ってたね。'):
            mind.after_reply('ねえ', answer, NOW)
        self.assertEqual(mind.snapshot(NOW)['traits'], [])

    def test_user_wording_is_never_offered_as_kaguya_own(self):
        """ユーザーの口ぐせは記録しない。渡すとかぐやが相手の言い回しを真似る。"""
        mind = self.mind()
        for minute in range(3):
            mind.after_reply('朝のやつ', 'うん。', NOW + timedelta(minutes=minute))
        context = mind.before_reply('朝のやつ', NOW + timedelta(minutes=3))
        self.assertNotIn('よく使う言い方', context)
        self.assertNotIn('shortcut_candidates', mind.snapshot(NOW + timedelta(minutes=3)))

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
        # 会話側のデータがresetで消えないことを、同じDBの行で確かめる。
        with self.db.session() as conn:
            conn.execute("INSERT INTO app_settings(key,value) VALUES ('options','{}')")
        mind.after_reply('明日面接があるんだ', 'あたしはプリンが好きだよ。', NOW)
        self.assertTrue(self.rows('persona_favorite'))
        self.assertEqual(mind.snapshot(NOW)['traits'][0]['name'], 'プリン')

        mind.reset()
        self.assertEqual(self.rows('persona_favorite'), 0)
        self.assertEqual(self.rows('app_settings'), 1)
        fresh = mind.snapshot(NOW)
        self.assertEqual(fresh['status'], 'ok')
        self.assertEqual(fresh['traits'], [])
        self.assertEqual(fresh['stats'], {'traits': 0, 'interactions': 0, 'open_loops': 0})
        # 削除後も同じインスタンスで学習を続けられる。
        mind.after_reply('ねえ', 'あたしは犬が好きだよ。', NOW)
        self.assertEqual(mind.snapshot(NOW)['traits'][0]['name'], '犬')

    def test_reset_is_allowed_while_the_feature_is_off(self):
        enabled = {'value': True}
        mind = self.mind(lambda: enabled['value'])
        mind.after_reply('ねえ', 'あたしはプリンが好きだよ。', NOW)
        self.assertTrue(self.rows('persona_favorite'))
        enabled['value'] = False
        mind.reset()
        self.assertEqual(self.rows('persona_favorite'), 0)

    def test_mind_guidance_is_only_added_when_context_exists(self):
        off_prompt = memory_prompt({})
        self.assertNotIn('Kaguya Mindの情報がある場合', off_prompt)
        on_prompt = memory_prompt({'mind': {'現在の気分': 'ご機嫌'}})
        self.assertIn('Kaguya Mindの情報がある場合', on_prompt)
        self.assertIn('ご機嫌', on_prompt)
        self.assertIn('毎回蒸し返さない', on_prompt)


class RecalledEmotionTests(unittest.TestCase):
    """思い出したことで感情が動く。言葉づかいだけで決めない（記憶 → 感情）。"""

    @staticmethod
    def react(memories=(), concerns=()):
        from app import tuning
        from app.mind.engine import KaguyaMind as Engine
        return Engine._recalled(dict(tuning.EMOTION_BASELINE), list(memories), list(concerns))

    def test_nothing_recalled_leaves_the_feelings_alone(self):
        from app import tuning
        self.assertEqual(self.react(), dict(tuning.EMOTION_BASELINE))

    def test_touching_a_remembered_topic_raises_interest(self):
        from app import tuning
        after = self.react([{'topic_key': '猫', 'importance': 2}])
        self.assertGreater(after['curiosity'], tuning.EMOTION_BASELINE['curiosity'])

    def test_an_important_memory_also_moves_closeness_and_mood(self):
        from app import tuning
        ordinary = self.react([{'topic_key': '猫', 'importance': 2}])
        core = self.react([{'topic_key': '家族', 'importance': tuning.RECALL_IMPORTANT}])
        self.assertGreater(core['affection'], ordinary['affection'])
        self.assertGreater(core['happiness'], ordinary['happiness'])

    def test_a_pending_concern_makes_her_worry(self):
        from app import tuning
        after = self.react(concerns=[{'topic': '面接'}])
        self.assertGreater(after['concern'], tuning.EMOTION_BASELINE['concern'])

    def test_malformed_rows_are_ignored_instead_of_raising(self):
        from app import tuning
        self.assertEqual(self.react([None, 'not a row']), dict(tuning.EMOTION_BASELINE))
        self.assertEqual(self.react([{'importance': None}])['curiosity'],
                         tuning.EMOTION_BASELINE['curiosity'] + tuning.RECALL_REACTION['curiosity'])


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class RecalledMoodTests(unittest.TestCase):
    def setUp(self):
        self.db = pgtemp.database()
        self.addCleanup(self.db.close)

    def mind(self):
        return KaguyaMind(self.db, lambda: True)

    def talk(self, mind, turns, recalled=None):
        """同じ言葉で数ターン話す。1回の増減は小さく、続けると効いてくる。"""
        for minute in range(turns):
            context = mind.before_reply('うん', NOW + timedelta(minutes=minute), recalled)
        return context

    def test_the_same_words_feel_different_depending_on_what_is_recalled(self):
        """「うん」でも、覚えている話題が続いているかどうかで気分が変わる。"""
        plain = self.talk(self.mind(), 4)
        rich = self.talk(self.mind(), 4, {'wisdom': [{'topic_key': '猫', 'importance': 5}]})
        self.assertEqual(plain['現在の気分'], 'いつも通り')
        self.assertNotEqual(rich['現在の気分'], 'いつも通り')

    def test_a_path_without_recall_still_works(self):
        """天気の即答やファイルタブへの引き継ぎでは記憶を引かない。"""
        self.assertIn('現在の気分', self.mind().before_reply('明日の天気は？', NOW))
        self.assertIn('現在の気分', self.mind().before_reply('うん', NOW, None))
        self.assertIn('現在の気分', self.mind().before_reply('うん', NOW, {}))

    def test_feelings_never_leave_the_0_to_100_range(self):
        mind = self.mind()
        memories = {'wisdom': [{'topic_key': '家族', 'importance': 5}]}
        for minute in range(40):
            mind.before_reply('かぐや、ありがとう', NOW + timedelta(minutes=minute), memories)
        for name, value in mind.snapshot(NOW + timedelta(minutes=40))['emotions'].items():
            with self.subTest(name=name):
                self.assertGreaterEqual(value, 0)
                self.assertLessEqual(value, 100)


if __name__ == '__main__':
    unittest.main()


class DispositionTests(unittest.TestCase):
    """よくある状態が性格になる（状態 → 性格）。回数だけを見て決める。"""

    @staticmethod
    def pick(rows):
        from app.mind.engine import KaguyaMind as Engine
        return Engine._disposition(rows)

    def test_a_state_that_keeps_coming_back_becomes_a_trait(self):
        self.assertEqual(self.pick([{'name': 'boredom', 'samples': 100, 'high_count': 60}]),
                         ['退屈しやすく、かまってほしくなる'])

    def test_too_few_conversations_decide_nothing(self):
        """少ない回数で性格を決めると、たまたまの機嫌が固定されてしまう。"""
        self.assertEqual(self.pick([{'name': 'boredom', 'samples': 5, 'high_count': 5}]), [])

    def test_a_rare_state_is_not_a_trait(self):
        self.assertEqual(self.pick([{'name': 'jealousy', 'samples': 200, 'high_count': 4}]), [])

    def test_the_strongest_tendencies_come_first_and_are_capped(self):
        from app import tuning
        rows = [{'name': name, 'samples': 100, 'high_count': count} for name, count in
                (('boredom', 40), ('curiosity', 90), ('happiness', 70))]
        found = self.pick(rows)
        self.assertEqual(len(found), tuning.DISPOSITION_MAX)
        self.assertEqual(found[0], '知りたがりで、いろいろ聞きたくなる')

    def test_unknown_or_broken_rows_are_ignored(self):
        self.assertEqual(self.pick([{'name': 'unknown', 'samples': 100, 'high_count': 100},
                                    {'name': 'boredom', 'samples': 0, 'high_count': 0}]), [])


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class DispositionCounterTests(unittest.TestCase):
    def setUp(self):
        self.db = pgtemp.database()
        self.addCleanup(self.db.close)

    def test_every_turn_is_counted_and_strong_feelings_are_marked(self):
        mind = KaguyaMind(self.db, lambda: True)
        for minute in range(3):
            mind.before_reply('ChatGPTの方が賢いよね', NOW + timedelta(minutes=minute))
        rows = {row['name']: row for row in mind.store.counters()}
        self.assertEqual(rows['jealousy']['samples'], 3)
        # やきもちは1回で閾値を越える。数えられていること自体を確かめる。
        self.assertGreaterEqual(rows['jealousy']['high_count'], 1)
        self.assertEqual(rows['happiness']['high_count'], 0)

    def test_nothing_is_claimed_before_enough_conversations(self):
        mind = KaguyaMind(self.db, lambda: True)
        mind.before_reply('ChatGPTの方が賢いよね', NOW)
        self.assertEqual(mind.disposition(), '')
