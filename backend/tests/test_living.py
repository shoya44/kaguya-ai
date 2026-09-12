"""かぐやの「いまの状態」。端末ごとではなくサーバが決めることを確かめる。"""
import unittest
from datetime import datetime, timedelta, timezone

from app import living

import pgtemp

JST = timezone(timedelta(hours=9))
NOON = datetime(2026, 9, 12, 12, tzinfo=JST)
NIGHT = datetime(2026, 9, 12, 3, tzinfo=JST)


class ActivityTests(unittest.TestCase):
    def test_just_now_means_she_is_here(self):
        self.assertEqual(living.activity(NOON, timedelta(minutes=1), [0] * 24, 0), 'idle')

    def test_a_short_absence_is_spent_nearby(self):
        found = living.activity(NOON, timedelta(minutes=10), [0] * 24, 0)
        self.assertIn(found, living.NEARBY)

    def test_a_long_absence_is_spent_on_her_own(self):
        found = living.activity(NOON, timedelta(hours=2), [0] * 24, 0)
        self.assertIn(found, living.AWAY)

    def test_she_sleeps_late_at_night_when_nobody_comes(self):
        self.assertEqual(living.activity(NIGHT, timedelta(hours=1), [0] * 24, 0), 'sleeping')

    def test_she_stays_up_at_an_hour_they_usually_talk(self):
        hours = [0] * 24
        hours[NIGHT.hour] = 20
        self.assertNotEqual(living.activity(NIGHT, timedelta(hours=1), hours, 0), 'sleeping')

    def test_the_same_situation_always_looks_the_same(self):
        """乱数を使わない。画面を開き直すたびに行動が変わると落ち着かない。"""
        first = living.activity(NOON, timedelta(hours=2), [0] * 24, 7)
        for _ in range(5):
            self.assertEqual(living.activity(NOON, timedelta(hours=2), [0] * 24, 7), first)

    def test_every_activity_is_one_the_screen_can_draw(self):
        for idle in (0, 5, 40, 120):
            for now in (NOON, NIGHT):
                found = living.activity(now, timedelta(minutes=idle), [0] * 24, 3)
                self.assertIn(found, living.ACTIVITIES)


class EnergyTests(unittest.TestCase):
    def test_energy_follows_the_time_of_day(self):
        self.assertGreater(living.energy(NOON, [0] * 24), living.energy(NIGHT, [0] * 24))

    def test_an_hour_they_usually_talk_lifts_her_a_little(self):
        hours = [0] * 24
        hours[NIGHT.hour] = 9
        self.assertGreater(living.energy(NIGHT, hours), living.energy(NIGHT, [0] * 24))

    def test_energy_stays_between_0_and_100(self):
        hours = [999] * 24
        for hour in range(24):
            value = living.energy(NOON.replace(hour=hour), hours)
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)

    def test_only_the_busiest_hours_are_learned(self):
        hours = [0] * 24
        for hour, count in enumerate([1, 9, 3, 7, 5] + [0] * 19):
            hours[hour] = count
        self.assertEqual(living.learned_hours(hours), {1, 3, 4})


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class LivingStoreTests(unittest.TestCase):
    def setUp(self):
        self.db = pgtemp.database()
        self.addCleanup(self.db.close)
        self.store = living.LivingStore(self.db)

    def test_a_fresh_install_starts_with_one_row(self):
        self.assertEqual(self.store.familiarity(), {'chats': 0, 'days': 0})

    def test_a_finished_conversation_is_counted_once_per_day(self):
        self.store.seen(NOON, counted=True)
        self.store.seen(NOON + timedelta(hours=5), counted=True)
        self.store.seen(NOON + timedelta(days=1), counted=True)
        self.assertEqual(self.store.familiarity(), {'chats': 3, 'days': 2})

    def test_merely_showing_up_does_not_count_as_a_conversation(self):
        self.store.seen(NOON, counted=False)
        self.assertEqual(self.store.familiarity()['chats'], 0)
        self.assertEqual(self.store.state(NOON)['activity'], 'idle')

    def test_the_visit_is_shared_by_every_device(self):
        """PCで話した直後にiPhoneを開いても「寝てた」と言わない。"""
        self.store.seen(NOON, counted=True)
        other = living.LivingStore(self.db)
        self.assertEqual(other.state(NOON + timedelta(minutes=1))['activity'], 'idle')

    def test_the_hours_they_talk_are_remembered(self):
        for _ in range(3):
            self.store.seen(NOON, counted=True)
        self.assertEqual(self.store.cache['hour_counts'][NOON.hour], 3)

    def test_an_unreadable_store_never_breaks_the_conversation(self):
        broken = living.LivingStore.__new__(living.LivingStore)
        broken.db = None  # session() で落ちる
        broken.cache = dict(living.EMPTY)
        broken.seen(NOON, counted=True)
        self.assertEqual(broken.familiarity()['chats'], 0)
        self.assertIn(broken.state(NOON)['activity'], living.ACTIVITIES)



class ChatPathTests(unittest.IsolatedAsyncioTestCase):
    """会った記録は文字チャットでも通話でも同じ場所へ入る。"""

    def controller(self):
        from unittest.mock import AsyncMock, MagicMock
        from types import SimpleNamespace
        from app.controller import Controller
        runtime = SimpleNamespace(data={'ledger': {}}, record=MagicMock(),
                                  options=SimpleNamespace(reply_tokens=1024))
        memory = SimpleNamespace(begin=AsyncMock(return_value={'status': 'pending'}),
                                 context=AsyncMock(return_value=[]), call=AsyncMock(return_value={}),
                                 complete=AsyncMock(), fail=AsyncMock())
        seen = []
        store = SimpleNamespace(seen=lambda now, counted: seen.append(counted),
                                familiarity=lambda: {'chats': 0, 'days': 0},
                                state=lambda now: {'activity': 'idle', 'energy': 60, 'last_seen_at': None})
        controller = Controller(memory, SimpleNamespace(reply=AsyncMock(return_value='へんじ')),
                                AsyncMock(), runtime, living=store)
        return controller, seen

    async def test_a_finished_turn_counts_once_and_arriving_does_not(self):
        controller, seen = self.controller()
        await controller.send({'turn_id': '1', 'client_id': 'pc', 'text': 'やっほ', 'input_mode': 'text'},
                              unittest.mock.AsyncMock())
        await controller.task
        self.assertEqual(seen, [False, True])

    async def test_the_voice_path_records_the_visit_too(self):
        """通話でも会った記録を残す。ここが抜けると通話だけ慣れが育たない。"""
        import inspect
        from app import voice
        source = inspect.getsource(voice)
        self.assertIn('living.seen', source)
        self.assertNotIn('record_success', source)


if __name__ == '__main__':
    unittest.main()
