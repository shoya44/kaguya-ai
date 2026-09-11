"""Private SQLite storage for Kaguya Mind.

This database is deliberately separate from the main PostgreSQL memory store.
If it is deleted or unreadable, the core app can continue without Mind.
"""
from __future__ import annotations

import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock


DEFAULT_EMOTIONS = {
    'happiness': 58.0,
    'curiosity': 60.0,
    'boredom': 25.0,
    'affection': 55.0,
    'jealousy': 5.0,
    'concern': 10.0,
}


SCHEMA = """
    CREATE TABLE IF NOT EXISTS emotions (
        name TEXT PRIMARY KEY,
        value REAL NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS traits (
        name TEXT PRIMARY KEY,
        valence REAL NOT NULL,
        confidence REAL NOT NULL,
        evidence INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS phrases (
        text TEXT PRIMARY KEY,
        count INTEGER NOT NULL,
        last_seen_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS graph_edges (
        subject TEXT NOT NULL,
        relation TEXT NOT NULL,
        object TEXT NOT NULL,
        strength REAL NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(subject, relation, object)
    );
    CREATE TABLE IF NOT EXISTS open_loops (
        topic TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        quote TEXT NOT NULL,
        opened_at TEXT NOT NULL,
        due_at TEXT NOT NULL,
        last_asked_at TEXT,
        asked INTEGER NOT NULL DEFAULT 0,
        resolved_at TEXT
    );
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
"""


class MindStore:
    """1本の接続を使い回す。毎ターンの接続確立をイベントループ上で繰り返さないため。"""

    def __init__(self, path: Path):
        self.path = path
        self.lock = RLock()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 会話は単一のイベントループから来るが、接続を跨いで使うため明示的に許可する。
        conn = sqlite3.connect(self.path, timeout=2.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=2000')
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn
        return conn

    @contextmanager
    def _session(self):
        with self.lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except BaseException:
                # 壊れたかもしれない接続は捨てる。次の呼び出しで開き直す。
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                self.close()
                raise

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def reset(self) -> None:
        """mind.dbを完全に削除する。会話・記憶・Relationship Memoryには触れない。"""
        with self.lock:
            self.close()
            for suffix in ('', '-wal', '-shm'):
                Path(str(self.path) + suffix).unlink(missing_ok=True)

    def emotions(self, now: datetime) -> tuple[dict[str, float], datetime]:
        stamp = now.isoformat()
        with self.lock, self._session() as conn:
            for name, value in DEFAULT_EMOTIONS.items():
                conn.execute('INSERT OR IGNORE INTO emotions(name,value,updated_at) VALUES (?,?,?)',
                             (name, value, stamp))
            rows = conn.execute('SELECT name,value,updated_at FROM emotions').fetchall()
        values = {row['name']: float(row['value']) for row in rows}
        updated = max((datetime.fromisoformat(row['updated_at']) for row in rows), default=now)
        return values, updated

    def save_emotions(self, values: dict[str, float], now: datetime) -> None:
        stamp = now.isoformat()
        with self.lock, self._session() as conn:
            for name, value in values.items():
                conn.execute('''INSERT INTO emotions(name,value,updated_at) VALUES (?,?,?)
                    ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at''',
                             (name, max(0.0, min(100.0, float(value))), stamp))

    def upsert_trait(self, name: str, valence: float, now: datetime) -> None:
        name = name.strip()[:40]
        if not name:
            return
        stamp = now.isoformat()
        with self.lock, self._session() as conn:
            old = conn.execute('SELECT * FROM traits WHERE name=?', (name,)).fetchone()
            if old:
                evidence = int(old['evidence']) + 1
                old_weight = min(int(old['evidence']), 5)
                value = (float(old['valence']) * old_weight + valence) / (old_weight + 1)
                direction_same = (float(old['valence']) >= .5) == (valence >= .5)
                confidence = min(.95, float(old['confidence']) + (.12 if direction_same else -.08))
                confidence = max(.20, confidence)
                conn.execute('''UPDATE traits SET valence=?,confidence=?,evidence=?,updated_at=? WHERE name=?''',
                             (value, confidence, evidence, stamp, name))
            else:
                conn.execute('INSERT INTO traits(name,valence,confidence,evidence,updated_at) VALUES (?,?,?,?,?)',
                             (name, valence, .35, 1, stamp))

    def traits_for(self, text: str, limit: int = 5) -> list[dict]:
        normalized = unicodedata.normalize('NFKC', text).casefold()
        with self.lock, self._session() as conn:
            rows = conn.execute('''SELECT * FROM traits
                ORDER BY confidence DESC,evidence DESC,updated_at DESC LIMIT 50''').fetchall()
        relevant = [row for row in rows if unicodedata.normalize('NFKC', row['name']).casefold() in normalized]
        fallback = [row for row in rows if row not in relevant and float(row['confidence']) >= .55]
        return [dict(row) for row in (relevant + fallback)[:limit]]

    def top_traits(self, limit: int = 8) -> list[dict]:
        with self.lock, self._session() as conn:
            rows = conn.execute('''SELECT * FROM traits
                ORDER BY confidence DESC,evidence DESC,updated_at DESC LIMIT ?''', (limit,)).fetchall()
        return [dict(row) for row in rows]

    def record_phrase(self, text: str, now: datetime) -> None:
        value = ' '.join(str(text or '').strip().split())
        if len(value) < 2 or len(value) > 32:
            return
        stamp = now.isoformat()
        with self.lock, self._session() as conn:
            conn.execute('''INSERT INTO phrases(text,count,last_seen_at) VALUES (?,?,?)
                ON CONFLICT(text) DO UPDATE SET count=count+1,last_seen_at=excluded.last_seen_at''',
                         (value, 1, stamp))

    def shortcut_candidates(self, limit: int = 5) -> list[dict]:
        with self.lock, self._session() as conn:
            rows = conn.execute('''SELECT text,count,last_seen_at FROM phrases WHERE count>=3
                ORDER BY count DESC,last_seen_at DESC LIMIT ?''', (limit,)).fetchall()
        return [dict(row) for row in rows]

    def upsert_edge(self, subject: str, relation: str, obj: str, strength: float, now: datetime) -> None:
        stamp = now.isoformat()
        with self.lock, self._session() as conn:
            conn.execute('''INSERT INTO graph_edges(subject,relation,object,strength,updated_at)
                VALUES (?,?,?,?,?) ON CONFLICT(subject,relation,object)
                DO UPDATE SET strength=excluded.strength,updated_at=excluded.updated_at''',
                         (subject[:80], relation[:40], obj[:80], max(0.0, min(1.0, strength)), stamp))

    def open_loop(self, topic: str, kind: str, quote: str, now: datetime, due: datetime) -> None:
        """未完の話題を1件記録する。同じ話題を再度聞いたら予定を上書きして開き直す。"""
        topic = topic.strip()[:40]
        if not topic:
            return
        with self.lock, self._session() as conn:
            conn.execute("""INSERT INTO open_loops(topic,kind,quote,opened_at,due_at,last_asked_at,asked,resolved_at)
                VALUES (?,?,?,?,?,NULL,0,NULL)
                ON CONFLICT(topic) DO UPDATE SET
                    kind=excluded.kind, quote=excluded.quote, opened_at=excluded.opened_at,
                    due_at=excluded.due_at, last_asked_at=NULL, asked=0, resolved_at=NULL""",
                         (topic, kind[:16], str(quote or '')[:60], now.isoformat(), due.isoformat()))

    def due_loops(self, now: datetime, limit: int = 2) -> list[dict]:
        """予定時刻を過ぎ、まだ触れていない話題だけを返す。しつこさを避けるため上限つき。"""
        stamp = now.isoformat()
        quiet = (now - timedelta(hours=12)).isoformat()
        with self.lock, self._session() as conn:
            rows = conn.execute("""SELECT * FROM open_loops
                WHERE resolved_at IS NULL AND due_at<=? AND asked<2
                  AND (last_asked_at IS NULL OR last_asked_at<=?)
                ORDER BY due_at ASC LIMIT ?""", (stamp, quiet, limit)).fetchall()
            for row in rows:
                conn.execute('UPDATE open_loops SET last_asked_at=? WHERE topic=?', (stamp, row['topic']))
        return [dict(row) for row in rows]

    def unresolved_topics(self, limit: int = 40) -> list[str]:
        with self.lock, self._session() as conn:
            rows = conn.execute("""SELECT topic FROM open_loops WHERE resolved_at IS NULL
                ORDER BY opened_at DESC LIMIT ?""", (limit,)).fetchall()
        return [row['topic'] for row in rows]

    def resolve_loop(self, topic: str, now: datetime) -> None:
        with self.lock, self._session() as conn:
            conn.execute('UPDATE open_loops SET resolved_at=? WHERE topic=? AND resolved_at IS NULL',
                         (now.isoformat(), topic))

    def mark_asked(self, topic: str, now: datetime) -> None:
        with self.lock, self._session() as conn:
            conn.execute('UPDATE open_loops SET asked=asked+1,last_asked_at=? WHERE topic=?',
                         (now.isoformat(), topic))

    def prune_loops(self, now: datetime) -> None:
        """人間は全部は覚えていない。片付いた話題と、古すぎる未完の話題は忘れる。"""
        with self.lock, self._session() as conn:
            conn.execute('DELETE FROM open_loops WHERE resolved_at IS NOT NULL AND resolved_at<?',
                         ((now - timedelta(days=14)).isoformat(),))
            conn.execute('DELETE FROM open_loops WHERE resolved_at IS NULL AND opened_at<?',
                         ((now - timedelta(days=30)).isoformat(),))

    def loop_stats(self) -> dict:
        with self.lock, self._session() as conn:
            row = conn.execute("""SELECT
                sum(CASE WHEN resolved_at IS NULL THEN 1 ELSE 0 END) AS open,
                sum(CASE WHEN resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS closed
                FROM open_loops""").fetchone()
        return {'open': int(row['open'] or 0), 'closed': int(row['closed'] or 0)}

    def increment(self, key: str, now: datetime) -> int:
        stamp = now.isoformat()
        with self.lock, self._session() as conn:
            row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            value = int(row['value']) + 1 if row else 1
            conn.execute('''INSERT INTO meta(key,value,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at''',
                         (key, str(value), stamp))
        return value

    def stats(self) -> dict:
        with self.lock, self._session() as conn:
            traits = conn.execute('SELECT count(*) AS n FROM traits').fetchone()['n']
            edges = conn.execute('SELECT count(*) AS n FROM graph_edges').fetchone()['n']
            row = conn.execute("SELECT value FROM meta WHERE key='interactions'").fetchone()
        return {'traits': int(traits), 'edges': int(edges), 'interactions': int(row['value']) if row else 0}
