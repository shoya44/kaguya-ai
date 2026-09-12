import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.controller import Controller
from app.mind import KaguyaMind
from app.mood import Mood

import pgtemp


JST = timezone(timedelta(hours=9))
NOON = datetime(2026, 9, 12, 12, 0, tzinfo=JST)
NIGHT = datetime(2026, 9, 12, 23, 30, tzinfo=JST)


class MoodTests(unittest.TestCase):
    def test_praise_and_goodnight_hold_for_a_while_then_fall_back_to_the_clock(self):
        mood = Mood()
        self.assertEqual(mood.current(NOON), 'normal')
        mood.react('かぐや、ありがとう。助かった', NOON)
        self.assertEqual(mood.current(NOON + timedelta(minutes=5)), 'happy')
        self.assertEqual(mood.current(NOON + timedelta(minutes=13)), 'normal')

    def test_being_compared_is_not_treated_as_praise(self):
        mood = Mood()
        # 「好き」を含むが褒め言葉ではない。living.tsでは褒め判定が先に当たっていた。
        mood.react('ChatGPTの方が好きかも', NOON)
        self.assertEqual(mood.current(NOON), 'sulky')

    def test_late_night_is_sleepy_without_any_conversation(self):
        self.assertEqual(Mood().current(NIGHT), 'sleepy')

    def test_nothing_to_react_to_leaves_the_face_alone(self):
        mood = Mood()
        mood.react('明日の天気は？', NOON)
        self.assertEqual(mood.current(NOON), 'normal')


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class MindExpressionTests(unittest.TestCase):
    def setUp(self):
        self.db = pgtemp.database()
        self.addCleanup(self.db.close)
        self.minds = []

    def mind(self, enabled=lambda: True) -> KaguyaMind:
        mind = KaguyaMind(self.db, enabled)
        self.minds.append(mind)
        return mind

    def test_off_gives_no_face_so_the_simple_mood_is_used(self):
        self.assertEqual(self.mind(lambda: False).face(NOON), '')

    def test_being_compared_shows_a_sulky_face(self):
        mind = self.mind()
        mind.before_reply('ChatGPTの方が賢いよね', NOON)
        self.assertEqual(mind.face(NOON), 'sulky')

    def test_praise_shows_a_happy_face(self):
        mind = self.mind()
        for minute in range(3):
            mind.before_reply('かぐや、ありがとう。えらいね', NOON + timedelta(minutes=minute))
        self.assertEqual(mind.face(NOON + timedelta(minutes=3)), 'happy')

    def test_a_worried_kaguya_does_not_smile(self):
        """気分ラベルが「少し心配している」なのに顔だけ普段どおり、をなくす。"""
        mind = self.mind()
        # 心配は1度では閾値に届かない。褒め言葉も同時に入るが、笑顔にはしない。
        for minute in range(4):
            mind.before_reply('かぐや、ありがとう。でも最近つらいし不安なんだ', NOON + timedelta(minutes=minute))
        self.assertEqual(mind.face(NOON + timedelta(minutes=4)), 'worried')

    def test_late_night_shows_a_sleepy_face(self):
        self.assertEqual(self.mind().face(NIGHT), 'sleepy')


class FaceBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def controller(self, mind=None):
        runtime = SimpleNamespace(data={'ledger': {}}, record=MagicMock(),
                                  options=SimpleNamespace(reply_tokens=1024))
        memory = SimpleNamespace(begin=AsyncMock(return_value={'status': 'pending'}),
                                 context=AsyncMock(return_value=[]), call=AsyncMock(return_value={}),
                                 complete=AsyncMock(), fail=AsyncMock())
        return Controller(memory, SimpleNamespace(reply=AsyncMock(return_value='reply')),
                          AsyncMock(), runtime, mind=mind)

    @staticmethod
    def faces(broadcast):
        return [call.args[0]['mood'] for call in broadcast.await_args_list
                if call.args and call.args[0].get('type') == 'mood.changed']

    @staticmethod
    def turn(turn_id, text):
        return {'turn_id': turn_id, 'client_id': 'pc', 'text': text, 'input_mode': 'text'}

    async def talk(self, controller, turn_id, text):
        await controller.send(self.turn(turn_id, text), AsyncMock())
        await controller.task

    async def test_the_same_face_is_sent_to_every_connected_device(self):
        controller = self.controller()
        await self.talk(controller, '1', 'かぐや、ありがとう')
        # 端末ごとに判定せず、全接続へ1度だけ配信する。iPhone側も同じ顔になる。
        self.assertEqual(self.faces(controller.broadcast), ['happy'])

    async def test_an_unchanged_face_is_not_resent(self):
        controller = self.controller()
        await self.talk(controller, '1', 'かぐや、ありがとう')
        await self.talk(controller, '2', 'かぐや、えらいね')
        self.assertEqual(self.faces(controller.broadcast), ['happy'])

    async def test_being_compared_changes_the_face_mid_conversation(self):
        controller = self.controller()
        await self.talk(controller, '1', 'かぐや、ありがとう')
        await self.talk(controller, '2', 'でもChatGPTの方が賢いよね')
        self.assertEqual(self.faces(controller.broadcast), ['happy', 'sulky'])


class TuningTests(unittest.TestCase):
    """調整値がtuningへ集約されていることを、実際の振る舞いで確認する。"""

    def test_changing_a_threshold_changes_the_face(self):
        from app import tuning
        from app.mind.engine import KaguyaMind as Engine
        values = dict(tuning.EMOTION_BASELINE, happiness=71)
        self.assertEqual(Engine._expression(values, 80), 'happy')
        with unittest.mock.patch.dict(tuning.EMOTION_THRESHOLD, {'happiness': 99}):
            self.assertEqual(Engine._expression(values, 80), 'normal')

    def test_every_expression_has_a_sprite_on_screen(self):
        """表情を足したのに画面側の絵が無い、という食い違いを防ぐ。"""
        from pathlib import Path
        from app.mood import FACES
        avatar = Path(__file__).resolve().parents[2] / 'frontend/src/avatar.ts'
        source = avatar.read_text(encoding='utf-8')
        mapping = source.split('const MOOD_SPRITES')[1].split('};')[0]
        for face in FACES:
            self.assertIn(f'{face}:', mapping, face)

    def test_changing_a_hold_time_changes_how_long_the_mood_lasts(self):
        from app import tuning
        mood = Mood()
        with unittest.mock.patch.dict(tuning.MOOD_HOLD, {'happy': timedelta(minutes=1)}):
            mood.react('ありがとう', NOON)
            self.assertEqual(mood.current(NOON + timedelta(seconds=30)), 'happy')
            self.assertEqual(mood.current(NOON + timedelta(minutes=2)), 'normal')

    def test_the_energy_table_covers_every_hour(self):
        from app.mind.engine import KaguyaMind as Engine
        hours = [Engine._energy(NOON.replace(hour=hour)) for hour in range(24)]
        self.assertEqual(len(hours), 24)
        self.assertTrue(all(isinstance(value, float) for value in hours))


if __name__ == '__main__':
    unittest.main()


class ThinkDelayTests(unittest.TestCase):
    """返事を書き始めるまでの「間」。即答が続くと機械的に見える。"""

    def test_a_heavy_message_gets_a_pause(self):
        from app.mood import think_delay
        self.assertGreater(think_delay('最近しんどくて何もやる気が出ない'), 0)

    def test_an_ordinary_message_is_answered_at_once(self):
        from app.mood import think_delay
        self.assertEqual(think_delay('今日は何して遊ぶ？'), 0)
        self.assertEqual(think_delay(''), 0)

    def test_a_sleepy_kaguya_pauses_a_little(self):
        from app.mood import think_delay
        self.assertGreater(think_delay('おはよう', '眠そう'), 0)

    def test_the_pause_never_makes_the_app_feel_slow(self):
        from app import tuning
        from app.mood import think_delay
        for text, mood in (('つらい', '眠そう'), ('疲れた', ''), ('やっほ', '眠そう')):
            self.assertLessEqual(think_delay(text, mood), tuning.THINK_DELAY_MAX)
