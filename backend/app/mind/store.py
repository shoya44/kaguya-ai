"""Storage for Kaguya Mind, in the same PostgreSQL database as everything else.

Mind keeps its own connection so that a failure here rolls back only Mind's own
transaction; settings, the calendar and the conversation are untouched. If these
tables are empty or unreadable, the core app continues without Mind.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta

from ..db import Database
from ..tuning import (EMOTION_BASELINE, LOOP_FORGET_DAYS, LOOP_KEEP_RESOLVED_DAYS, LOOP_MAX_ASKS,
                      LOOP_QUIET, TRAIT_CONFIDENCE_DOWN,
                      TRAIT_CONFIDENCE_RANGE, TRAIT_CONFIDENCE_START, TRAIT_CONFIDENCE_UP,
                      TRAIT_EVIDENCE_WEIGHT_MAX, TRAIT_FORGET_DAYS, TRAIT_SETTLED)

# 既存の呼び出し名は変えず、値だけtuningへ移す。
DEFAULT_EMOTIONS = EMOTION_BASELINE

# reset() が空にする範囲。「育ったもの」だけを消し、会話と長期記憶は残す。
# living_activity は「いつ会ったか」なので、育ちの取り消しでは消さない。
TABLES = ('living_emotion', 'persona_favorite', 'memory_concern')


def _row(row: dict) -> dict:
    """時刻はISO文字列で返す。読み手（engine・画面）の形を変えないため。"""
    return {key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in row.items()}


class MindStore:
    def __init__(self, db: Database):
        self.db = db

    def _session(self):
        return self.db.session()

    def close(self) -> None:
        self.db.close()

    def reset(self) -> None:
        """育ったものを全部消す。他のテーブルには触れない。"""
        with self._session() as conn:
            for table in TABLES:
                conn.execute(f'DELETE FROM {table}')

    def emotions(self, now: datetime) -> tuple[dict[str, float], datetime]:
        with self._session() as conn:
            for name, value in DEFAULT_EMOTIONS.items():
                conn.execute('''INSERT INTO living_emotion(name,value,updated_at) VALUES (%s,%s,%s)
                    ON CONFLICT(name) DO NOTHING''', (name, value, now))
            rows = conn.execute('SELECT name,value,updated_at FROM living_emotion').fetchall()
        values = {row['name']: float(row['value']) for row in rows}
        updated = max((row['updated_at'] for row in rows), default=now)
        return values, updated

    def save_emotions(self, values: dict[str, float], now: datetime, high=()) -> None:
        """いまの値を書き、ついでに「何回のうち何回が強かったか」を数える。

        履歴の表は持たない。週次で傾向を見るのに必要なのは回数だけで、
        毎ターン1行増える記録は掃除の手間に見合わない。
        """
        high = set(high or ())
        with self._session() as conn:
            for name, value in values.items():
                conn.execute('''INSERT INTO living_emotion(name,value,updated_at,samples,high_count)
                    VALUES (%s,%s,%s,1,%s)
                    ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,
                        samples=living_emotion.samples+1,
                        high_count=living_emotion.high_count+excluded.high_count''',
                             (name, max(0.0, min(100.0, float(value))), now, int(name in high)))

    def counters(self) -> list[dict]:
        with self._session() as conn:
            rows = conn.execute('SELECT name,samples,high_count FROM living_emotion').fetchall()
        return [_row(row) for row in rows]

    def upsert_trait(self, name: str, valence: float, now: datetime) -> None:
        name = name.strip()[:40]
        if not name:
            return
        with self._session() as conn:
            old = conn.execute('SELECT * FROM persona_favorite WHERE name=%s FOR UPDATE', (name,)).fetchone()
            if old:
                evidence = int(old['evidence']) + 1
                old_weight = min(int(old['evidence']), TRAIT_EVIDENCE_WEIGHT_MAX)
                value = (float(old['valence']) * old_weight + valence) / (old_weight + 1)
                direction_same = (float(old['valence']) >= .5) == (valence >= .5)
                step = TRAIT_CONFIDENCE_UP if direction_same else -TRAIT_CONFIDENCE_DOWN
                low, high = TRAIT_CONFIDENCE_RANGE
                confidence = max(low, min(high, float(old['confidence']) + step))
                conn.execute('UPDATE persona_favorite SET valence=%s,confidence=%s,evidence=%s,updated_at=%s WHERE name=%s',
                             (value, confidence, evidence, now, name))
            else:
                conn.execute('INSERT INTO persona_favorite(name,valence,confidence,evidence,updated_at) VALUES (%s,%s,%s,%s,%s)',
                             (name, valence, TRAIT_CONFIDENCE_START, 1, now))

    def traits_for(self, text: str, limit: int = 5) -> list[dict]:
        normalized = unicodedata.normalize('NFKC', text).casefold()
        with self._session() as conn:
            rows = conn.execute('''SELECT * FROM persona_favorite
                ORDER BY confidence DESC,evidence DESC,updated_at DESC LIMIT 50''').fetchall()
        relevant = [row for row in rows if unicodedata.normalize('NFKC', row['name']).casefold() in normalized]
        fallback = [row for row in rows if row not in relevant and float(row['confidence']) >= .55]
        return [_row(row) for row in (relevant + fallback)[:limit]]

    def top_traits(self, limit: int = 8) -> list[dict]:
        with self._session() as conn:
            rows = conn.execute('''SELECT * FROM persona_favorite
                ORDER BY confidence DESC,evidence DESC,updated_at DESC LIMIT %s''', (limit,)).fetchall()
        return [_row(row) for row in rows]

    def open_loop(self, topic: str, kind: str, quote: str, now: datetime, due: datetime) -> None:
        """未完の話題を1件記録する。同じ話題を再度聞いたら予定を上書きして開き直す。"""
        topic = topic.strip()[:40]
        if not topic:
            return
        with self._session() as conn:
            conn.execute("""INSERT INTO memory_concern(topic,kind,quote,opened_at,due_at,last_asked_at,asked,resolved_at)
                VALUES (%s,%s,%s,%s,%s,NULL,0,NULL)
                ON CONFLICT(topic) DO UPDATE SET
                    kind=excluded.kind, quote=excluded.quote, opened_at=excluded.opened_at,
                    due_at=excluded.due_at, last_asked_at=NULL, asked=0, resolved_at=NULL""",
                         (topic, kind[:16], str(quote or '')[:60], now, due))

    def due_loops(self, now: datetime, limit: int = 2) -> list[dict]:
        """予定時刻を過ぎ、まだ触れていない話題だけを返す。しつこさを避けるため上限つき。"""
        with self._session() as conn:
            rows = conn.execute("""SELECT * FROM memory_concern
                WHERE resolved_at IS NULL AND due_at<=%s AND asked<%s
                  AND (last_asked_at IS NULL OR last_asked_at<=%s)
                ORDER BY due_at ASC LIMIT %s""",
                                (now, LOOP_MAX_ASKS, now - LOOP_QUIET, limit)).fetchall()
        # 読んだだけでは記録しない。実際に触れたときに mark_asked で数える。
        return [_row(row) for row in rows]

    def unresolved_topics(self, limit: int = 40) -> list[str]:
        with self._session() as conn:
            rows = conn.execute("""SELECT topic FROM memory_concern WHERE resolved_at IS NULL
                ORDER BY opened_at DESC LIMIT %s""", (limit,)).fetchall()
        return [row['topic'] for row in rows]

    def resolve_loop(self, topic: str, now: datetime) -> None:
        with self._session() as conn:
            conn.execute('UPDATE memory_concern SET resolved_at=%s WHERE topic=%s AND resolved_at IS NULL',
                         (now, topic))

    def mark_asked(self, topic: str, now: datetime) -> None:
        with self._session() as conn:
            # 同じ話題を会話側と声かけ側で二重に数えない（LOOP_QUIET の間は1回だけ）。
            conn.execute('''UPDATE memory_concern SET asked=asked+1,last_asked_at=%s WHERE topic=%s
                AND (last_asked_at IS NULL OR last_asked_at<=%s)''', (now, topic, now - LOOP_QUIET))

    def prune_loops(self, now: datetime) -> None:
        """人間は全部は覚えていない。片付いた話題と、古すぎる未完の話題は忘れる。"""
        with self._session() as conn:
            conn.execute('DELETE FROM memory_concern WHERE resolved_at IS NOT NULL AND resolved_at<%s',
                         (now - timedelta(days=LOOP_KEEP_RESOLVED_DAYS),))
            conn.execute('DELETE FROM memory_concern WHERE resolved_at IS NULL AND opened_at<%s',
                         (now - timedelta(days=LOOP_FORGET_DAYS),))

    def prune_traits(self, now: datetime) -> None:
        """人間は一度口にしただけの好みを覚えていない。定着しなかったものは忘れる。"""
        with self._session() as conn:
            conn.execute('DELETE FROM persona_favorite WHERE confidence<%s AND updated_at<%s',
                         (TRAIT_SETTLED, now - timedelta(days=TRAIT_FORGET_DAYS)))

    def loop_stats(self) -> dict:
        with self._session() as conn:
            row = conn.execute("""SELECT
                count(*) FILTER (WHERE resolved_at IS NULL) AS open,
                count(*) FILTER (WHERE resolved_at IS NOT NULL) AS closed
                FROM memory_concern""").fetchone()
        return {'open': int(row['open'] or 0), 'closed': int(row['closed'] or 0)}

    def stats(self) -> dict:
        """育ち具合の材料。会話回数は living_activity（かぐやの状態）が持つ。"""
        with self._session() as conn:
            traits = conn.execute('SELECT count(*) AS n FROM persona_favorite').fetchone()['n']
            row = conn.execute('SELECT chats FROM living_activity').fetchone()
        return {'traits': int(traits), 'interactions': int(row['chats']) if row else 0}
