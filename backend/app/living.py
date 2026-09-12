"""かぐやの「いまの状態」。活動・元気さ・会った記録を1箇所で決める。

以前は frontend/src/living.ts が端末のlocalStorageで同じ判定を持っていたため、
PCとiPhoneで別々の行動をしていた。元気さの計算もサーバ（tuning）とフロントの
両方にあり、数値も食い違っていた。かぐやはPC上に1人しかいないので、ここで決める。

表情（mood）は app/mood.py と mind/engine.py が決める。この module は
「何をしていて、どれくらい元気か」だけを持つ。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .db import Database
from .tuning import (ENERGY_BY_HOUR, LIVING_AWAKE_BONUS, LIVING_IDLE_AWAY, LIVING_IDLE_HERE,
                     LIVING_LEARNED_HOURS, LIVING_NIGHT_UNTIL, LIVING_SLEEP_IDLE)

_log = logging.getLogger('uvicorn.error')

# 画面に出せる活動。frontend/src/avatar.ts の LifeActivity と同じ並びにする。
ACTIVITIES = ('idle', 'reading', 'working', 'playing', 'snacking', 'daydreaming', 'sleeping')
# 相手がしばらく居ないときの過ごし方と、少しだけ離れているときの過ごし方。
AWAY = ('reading', 'playing', 'snacking', 'daydreaming')
NEARBY = ('reading', 'working', 'playing', 'daydreaming')

EMPTY = {'activity': 'idle', 'energy': 60, 'last_seen_at': None,
         'hour_counts': [0] * 24, 'chats': 0, 'days': []}


def energy(now: datetime, hour_counts) -> int:
    """時間帯で決まる元気さ。よく話す時間帯は少し上乗せする。"""
    value = next(number for until, number in ENERGY_BY_HOUR if now.hour < until)
    if now.hour in learned_hours(hour_counts):
        value += LIVING_AWAKE_BONUS
    return int(max(0, min(100, value)))


def learned_hours(hour_counts) -> set[int]:
    """よく会話する時間帯の上位3つ。0回の時間は数えない。"""
    counts = list(hour_counts or [])
    ranked = sorted(((int(count or 0), hour) for hour, count in enumerate(counts) if count),
                    reverse=True)[:LIVING_LEARNED_HOURS]
    return {hour for _, hour in ranked}


def activity(now: datetime, idle: timedelta, hour_counts, chats: int) -> str:
    """いま何をしているか。会っていない時間の長さと時刻だけで決める。

    乱数は使わない。同じ状況で画面を開き直すたびに行動が変わると落ち着かない。
    """
    if now.hour < LIVING_NIGHT_UNTIL and idle > LIVING_SLEEP_IDLE and now.hour not in learned_hours(hour_counts):
        return 'sleeping'
    if idle < LIVING_IDLE_HERE:
        return 'idle'
    choices = AWAY if idle > LIVING_IDLE_AWAY else NEARBY
    return choices[(now.day * 31 + now.hour * 7 + int(chats)) % len(choices)]


class LivingStore:
    """1行だけの表。読みは会話のたびに起きるので、手元にも持っておく。"""

    def __init__(self, db: Database):
        self.db = db
        self.cache = dict(EMPTY)
        self.load()

    def load(self) -> dict:
        try:
            with self.db.session() as conn:
                row = conn.execute('SELECT * FROM living_activity WHERE id').fetchone()
                if not row:
                    conn.execute('INSERT INTO living_activity(id) VALUES (true) ON CONFLICT DO NOTHING')
                    row = conn.execute('SELECT * FROM living_activity WHERE id').fetchone()
        except Exception:
            # 状態が読めないだけで会話を止めない。既定値のまま続ける。
            _log.warning('living_activity could not be read; defaults are used', exc_info=True)
            return self.cache
        if row:
            self.cache = {key: row[key] for key in EMPTY if key in row}
        return self.cache

    def seen(self, now: datetime, counted: bool) -> None:
        """会ったことを記録する。countedは会話が1往復成立したときだけTrue。"""
        hours = list(self.cache.get('hour_counts') or [0] * 24)
        if len(hours) != 24:
            hours = [0] * 24
        days = [str(day) for day in (self.cache.get('days') or []) if day]
        chats = int(self.cache.get('chats') or 0)
        if counted:
            hours[now.hour] += 1
            chats += 1
            today = now.date().isoformat()
            if today not in days:
                days.append(today)
            days = days[-1000:]
        try:
            with self.db.session() as conn:
                from psycopg.types.json import Jsonb
                conn.execute('''UPDATE living_activity SET last_seen_at=%s,hour_counts=%s,
                    chats=%s,days=%s,updated_at=now() WHERE id''',
                             (now, Jsonb(hours), chats, Jsonb(days)))
        except Exception:
            _log.warning('living_activity could not be written', exc_info=True)
            return
        self.cache.update(last_seen_at=now, hour_counts=hours, chats=chats, days=days)

    def state(self, now: datetime) -> dict:
        """画面へ配る現在の状態。保存はしない（時刻で決まるため読むたびに求まる）。"""
        last = self.cache.get('last_seen_at') or now
        idle = max(timedelta(0), now - last)
        hours = self.cache.get('hour_counts') or [0] * 24
        return {
            'activity': activity(now, idle, hours, int(self.cache.get('chats') or 0)),
            'energy': energy(now, hours),
            'last_seen_at': last.isoformat() if hasattr(last, 'isoformat') else None,
        }

    def familiarity(self) -> dict:
        """接し方の参考にする「慣れ」。会話回数と利用日数から決める。"""
        return {'chats': int(self.cache.get('chats') or 0),
                'days': len({str(day) for day in (self.cache.get('days') or []) if day})}
