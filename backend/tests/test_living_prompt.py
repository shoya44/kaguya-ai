import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app import memory_api, memory_store
from app.db import Database
from app.living_prompt import ACTIVITY_LABELS, derive_mood, living_context, time_hint
from app.models import Completion
from app.persona import memory_prompt


NOW = datetime(2026, 9, 13, 8, 30, tzinfo=timezone(timedelta(hours=9)))


class PromptTests(unittest.TestCase):
    def test_time_and_weekday(self):
        prompt = memory_prompt(now=NOW)
        self.assertIn('2026-09-13（日）08:30 JST', prompt)
        self.assertIn('眠そう', prompt)
        self.assertIn('深夜', time_hint(NOW.replace(hour=0)))
        self.assertIn('昼', time_hint(NOW.replace(hour=12)))
        self.assertIn('夜', time_hint(NOW.replace(hour=22)))

    def test_mood_thresholds_and_precedence(self):
        for emotions, energy, expected in [
            ({'happiness': 90, 'concern': 90}, 29, 'sleepy'),
            ({'concern': 61, 'happiness': 90}, 30, 'worried'),
            ({'happiness': 61}, 30, 'happy'),
            ({'happiness': 60, 'concern': 60}, 30, 'calm'),
            ({'jealousy': 61}, 60, 'sulky'),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(derive_mood(emotions, {'energy': energy}), expected)
        self.assertEqual(derive_mood({}, {}), '')

    def test_reunion_boundaries(self):
        for hours, expected in [(23.99, None), (24, '24時間'), (71.99, '24時間'),
                                (72, '3日'), (167.99, '3日'), (168, '1週間')]:
            with self.subTest(hours=hours):
                result = living_context({'last_seen_at': NOW - timedelta(hours=hours)}, now=NOW)
                if expected:
                    self.assertIn(expected, result['再会'])
                else:
                    self.assertNotIn('再会', result)

    def test_first_turn_uses_jst_and_skips_missing_and_future(self):
        for last in [None, NOW, NOW + timedelta(days=1), NOW.astimezone(timezone.utc).isoformat()]:
            self.assertNotIn('今日の初回', living_context({'last_seen_at': last}, now=NOW))
        prior = {'last_seen_at': NOW - timedelta(days=1)}
        self.assertIn('おはよう', living_context(prior, now=NOW)['今日の初回'])
        self.assertNotIn('おはよう', living_context(prior, now=NOW.replace(hour=22))['今日の初回'])

    def test_each_activity_and_empty_blocks(self):
        for activity, label in ACTIVITY_LABELS.items():
            self.assertIn(label, living_context({'activity': activity}, now=NOW)['直前の活動'])
        values = json.loads(memory_prompt(now=NOW).splitlines()[-1])
        self.assertEqual(set(values), {'現在日時', '時間帯の口調'})

    def test_memory_and_persona_are_independent_of_mood(self):
        recalled = {'pending_topic': [{'topic': '面接', 'kind': 'plan', 'quote': '明日面接がある'}],
                    'favorites': [{'name': '猫', 'valence': 0.9}], 'mood': 'calm'}
        values = json.loads(memory_prompt(recalled, now=NOW).splitlines()[-1])
        self.assertEqual(values['Living：かぐやの今']['mood'], 'calm')
        self.assertEqual(values['Persona：かぐやの好み'][0]['好み'], '好き')
        self.assertEqual(values['Memory：気にかけている話題'][0]['話題'], '面接')


class RecallTests(unittest.TestCase):
    def test_read_only_query_budget_and_tone(self):
        for mood, expected in [('worried', -1), ('sulky', -1), ('happy', 1), ('calm', 0)]:
            conn = MagicMock()
            rows = [dict(id=uuid4(), topic_key='仕事', summary='仕事の話', locked=False,
                         kind='explicit', tone=tone, importance=3, updated_at=NOW)
                    for tone in [0, -1, 1]]
            conn.execute.side_effect = [
                MagicMock(fetchone=lambda: {'persona': [], 'living': None, 'emotions': None}),
                MagicMock(fetchall=lambda: rows), MagicMock(fetchall=lambda: []),
                MagicMock(fetchall=lambda: []),
            ]
            result = memory_store.recall(conn, '仕事', mood=mood)
            self.assertEqual(result['wisdom'][0]['tone'], expected)
            self.assertEqual(len(result['wisdom']), 3)
            self.assertEqual(conn.execute.call_count, 4)  # 既存2本 + 追加2本
            self.assertTrue(all(call.args[0].lstrip().startswith('SELECT')
                                for call in conn.execute.call_args_list))

    def test_completion_updates_only_selected_ids_once(self):
        selected = [uuid4(), uuid4()]
        for existing in [None, {'content': '返事'}]:
            conn = MagicMock()
            conn.__enter__.return_value = conn
            conn.execute.side_effect = [
                MagicMock(fetchone=lambda: {'status': 'pending', 'origin_client_id': uuid4(), 'input_mode': 'text'}),
                MagicMock(fetchone=lambda: existing), MagicMock(), MagicMock(), MagicMock(),
            ]
            with patch.object(memory_api, 'connection', return_value=conn):
                memory_api.complete(uuid4(), Completion(answer='返事', recalled_ids=selected), SimpleNamespace())
            writes = [call for call in conn.execute.call_args_list if 'UPDATE memory_long' in call.args[0]]
            self.assertEqual(len(writes), 0 if existing else 1)
            if writes:
                self.assertEqual(writes[0].args[1], (selected,))

    def test_session_handles_only_exceptions(self):
        for error, rollback in [(ValueError(), True), (KeyboardInterrupt(), False), (SystemExit(), False)]:
            db = Database('')
            conn = MagicMock()
            with patch.object(db, '_connect', return_value=conn):
                with self.assertRaises(type(error)):
                    with db.session():
                        raise error
            self.assertEqual(conn.rollback.called, rollback)
            conn.commit.assert_not_called()


class SaveRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_ids_survive_save_failure_and_retry(self):
        from test_living import RecallOrderTests
        from app.errors import ChatError
        from unittest.mock import AsyncMock

        helper = RecallOrderTests()
        controller, _, _ = helper.controller()
        controller.memory.complete.side_effect = [ChatError('save_failed', '保存失敗'), None]
        await helper.talk(controller)
        self.assertIsNotNone(controller.unsaved)
        selected = controller.recalled_ids.copy()
        await controller.retry_save('1', AsyncMock())
        self.assertIsNone(controller.unsaved)
        self.assertEqual(controller.memory.complete.await_count, 2)
        for call in controller.memory.complete.await_args_list:
            self.assertEqual(call.kwargs['recalled_ids'], selected)
        controller.llm.reply.assert_awaited_once()

    async def test_living_is_read_before_seen_updates_it(self):
        from test_living import RecallOrderTests

        helper = RecallOrderTests()
        controller, calls, _ = helper.controller()
        controller.living = MagicMock()
        controller.living.seen.side_effect = lambda *a, **k: calls.append('seen')
        await helper.talk(controller)
        self.assertLess(calls.index('/recall'), calls.index('seen'))


class DatabaseRecallTests(unittest.TestCase):
    def setUp(self):
        import pgtemp
        if not pgtemp.available():
            self.skipTest(pgtemp.reason())
        self.db = pgtemp.database()
        self.addCleanup(self.db.close)

    def test_due_and_confident_matches_are_bounded_and_read_only(self):
        with self.db.session() as conn:
            # pgtemp専用のデータ。実DB・.envは参照しない。
            conn.execute('TRUNCATE memory_long,persona_character')
            conn.execute("INSERT INTO living_activity(id,activity,energy,last_seen_at) VALUES (true,'reading',20,now()-interval '4 days')")
            conn.execute("INSERT INTO living_emotion(name,value,updated_at) VALUES ('happiness',90,now())")
            for i in range(5):
                conn.execute("""INSERT INTO memory_concern(topic,kind,quote,opened_at,due_at)
                    VALUES (%s,'plan','予定',now()-interval '2 days',now()-interval '1 day')""", (f'予定{i}',))
            conn.execute("UPDATE memory_concern SET due_at=now()+interval '1 day' WHERE topic='予定3'")
            conn.execute("UPDATE memory_concern SET resolved_at=now() WHERE topic='予定4'")
            for name, confidence in [('猫', 0.9), ('本', 0.8), ('お茶', 0.6), ('犬', 0.59), ('星', 1.0)]:
                conn.execute('''INSERT INTO persona_favorite(name,valence,confidence,evidence,updated_at)
                    VALUES (%s,0.9,%s,3,now())''', (name, confidence))
            result = memory_store.recall(conn, '猫と本とお茶と犬')
            self.assertEqual(result['mood'], 'sleepy')
            self.assertEqual(result['living']['activity'], 'reading')
            self.assertEqual([row['topic'] for row in result['pending_topic']], ['予定0', '予定1', '予定2'])
            self.assertEqual([row['name'] for row in result['favorites']], ['猫', '本'])
            result = memory_store.recall(conn, 'お茶と犬')
            self.assertEqual([row['name'] for row in result['favorites']], ['お茶'])
            self.assertEqual(memory_store.recall(conn, '未一致')['favorites'], [])

    def test_recall_does_not_touch_last_used_until_completion(self):
        selected, unused, turn_id, client_id = uuid4(), uuid4(), uuid4(), uuid4()
        with self.db.session() as conn:
            conn.execute('TRUNCATE memory_long,memory_short')
            for key, row_id in [('猫好き', selected), ('登山', unused)]:
                conn.execute('''INSERT INTO memory_long(id,topic_key,summary,kind,support_level)
                    VALUES (%s,%s,%s,'explicit','stated')''', (row_id, key, key))
            result = memory_store.recall(conn, '猫好き')
            self.assertEqual([row['id'] for row in result['wisdom']], [selected])
            self.assertIsNone(result['wisdom'][0]['last_used_at'])
            conn.execute('''INSERT INTO memory_short(id,turn_id,role,content,status,origin_client_id,input_mode)
                VALUES (%s,%s,'user','猫','pending',%s,'text')''', (uuid4(), turn_id, client_id))
        with patch.object(memory_api, 'connection', side_effect=self.db.session):
            memory_api.complete(turn_id, Completion(answer='猫だね', recalled_ids=[selected]), SimpleNamespace())
        with self.db.session() as conn:
            used = {row['id']: row['last_used_at'] for row in conn.execute('SELECT id,last_used_at FROM memory_long').fetchall()}
            self.assertIsNotNone(used[selected])
            self.assertIsNone(used[unused])
