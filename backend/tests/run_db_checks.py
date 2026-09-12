"""Disposable PostgreSQL integration checks; never reads backend/.env.

Run with the existing Python dependencies and PostgreSQL binaries installed.
The cluster, role, data and port are exclusively created for this process.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import memory_store as store
import pgtemp



class DatabaseChecks(unittest.TestCase):
    def connect(self):
        return psycopg.connect(pgtemp.dsn(), row_factory=dict_row)

    def setUp(self):
        with self.connect() as conn:
            conn.execute('TRUNCATE memory_short,memory_long,persona_character,reminders')
            conn.execute("INSERT INTO persona_character(key,value,locked) VALUES ('reply_style','\"短く\"',false)")

    def add(self, content='お茶が好き', days=0, processed=False):
        raw_id, turn_id = uuid4(), uuid4()
        with self.connect() as conn:
            conn.execute('''INSERT INTO memory_short(id,turn_id,role,content,status,origin_client_id,input_mode,created_at,processed_at)
                VALUES (%s,%s,'user',%s,'completed',%s,'text',%s,%s)''',
                         (raw_id, turn_id, content, uuid4(), datetime.now(timezone.utc) - timedelta(days=days),
                          datetime.now(timezone.utc) if processed else None))
            conn.execute('''INSERT INTO memory_short(id,turn_id,role,content,status,origin_client_id,input_mode,created_at,processed_at)
                VALUES (%s,%s,'assistant','了解','completed',%s,'text',%s,%s)''',
                         (uuid4(), turn_id, uuid4(), datetime.now(timezone.utc) - timedelta(days=days),
                          datetime.now(timezone.utc) if processed else None))
        return str(raw_id)

    def batch(self, raw_id, topic='飲み物'):
        return {'items': [{'topic_key': topic, 'summary': 'お茶が好き', 'kind': 'explicit', 'importance': 3,
                           'evidence_ids': [raw_id]}]}

    def organize(self, raw_id):
        with self.connect() as conn: snap = store.snapshot(conn)
        with self.connect() as conn: store.commit_wisdom(conn, snap, self.batch(raw_id))
        return snap

    def test_atomic_idempotency_and_next_conversation_recall(self):
        raw_id = self.add()
        snap = self.organize(raw_id)
        with self.assertRaises(HTTPException):
            with self.connect() as conn: store.commit_wisdom(conn, snap, self.batch(raw_id))
        with self.connect() as conn:
            recalled = store.recall(conn, '好きなお茶は？')
            self.assertEqual(len(recalled['wisdom']), 1)
            self.assertEqual(len(recalled['wisdom'][0]['evidence']), 1)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM memory_short WHERE processed_at IS NOT NULL').fetchone()['n'], 2)

    def test_recall_prefers_the_current_message_over_the_surrounding_context(self):
        with self.connect() as conn:
            for topic, text in [('コーヒー', 'コーヒーはブラックで飲む'), ('確定申告', '確定申告の書類は書斎の棚')]:
                conn.execute('''INSERT INTO memory_long(id,topic_key,summary,kind,support_level,importance,evidence)
                    VALUES (%s,%s,%s,'explicit','stated',3,'[]')''', (uuid4(), topic, text))
        with self.connect() as conn:
            # 直前の話題（コーヒー）に引きずられず、今の質問の記憶を先頭に置く。
            recalled = store.recall(conn, '確定申告の書類ってどこだっけ', 'コーヒーの話をしていた')
        self.assertEqual(recalled['wisdom'][0]['topic_key'], '確定申告')

    def test_recall_matches_across_half_and_full_width_spellings(self):
        with self.connect() as conn:
            conn.execute('''INSERT INTO memory_long(id,topic_key,summary,kind,support_level,importance,evidence)
                VALUES (%s,'GitHub','GitHubのアカウントはshoya44','explicit','stated',3,'[]')''', (uuid4(),))
        with self.connect() as conn:
            recalled = store.recall(conn, 'ＧＩＴＨＵＢのアカウント教えて')
        self.assertEqual([row['topic_key'] for row in recalled['wisdom']], ['GitHub'])

    def test_memory_tab_search_ignores_half_and_full_width_spellings(self):
        with self.connect() as conn:
            conn.execute('''INSERT INTO memory_long(id,topic_key,summary,kind,support_level,importance,evidence)
                VALUES (%s,'ＧｉｔＨｕｂ','ＧｉｔＨｕｂのアカウントはshoya44','explicit','stated',3,'[]')''', (uuid4(),))
        with self.connect() as conn:
            found = store.list_memories(conn, 'wisdom', 'github')
        self.assertEqual(len(found['items']), 1)

    def test_summary_counts_exactly_what_organizing_would_pick_up(self):
        self.add()
        with self.connect() as conn:
            result = store.summary(conn)
            self.assertEqual(result['pending'], len(store.snapshot(conn)['raw']))
            self.assertEqual(result['recent'], [])

    def test_unknown_evidence_does_not_consume_original(self):
        self.add()
        with self.connect() as conn: snap = store.snapshot(conn)
        with self.assertRaises(ValueError):
            with self.connect() as conn: store.commit_wisdom(conn, snap, self.batch(str(uuid4())))
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM memory_short WHERE processed_at IS NULL').fetchone()['n'], 2)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM memory_long').fetchone()['n'], 0)

    def test_correction_invalidates_inflight_snapshot(self):
        raw_id = self.add()
        with self.connect() as conn: snap = store.snapshot(conn)
        with self.connect() as conn: store.mutate(conn, 'raw', raw_id, {'revision': 1, 'value': 'コーヒーが好き'})
        with self.assertRaises(HTTPException):
            with self.connect() as conn: store.commit_wisdom(conn, snap, self.batch(raw_id))
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT content FROM memory_short WHERE id=%s', (raw_id,)).fetchone()['content'], 'コーヒーが好き')
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM memory_long').fetchone()['n'], 0)

    def test_three_day_weekly_update_and_cascade_clears_rollback(self):
        ids = []
        for day in [3, 2, 1]:
            raw_id = self.add(days=day); ids.append(raw_id); self.organize(raw_id)
        with self.connect() as conn: snap = store.weekly_snapshot(conn)
        self.assertEqual(len(snap['wisdom']), 1)
        wisdom_id = str(snap['wisdom'][0]['id'])
        with self.connect() as conn:
            store.commit_persona(conn, snap, {'key': 'reply_style', 'value': '飲み物の話題を尊重する', 'source_wisdom_ids': [wisdom_id]})
        with self.connect() as conn:
            store.mutate(conn, 'raw', ids[0], {'revision': 1}, delete=True)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM memory_long').fetchone()['n'], 0)
            persona = conn.execute("SELECT * FROM persona_character WHERE key='reply_style'").fetchone()
            self.assertIsNone(persona['previous_value'])
            self.assertEqual(persona['source_wisdom_ids'], [])

    def test_weekly_rejects_two_days_and_locked_persona(self):
        for day in [2, 1]: self.organize(self.add(days=day))
        with self.connect() as conn:
            self.assertEqual(store.weekly_snapshot(conn)['wisdom'], [])
        self.organize(self.add())
        with self.connect() as conn:
            snap = store.weekly_snapshot(conn)
            conn.execute("UPDATE persona_character SET locked=true WHERE key='reply_style'")
        with self.assertRaises(HTTPException):
            with self.connect() as conn:
                store.commit_persona(conn, snap, {'key': 'reply_style', 'value': '短く', 'source_wisdom_ids': [str(snap['wisdom'][0]['id'])]})

    def test_retention_keeps_unprocessed_pairs(self):
        kept = self.add(days=10)
        self.add(days=10, processed=True)
        with self.connect() as conn:
            self.assertEqual(store.cleanup(conn)['removed'], 2)
            self.assertIsNotNone(conn.execute('SELECT id FROM memory_short WHERE id=%s', (kept,)).fetchone())
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM memory_short').fetchone()['n'], 2)

    def test_manual_wisdom_edit_removes_sources_and_protects_correction(self):
        raw_id = self.add(); self.organize(raw_id)
        with self.connect() as conn:
            wisdom = conn.execute('SELECT * FROM memory_long').fetchone()
            store.mutate(conn, 'wisdom', str(wisdom['id']), {'revision': 1, 'value': 'コーヒーが好き'})
        with self.connect() as conn:
            wisdom = conn.execute('SELECT * FROM memory_long').fetchone()
            self.assertEqual(wisdom['summary'], 'コーヒーが好き'); self.assertTrue(wisdom['locked'])
            self.assertEqual(wisdom['evidence'], [])
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM memory_short').fetchone()['n'], 0)

    def test_reminders_survive_poll_and_reconnect_until_acknowledged(self):
        now = datetime.now(timezone.utc)
        with self.connect() as conn:
            store.add_reminder(conn, now - timedelta(minutes=2), 'first')
            store.add_reminder(conn, now - timedelta(minutes=1), 'second')
            first = store.take_due_reminders(conn, now)['items'][0]
            self.assertEqual(first['message'], 'first')
        with self.connect() as conn:
            self.assertEqual(store.take_due_reminders(conn, now)['items'][0]['id'], first['id'])
            store.acknowledge_reminder(conn, first['id'])
            store.acknowledge_reminder(conn, first['id'])
        with self.connect() as conn:
            second = store.take_due_reminders(conn, now)['items'][0]
            self.assertEqual(second['message'], 'second')
            store.acknowledge_reminder(conn, second['id'])
            self.assertEqual(store.take_due_reminders(conn, now)['items'], [])


def main():
    # クラスタの用意は pgtemp に任せる。unittest 側と同じ作り方・同じ移行スクリプト。
    if not pgtemp.available():
        print(pgtemp.reason())
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DatabaseChecks))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
