"""settings.json / calendar.json / mind.db がDBへ移り、以後は残らないことを確かめる。"""
import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import migrate

import pgtemp

# 改名（005）を当てる前の形。ここから移せることを確かめる。
BEFORE_REDESIGN = pgtemp.SCRIPTS[:pgtemp.SCRIPTS.index('005_memory_redesign.sql')]

CALENDAR = [{'id': str(uuid4()), 'title': '歯医者', 'start': '2026-09-12T15:00:00+09:00',
             'end': None, 'note': '予約済み'}]
SETTINGS = {'options': {'quiet': True, 'reply_tokens': 768}, 'ledger': {'call_day': '2026-09-11', 'calls': 2}}


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class ImportLocalFilesTests(unittest.TestCase):
    def setUp(self):
        self.db = pgtemp.database()
        self.addCleanup(self.db.close)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data_dir = Path(temp.name)

    def write_files(self):
        (self.data_dir / 'settings.json').write_text(json.dumps(SETTINGS), encoding='utf-8')
        (self.data_dir / 'calendar.json').write_text(json.dumps(CALENDAR), encoding='utf-8')
        with contextlib.closing(sqlite3.connect(self.data_dir / 'mind.db')) as mind:
            mind.execute('CREATE TABLE traits (name TEXT PRIMARY KEY, valence REAL, confidence REAL,'
                         ' evidence INTEGER, updated_at TEXT)')
            mind.execute("INSERT INTO traits VALUES ('プリン',0.78,0.47,2,'2026-09-11T20:00:00+09:00')")
            # タイムゾーンなしの時刻も、このPCの時刻として読めること。
            mind.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)')
            mind.execute("INSERT INTO meta VALUES ('interactions','7','2026-09-11T20:00:00')")
            mind.commit()

    def run_import(self):
        with self.db.session() as conn:
            imported = migrate.import_local_files(conn, self.data_dir)
        return migrate.retire(imported)

    def rows(self, sql):
        with self.db.session() as conn:
            return conn.execute(sql).fetchall()

    def test_files_move_into_the_database_once_and_are_renamed(self):
        self.write_files()
        self.assertEqual(self.run_import(), ['settings.json', 'calendar.json', 'mind.db'])


        settings = {row['key']: row['value'] for row in self.rows('SELECT key,value FROM app_settings')}
        self.assertEqual(settings['options']['reply_tokens'], 768)
        self.assertEqual(settings['ledger']['calls'], 2)
        self.assertEqual([row['title'] for row in self.rows('SELECT title FROM calendar_events')], ['歯医者'])
        self.assertEqual(self.rows('SELECT name FROM persona_favorite')[0]['name'], 'プリン')
        # 時刻はタイムゾーン付きで入る。素の文字列のままだと後で比較が狂う。
        self.assertIsNotNone(self.rows('SELECT updated_at FROM persona_favorite')[0]['updated_at'].tzinfo)

        for name in ('settings.json', 'calendar.json', 'mind.db'):
            self.assertFalse((self.data_dir / name).exists())
            self.assertTrue((self.data_dir / (name + '.migrated')).exists())

    def test_the_second_start_does_nothing(self):
        self.write_files()
        self.run_import()
        self.assertEqual(self.run_import(), [])
        self.assertEqual(len(self.rows('SELECT id FROM calendar_events')), 1)

    def test_existing_database_values_are_not_overwritten(self):
        self.write_files()
        with self.db.session() as conn:
            conn.execute("INSERT INTO app_settings(key,value) VALUES ('options','{\"reply_tokens\": 2048}')")
        self.run_import()
        settings = {row['key']: row['value'] for row in self.rows('SELECT key,value FROM app_settings')}
        self.assertEqual(settings['options']['reply_tokens'], 2048)
        # 取り込まなかった場合もファイルは片付ける。次回また同じ判定をしないため。
        self.assertFalse((self.data_dir / 'settings.json').exists())


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class ImportFailureTests(unittest.TestCase):
    def test_a_failed_import_keeps_the_files(self):
        """取り込みが失敗したらファイルは残る。次回また試せる状態を保つ。"""
        db = pgtemp.database()
        self.addCleanup(db.close)
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            (data_dir / 'calendar.json').write_text('{ this is not json', encoding='utf-8')
            with self.assertRaises(ValueError), db.session() as conn:
                migrate.import_local_files(conn, data_dir)
            self.assertTrue((data_dir / 'calendar.json').exists())


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class RedesignMigrationTests(unittest.TestCase):
    """005 は名前と置き場所を直すだけ。会話も記憶も失わないことを確かめる。"""

    def setUp(self):
        # 004 までを当てた状態から始める。005 はこのテストの中で当てる。
        self.db = pgtemp.staged(BEFORE_REDESIGN)
        self.addCleanup(self.db.close)

    def apply(self):
        sql = migrate.load_scripts(pgtemp.MIGRATIONS)['005_memory_redesign.sql']
        with self.db.session() as conn:
            conn.execute(sql.replace('BEGIN;', '').replace('COMMIT;', ''))

    def rows(self, sql):
        with self.db.session() as conn:
            return conn.execute(sql).fetchall()

    def test_conversations_and_memories_survive_the_rename(self):
        turn = uuid4()
        with self.db.session() as conn:
            conn.execute("""INSERT INTO raw_memory(id,turn_id,role,content,status,origin_client_id,input_mode)
                VALUES (%s,%s,'user','ラーメンが好き','completed',%s,'text')""", (uuid4(), turn, uuid4()))
            conn.execute("""INSERT INTO wisdom(id,topic_key,summary,kind,support_level,importance,evidence)
                VALUES (%s,'食べ物','ラーメンが好き','explicit','stated',3,'[]')""", (uuid4(),))
            conn.execute("INSERT INTO mind_emotions VALUES ('happiness',71,now())")
            conn.execute("INSERT INTO mind_traits VALUES ('プリン',0.78,0.6,2,now())")
            conn.execute("""INSERT INTO mind_open_loops
                VALUES ('面接','plan','明日面接',now(),now(),NULL,0,NULL)""")
        self.apply()
        self.assertEqual(self.rows('SELECT content FROM memory_short')[0]['content'], 'ラーメンが好き')
        self.assertEqual(self.rows('SELECT topic_key FROM memory_long')[0]['topic_key'], '食べ物')
        self.assertEqual(self.rows('SELECT name FROM living_emotion')[0]['name'], 'happiness')
        self.assertEqual(self.rows('SELECT name FROM persona_favorite')[0]['name'], 'プリン')
        self.assertEqual(self.rows('SELECT topic FROM memory_concern')[0]['topic'], '面接')
        self.assertEqual(self.rows('SELECT key FROM persona_character ORDER BY key')[0]['key'],
                         'addressing')

    def test_new_memories_start_neutral(self):
        with self.db.session() as conn:
            conn.execute("""INSERT INTO wisdom(id,topic_key,summary,kind,support_level,importance,evidence)
                VALUES (%s,'食べ物','ラーメンが好き','explicit','stated',3,'[]')""", (uuid4(),))
        self.apply()
        self.assertEqual(self.rows('SELECT tone FROM memory_long')[0]['tone'], 0)

    def test_the_relationship_moves_out_of_the_scheduler_ledger(self):
        with self.db.session() as conn:
            conn.execute("""INSERT INTO app_settings(key,value) VALUES ('ledger',
                '{"calls": 2, "relationship_chats": 12, "relationship_days": ["2026-09-11"],
                  "relationship_style_hint": "短めに返す。"}')""")
            conn.execute("INSERT INTO mind_meta VALUES ('interactions','9',now())")
        self.apply()
        row = self.rows('SELECT chats,days FROM living_activity')[0]
        self.assertEqual(row['chats'], 12)
        self.assertEqual(row['days'], ['2026-09-11'])
        style = self.rows("SELECT value,locked FROM persona_character WHERE key='style_feedback'")[0]
        self.assertEqual(style['value'], '短めに返す。')
        self.assertTrue(style['locked'])
        ledger = self.rows("SELECT value FROM app_settings WHERE key='ledger'")[0]['value']
        self.assertEqual(ledger, {'calls': 2})

    def test_the_dead_tables_are_gone(self):
        self.apply()
        for name in ('mind_phrases', 'mind_graph_edges', 'mind_meta'):
            with self.subTest(name=name):
                found = self.rows(f"SELECT to_regclass('public.{name}') AS name")[0]['name']
                self.assertIsNone(found)


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class MigrationRunnerTests(unittest.TestCase):
    """起動のたびに migrate は走る。2度目以降に壊れないことを確かめる。"""

    def db_for(self, scripts):
        db = pgtemp.staged(scripts)
        self.addCleanup(db.close)
        return db

    def run_twice(self, db):
        # 本番の設定・DBには触れず、起動時と同じ関数をそのまま検証する。
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(migrate, 'Settings', return_value=SimpleNamespace(data_dir=Path(temp))), \
                 patch.object(migrate, 'database_connection', side_effect=lambda _: db.session()):
                for _ in range(2):
                    migrate.migrate()

    def test_an_existing_install_migrates_once_and_stays_put(self):
        db = self.db_for(BEFORE_REDESIGN)
        turn = uuid4()
        with db.session() as conn:
            conn.execute("""INSERT INTO raw_memory(id,turn_id,role,content,status,origin_client_id,input_mode)
                VALUES (%s,%s,'user','残っていること','completed',%s,'text')""", (uuid4(), turn, uuid4()))
        self.run_twice(db)
        with db.session() as conn:
            self.assertEqual(conn.execute('SELECT content FROM memory_short').fetchone()['content'],
                             '残っていること')
            applied = {row['name'] for row in conn.execute('SELECT name FROM schema_migrations').fetchall()}
        self.assertIn('005_memory_redesign.sql', applied)

    def test_a_fresh_install_ends_up_with_the_same_schema(self):
        db = self.db_for(())
        self.run_twice(db)
        with db.session() as conn:
            for name in ('memory_short', 'memory_long', 'memory_concern', 'living_emotion',
                         'living_activity', 'persona_character', 'persona_favorite'):
                with self.subTest(name=name):
                    found = conn.execute(f"SELECT to_regclass('public.{name}') AS name").fetchone()['name']
                    self.assertIsNotNone(found)

    def test_every_recorded_version_upgrades_without_replaying_applied_sections(self):
        for count in range(1, len(pgtemp.SCRIPTS) + 1):
            with self.subTest(version=count):
                applied = pgtemp.SCRIPTS[:count]
                db = self.db_for(applied)
                old_table = 'raw_memory' if count < 5 else 'memory_short'
                with db.session() as conn:
                    conn.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
                        name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())''')
                    for name in applied:
                        conn.execute('INSERT INTO schema_migrations(name) VALUES (%s) ON CONFLICT DO NOTHING', (name,))
                    stamps = {row['name']: row['applied_at'] for row in
                              conn.execute('SELECT * FROM schema_migrations').fetchall()}
                    conn.execute(f'''INSERT INTO {old_table}
                        (id,turn_id,role,content,status,origin_client_id,input_mode)
                        VALUES (%s,%s,'user','残す会話','completed',%s,'text')''',
                                 (uuid4(), uuid4(), uuid4()))
                self.run_twice(db)
                with db.session() as conn:
                    after = {row['name']: row['applied_at'] for row in
                             conn.execute('SELECT * FROM schema_migrations').fetchall()}
                    self.assertEqual(set(after), set(pgtemp.SCRIPTS))
                    self.assertEqual({name: after[name] for name in stamps}, stamps)
                    self.assertEqual(conn.execute('SELECT content FROM memory_short').fetchone()['content'],
                                     '残す会話')

    def test_whole_sql_on_a_fresh_database_can_then_use_the_normal_runner(self):
        db = self.db_for(())
        with db.session() as conn:
            # SQL単体を空DBに適用した場合も、次の起動で改名を再実行しない。
            sql = (pgtemp.MIGRATIONS / 'schema.sql').read_text(encoding='utf-8')
            conn.execute(sql.replace('BEGIN;', '').replace('COMMIT;', ''))
        self.run_twice(db)
        with db.session() as conn:
            applied = {row['name'] for row in conn.execute('SELECT name FROM schema_migrations').fetchall()}
            self.assertEqual(applied, set(pgtemp.SCRIPTS))
