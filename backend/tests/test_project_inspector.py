import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import project_inspector


class ProjectInspectorTests(unittest.TestCase):
    def test_search_returns_context_and_skips_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'src').mkdir()
            (root / 'src' / 'sample.py').write_text('one\ntwo\ndef target():\n    return 1\nfive\nsix\n', encoding='utf-8')
            (root / 'node_modules').mkdir()
            (root / 'node_modules' / 'hidden.py').write_text('target', encoding='utf-8')
            with patch.object(project_inspector, 'ROOT', root):
                result = project_inspector.project_search('target')
            self.assertTrue(result['ok'])
            self.assertEqual(len(result['matches']), 1)
            self.assertEqual(result['matches'][0]['path'], 'src/sample.py')
            self.assertIn('def target()', result['matches'][0]['context'])

    def test_read_limits_requested_range(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'notes.md').write_text('\n'.join(f'line {n}' for n in range(1, 30)), encoding='utf-8')
            with patch.object(project_inspector, 'ROOT', root):
                result = project_inspector.project_read('notes.md', 5, 8)
            self.assertTrue(result['ok'])
            self.assertEqual((result['start_line'], result['end_line']), (5, 8))
            self.assertIn('5: line 5', result['content'])
            self.assertNotIn('9: line 9', result['content'])


if __name__ == '__main__':
    unittest.main()
