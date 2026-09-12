"""DBを起動せず、統合DDLの適用単位と履歴互換性を検証する。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import migrate

ROOT = Path(__file__).resolve().parents[1] / 'migrations'
NAMES = ('001_init.sql', '002_memory_jobs.sql', '003_reminders.sql',
         '004_local_state.sql', '005_memory_redesign.sql', '006_emotion_counters.sql',
         '007_organize_budget.sql')


class ConsolidatedSchemaTests(unittest.TestCase):
    def test_sections_keep_the_persisted_migration_names_in_one_file(self):
        self.assertEqual(tuple(migrate.load_scripts(ROOT)), NAMES)
        self.assertEqual([p.name for p in ROOT.glob('*.sql')], ['schema.sql'])

    def test_invalid_section_order_or_missing_sections_fail_before_execution(self):
        original = (ROOT / 'schema.sql').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for heading in ('-- missing: 001_init.sql', '-- migration: 002_memory_jobs.sql'):
                with self.subTest(heading=heading):
                    (root / 'schema.sql').write_text(
                        original.replace('-- migration: 001_init.sql', heading), encoding='utf-8')
                    conn = Mock()
                    with self.assertRaises(ValueError):
                        migrate._run(conn, root, NAMES[0], set())
                    conn.execute.assert_not_called()

    def test_already_applied_sections_do_not_execute_sql(self):
        conn = Mock()
        done = set(NAMES)
        for name in NAMES:
            migrate._run(conn, ROOT, name, done)
        conn.execute.assert_not_called()

    def test_failed_sql_is_not_recorded_as_applied(self):
        conn = Mock()
        conn.execute.side_effect = RuntimeError('SQL failed')
        done = set()
        with self.assertRaises(RuntimeError):
            migrate._run(conn, ROOT, NAMES[0], done)
        self.assertEqual(done, set())
        self.assertEqual(conn.execute.call_count, 1)
