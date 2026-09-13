"""DBを起動せず、統合DDLの適用単位と履歴互換性を検証する。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import migrate

ROOT = Path(__file__).resolve().parents[1] / 'migrations'
NAMES = ('001_init.sql', '002_memory_jobs.sql', '003_reminders.sql',
         '004_local_state.sql', '005_memory_redesign.sql', '006_emotion_counters.sql',
         '007_organize_budget.sql', '008_persona_rows.sql',
         '009_persona_opinion.sql')


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


class SettingsDocumentationTests(unittest.TestCase):
    """設定のキー名がREADMEに載っていること。

    値は app_settings を直接編集して変える運用なので、キー名が分からないと
    そもそも変えられない。項目を足したときに書き忘れると、使う側からは
    存在しない設定になる。
    """

    def test_readme_lists_every_setting_key(self):
        from app.runtime import Options
        readme = (ROOT.parents[1] / 'README.md').read_text(encoding='utf-8')
        section = readme.split('# 設定\n')[1].split('\n# 基本操作')[0]
        for name in Options.model_fields:
            with self.subTest(setting=name):
                self.assertIn(f'`{name}`', section)

    def test_readme_does_not_invent_settings(self):
        """無い設定を書かない。書いてあるとおりに直すと起動しなくなる。"""
        import re
        from app.runtime import Options
        readme = (ROOT.parents[1] / 'README.md').read_text(encoding='utf-8')
        section = readme.split('# 設定\n')[1].split('\n# 基本操作')[0]
        listed = {m.group(1) for m in re.finditer(r'^\| `([a-z_]+)`', section, re.MULTILINE)}
        self.assertEqual(listed - set(Options.model_fields), set())
