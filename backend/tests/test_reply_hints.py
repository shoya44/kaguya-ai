"""会話品質まわり（気がかり・活動の一致・導入句・応答方針・振り返り）の確認。"""
import unittest
from datetime import datetime, timedelta, timezone

from app import memory_api, memory_store
from app.reply_hints import concern_status


NOW = datetime(2026, 9, 13, 20, 0, tzinfo=timezone(timedelta(hours=9)))


class ConcernStatusTests(unittest.TestCase):
    def test_mention_alone_is_not_resolved_and_still_wins(self):
        for said, expected in [
            ('面接、無事に終わったよ', 'resolved'),
            ('面接受かった！', 'resolved'),
            ('面接の話なんだけどさ', ''),
            ('面接まだ不安なんだよね', 'still'),
            # 「まだ終わってない」は終わった側の語を含むが、未解決として扱う。
            ('面接まだ終わってない', 'still'),
            ('今日は暑いね', ''),
        ]:
            with self.subTest(said=said):
                self.assertEqual(concern_status('面接', said), expected)


class ConcernRecallTests(unittest.TestCase):
    def setUp(self):
        import pgtemp
        if not pgtemp.available():
            self.skipTest(pgtemp.reason())
        self.db = pgtemp.database()
        self.addCleanup(self.db.close)

    def _open(self, conn, topic, **columns):
        conn.execute("""INSERT INTO memory_concern(topic,kind,quote,opened_at,due_at)
            VALUES (%s,'plan','予定',now()-interval '2 days',now()-interval '1 day')""", (topic,))
        for column, value in columns.items():
            conn.execute(f'UPDATE memory_concern SET {column}=%s WHERE topic=%s', (value, topic))

    def test_recall_skips_recently_asked_and_exhausted_topics(self):
        with self.db.session() as conn:
            conn.execute('TRUNCATE memory_long,memory_concern,persona_character')
            self._open(conn, '面接')
            self._open(conn, '歯医者', last_asked_at=datetime.now(timezone.utc) - timedelta(hours=1))
            self._open(conn, '引越し', asked=2)
            topics = [row['topic'] for row in memory_store.recall(conn, 'こんばんは')['pending_topic']]
            self.assertEqual(topics, ['面接'])

    def test_settle_marks_asked_reopens_still_and_closes_resolved(self):
        with self.db.session() as conn:
            conn.execute('TRUNCATE memory_concern')
            self._open(conn, '面接', asked=1)
            self._open(conn, '歯医者', asked=1)
            self._open(conn, '引越し', asked=1)
            memory_api._settle_concerns(
                conn, ['面接', '歯医者', '引越し'],
                '面接はまだ不安。歯医者は終わったよ。',
                'そっか。引越しの準備はどう？')
            rows = {row['topic']: row for row in
                    conn.execute('SELECT topic,asked,last_asked_at,resolved_at FROM memory_concern').fetchall()}
            # まだ不安 → 諦めない（askedを戻す）。解決 → もう触れない。触れただけ → 1回消費。
            self.assertEqual((rows['面接']['asked'], rows['面接']['resolved_at']), (0, None))
            self.assertIsNotNone(rows['歯医者']['resolved_at'])
            self.assertEqual((rows['引越し']['asked'], rows['引越し']['resolved_at']), (2, None))
            self.assertTrue(all(row['last_asked_at'] for row in rows.values()))
            # 直後は同じ話題を蒸し返さない。
            self.assertEqual(memory_store.recall(conn, '面接どうだった')['pending_topic'], [])


class ScreenConsistencyTests(unittest.TestCase):
    """画面の姿（活動・元気さ・表情）と、返答の口調・語りを一致させる。"""

    def test_activity_adds_a_no_contradiction_rule(self):
        from app.living_prompt import living_context
        result = living_context({'activity': 'reading', 'energy': 70}, 'happy', now=NOW)
        self.assertIn('本を読んでいた', result['直前の活動'])
        self.assertIn('自分から作らない', result['画面との一致'])

    def test_low_energy_overrides_the_mood_tone(self):
        from app.living_prompt import living_context
        tired = living_context({'activity': 'idle', 'energy': 20}, 'happy', now=NOW)
        lively = living_context({'activity': 'idle', 'energy': 70}, 'happy', now=NOW)
        self.assertIn('眠そう', tired['今の口調'])
        self.assertIn('弾んだ', lively['今の口調'])
        # 活動も元気さも分からないときは、従来どおり何も足さない。
        self.assertEqual(living_context({}, '', now=NOW), {})


class IntroRepeatTests(unittest.TestCase):
    """毎ターン「今〜してた」で切り出さない。間が空いた初回では出す。"""

    def _living(self, minutes_ago):
        return {'activity': 'reading', 'energy': 70,
                'last_seen_at': NOW - timedelta(minutes=minutes_ago)}

    def test_intro_is_dropped_while_the_conversation_continues(self):
        from app.living_prompt import living_context
        from app.reply_hints import allow_activity_intro
        history = [{'text': 'ただいま', 'answer': '今ちょっと本読んでた。おかえり。'}]
        self.assertFalse(allow_activity_intro('うん', history))
        dropped = living_context(self._living(1), '', NOW, intro=False)
        self.assertNotIn('直前の活動', dropped)
        self.assertIn('今回は', dropped['切り出し'])

    def test_intro_returns_after_a_break_or_when_asked(self):
        from app.living_prompt import living_context
        from app.reply_hints import allow_activity_intro
        history = [{'text': 'ただいま', 'answer': '今ちょっと本読んでた。'}]
        # 会話が途切れたあとの初回は、直近で使っていても切り出してよい。
        self.assertIn('直前の活動', living_context(self._living(31), '', NOW, intro=False))
        # 「何してた？」と聞かれたら答える。
        self.assertTrue(allow_activity_intro('何してた？', history))
        self.assertTrue(allow_activity_intro('うん', [{'text': 'ねえ', 'answer': 'どうしたの？'}]))

    def test_five_turns_do_not_repeat_the_opener(self):
        from app.persona import memory_prompt
        history, used = [], []
        for turn in range(5):
            recalled = {'living': self._living(1)}
            prompt = memory_prompt(recalled, now=NOW, text='うん', history=history)
            used.append('直前の活動' in prompt)
            # 導入句が許されたターンだけ、かぐやが実際に使ったとみなす。
            history.append({'text': 'うん', 'answer': '今ちょっと本読んでた。' if used[-1] else 'そうなんだ。'})
        # 直近3ターンに使っていれば省く。5ターン続けても毎回は出ない。
        self.assertEqual(used, [True, False, False, False, True])


class CallbackTests(unittest.TestCase):
    """久しぶりの話題に短く触れる。連続はしない。"""

    def _wisdom(self, topic, days, importance=4):
        return {'id': topic, 'topic_key': topic, 'summary': f'{topic}の話', 'kind': 'explicit',
                'importance': importance, 'last_seen_at': (NOW - timedelta(days=days)).isoformat()}

    def test_picks_the_oldest_important_topic_only(self):
        from app.reply_hints import callback
        recalled = {'wisdom': [self._wisdom('登山', 10), self._wisdom('猫', 40),
                               self._wisdom('天気', 40, importance=1), self._wisdom('仕事', 1)]}
        result = callback(recalled, 'ひさしぶり', [], NOW)
        self.assertEqual(result['話題'], '猫')
        self.assertIn('短く一度だけ', result['触れ方'])

    def test_skips_topics_already_in_play_and_does_not_repeat(self):
        from app.reply_hints import callback
        recalled = {'wisdom': [self._wisdom('猫', 40)]}
        # いま話している話題は振り返らない。
        self.assertEqual(callback(recalled, '猫がね', [], NOW), {})
        # 直近の返答で振り返ったばかりなら、続けて振り返らない。
        history = [{'text': 'ねえ', 'answer': 'そういえば猫の話、どうなった？'}]
        self.assertEqual(callback(recalled, 'ひさしぶり', history, NOW), {})


class IntentTests(unittest.TestCase):
    """共感・相談・雑談で、返答の構成を切り替える。"""

    def test_three_intents_change_the_plan(self):
        from app.persona import memory_prompt
        from app.reply_hints import RESPONSE_PLAN, intent
        for said, expected in [
            ('疲れた', 'empathy'), ('しんどい、聞いてほしい', 'empathy'),
            ('どうしたらいい？', 'consult'), ('疲れたけど、どうすればいいかな', 'consult'),
            ('今日暑いね', 'chat'), ('', 'chat'),
        ]:
            with self.subTest(said=said):
                self.assertEqual(intent(said), expected)
        # 「疲れた」には助言を出さず、「どうしたらいい？」には具体案を求める。
        self.assertIn('助言', RESPONSE_PLAN['empathy'])
        self.assertIn('具体案', RESPONSE_PLAN['consult'])
        self.assertIn(RESPONSE_PLAN['empathy'], memory_prompt(now=NOW, text='疲れた'))
        self.assertIn(RESPONSE_PLAN['chat'], memory_prompt(now=NOW, text='今日暑いね'))
