"""settings.json / calendar.json / mind.db がDBへ移り、以後は残らないことを確かめる。"""
import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import migrate

import pgtemp

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
        self.assertEqual(self.rows("SELECT value FROM mind_meta WHERE key='interactions'")[0]['value'], '7')
        self.assertEqual(self.rows('SELECT name FROM mind_traits')[0]['name'], 'プリン')
        # 時刻はタイムゾーン付きで入る。素の文字列のままだと後で比較が狂う。
        self.assertIsNotNone(self.rows('SELECT updated_at FROM mind_traits')[0]['updated_at'].tzinfo)

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
