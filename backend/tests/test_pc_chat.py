import json
import tempfile
import unittest
from pathlib import Path

from app import pc
from app.config import Settings


class PCChatRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_config = pc.CONFIG_PATH
        self.addCleanup(setattr, pc, 'CONFIG_PATH', self.old_config)
        pc.CONFIG_PATH = Path(self.temp.name) / 'pc_access.json'
        pc.CONFIG_PATH.write_text(json.dumps({
            'video_folders': {'movies': self.temp.name},
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


class PCChatWithoutRegistrationTests(unittest.TestCase):
    """何も登録していない人に「PCタブを開いたよ」と言わない。"""

    def config(self, body):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        old = pc.CONFIG_PATH
        self.addCleanup(setattr, pc, 'CONFIG_PATH', old)
        pc.CONFIG_PATH = Path(temp.name) / 'pc_access.json'
        if body is not None:
            pc.CONFIG_PATH.write_text(body, encoding='utf-8')
        return Path(temp.name)

    def test_nothing_registered_stays_normal_chat(self):
        self.config(None)
        for text in ('猫の動画を見せて', 'batを実行してお願い', 'PCタブを開いて'):
            with self.subTest(text=text):
                self.assertIsNone(pc.chat_action(text))

    def test_unreadable_config_stays_normal_chat(self):
        self.config('{ this is not json')
        self.assertIsNone(pc.chat_action('猫の動画を見せて'))

    def test_only_videos_registered_does_not_offer_bat(self):
        folder = self.config(json.dumps({
            'video_folders': {'movies': '.'}, 'commands': {},
            'tailscale_origin': '', 'tailscale_login': '',
        }))
        self.assertIsNone(pc.chat_action('batを実行してお願い'))
        self.assertIsNotNone(pc.chat_action('猫の動画を見せて'))
        self.assertTrue(folder.exists())

    def test_only_commands_registered_does_not_offer_videos(self):
        self.config(json.dumps({
            'video_folders': {},
            'commands': {'safe': {'name': 'テスト', 'path': 'C:/t.bat',
                                  'description': '', 'timeout_seconds': 30}},
            'tailscale_origin': '', 'tailscale_login': '',
        }))
        self.assertIsNone(pc.chat_action('猫の動画を見せて'))
        self.assertIsNotNone(pc.chat_action('テストを実行してお願い'))


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


class VideoDurationTests(unittest.TestCase):
    """一覧に出す「長さ」。mp4から読めなければ表示を省く。"""

    @staticmethod
    def mp4(timescale=600, length=90000, version=0):
        import struct
        if version == 1:
            body = b'\x01\x00\x00\x00' + b'\x00' * 16 + struct.pack('>IQ', timescale, length) + b'\x00' * 60
        else:
            body = b'\x00' * 4 + b'\x00' * 8 + struct.pack('>II', timescale, length) + b'\x00' * 60
        mvhd = struct.pack('>I', 8 + len(body)) + b'mvhd' + body
        moov = struct.pack('>I', 8 + len(mvhd)) + b'moov' + mvhd
        ftyp = struct.pack('>I', 16) + b'ftyp' + b'isom' + b'\x00\x00\x02\x00'
        return ftyp + moov

    def file(self, data: bytes) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'movie.mp4'
        path.write_bytes(data)
        return path

    def test_duration_is_read_from_the_header(self):
        self.assertEqual(pc.seconds(self.file(self.mp4())), 150.0)
        # 長い動画は64bit版のmvhdになる。
        self.assertEqual(pc.seconds(self.file(self.mp4(version=1, timescale=1000, length=7_200_000))), 7200.0)

    def test_unreadable_files_do_not_break_the_listing(self):
        for data in (b'not an mp4', b'', b'\x00\x00\x00\x10ftypisom'):
            with self.subTest(data=data):
                self.assertIsNone(pc.seconds(self.file(data)))
        self.assertIsNone(pc.seconds(Path('/does/not/exist.mp4')))

    def test_a_zero_timescale_is_not_divided_by(self):
        self.assertIsNone(pc.seconds(self.file(self.mp4(timescale=0))))


class OriginCheckTests(unittest.TestCase):
    """壊れたpc_access.jsonで、チャットWebSocketの接続判定を巻き添えにしない。"""

    def config(self, body):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        old = pc.CONFIG_PATH
        self.addCleanup(setattr, pc, 'CONFIG_PATH', old)
        pc.CONFIG_PATH = Path(temp.name) / 'pc_access.json'
        if body is not None:
            pc.CONFIG_PATH.write_text(body, encoding='utf-8')

    def test_unreadable_config_rejects_instead_of_raising(self):
        self.config('{ this is not json')
        settings = Settings()
        # 許可済みのオリジンは通り、未登録のオリジンは例外ではなくFalseで落ちる。
        self.assertTrue(settings.origin_allowed('http://tauri.localhost'))
        self.assertFalse(settings.origin_allowed('http://example.com'))

    def test_tailscale_origin_is_allowed_when_configured(self):
        self.config(json.dumps({
            'video_folders': {}, 'commands': {},
            'tailscale_origin': 'https://kaguya.example.ts.net', 'tailscale_login': 'me@example.com',
        }))
        settings = Settings()
        self.assertTrue(settings.origin_allowed('https://kaguya.example.ts.net'))
        self.assertFalse(settings.origin_allowed('https://other.example.ts.net'))

    def test_empty_origin_is_not_allowed_by_an_empty_setting(self):
        self.config(None)
        self.assertFalse(Settings().origin_allowed(''))


if __name__ == '__main__':
    unittest.main()
