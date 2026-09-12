"""アプリの保存先をPostgreSQL 1本にまとめるための、接続の置き場。

会話・記憶に加えて、設定・予定・Kaguya Mindも同じDBに入る。ファイルとして残るのは
ユーザーが自分で置く references/ と、秘密情報の .env だけ。
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import RLock

import psycopg
from psycopg.rows import dict_row


class Database:
    """1本の接続を使い回す。毎回つなぎ直すと、そのぶん返事が遅れるため。"""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.lock = RLock()
        self._conn: psycopg.Connection | None = None

    def _connect(self) -> psycopg.Connection:
        conn = self._conn
        if conn is not None and not conn.closed and not conn.broken:
            return conn
        self.close()
        if not self.dsn:
            raise RuntimeError('database_url is not configured')
        conn = psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)
        self._conn = conn
        return conn

    @contextmanager
    def session(self):
        """1呼び出し＝1トランザクション。失敗した接続は捨て、次回に開き直す。"""
        with self.lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except psycopg.Error:
                    self.close()
                raise

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except psycopg.Error:
                pass
