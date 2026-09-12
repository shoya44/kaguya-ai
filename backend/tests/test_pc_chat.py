import json
import tempfile
import unittest
from pathlib import Path

from app import pc


class PCChatRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_config = pc.CONFIG_PATH
        self.addCleanup(setattr, pc, 'CONFIG_PATH', self.old_config)
        pc.CONFIG_PATH = Path(self.temp.name) / 'pc_access.json'
        pc.CONFIG_PATH.write_text(json.dumps({
            'video_folders': {},
            'commands': {
                'bat-backup': {
                    'name': 'バックアップ', 'path': r'C:\\tools\\backup.bat',
                    'description': 'バックアップを実行', 'timeout_seconds': 300,
                },
                'bat-backup-full': {
                    'name': 'バックアップ完全', 'path': r'C:\\tools\\backup-full.bat',
                    'description': '完全バックアップを実行', 'timeout_seconds': 300,
                },
            },
            'tailscale_origin': '', 'tailscale_login': '',
        }, ensure_ascii=False), encoding='utf-8')

    def test_video_request_opens_pc_search(self):
        for text in ('猫の動画を再生して', '猫の動画を再生してほしい', '猫の動画を再生してください'):
            with self.subTest(text=text):
                result = pc.chat_action(text)
                self.assertEqual(result['event'], {'query': '猫', 'autoload_video': True})

    def test_registered_bat_opens_confirmation_target(self):
        result = pc.chat_action('バックアップ完全を実行して')
        self.assertEqual(result['event']['command_id'], 'bat-backup-full')

    def test_question_does_not_open_execution_confirmation(self):
        self.assertIsNone(pc.chat_action('バックアップを実行しても大丈夫？'))
        self.assertIsNone(pc.chat_action('バックアップの実行方法を教えて'))

    def test_unrelated_media_request_stays_normal_chat(self):
        self.assertIsNone(pc.chat_action('音楽を流して'))


class PCServiceSafetyTests(unittest.TestCase):
    def test_deleted_bat_after_prepare_is_clean_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            old_config = pc.CONFIG_PATH
            self.addCleanup(setattr, pc, 'CONFIG_PATH', old_config)
            root = Path(temp)
            bat = root / 'safe.bat'
            bat.write_text('@echo off\n', encoding='utf-8')
            pc.CONFIG_PATH = root / 'pc_access.json'
            pc.CONFIG_PATH.write_text(json.dumps({
                'video_folders': {},
                'commands': {'safe': {
                    'name': '安全テスト', 'path': str(bat), 'description': '', 'timeout_seconds': 5,
                }},
                'tailscale_origin': '', 'tailscale_login': '',
            }, ensure_ascii=False), encoding='utf-8')
            service = pc.PCService()
            owner = 'owner'
            token = service.prepare('safe', owner)['confirmation']
            bat.unlink()
            with self.assertRaises(pc.HTTPException) as caught:
                service.start(token, owner)
            self.assertEqual(caught.exception.status_code, 409)


if __name__ == '__main__':
    unittest.main()
