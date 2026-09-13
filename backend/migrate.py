"""Apply only the local application's additive schema setup. Never reset data.

Also moves the last three local files (settings.json / calendar.json / mind.db)
into the database, once. After that the check costs three os.stat calls: the
files are renamed to *.migrated, so they are simply no longer there.
"""
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.config import Settings
from app.memory_api import database_connection

SCRIPTS = ('002_memory_jobs.sql', '003_reminders.sql', '004_local_state.sql',
           '005_memory_redesign.sql', '006_emotion_counters.sql',
           '007_organize_budget.sql', '008_persona_rows.sql',
           '009_persona_opinion.sql')


def load_scripts(root: Path) -> dict[str, str]:
    """統合DDLを適用履歴の識別子で分ける。見出しの欠落・重複・順序違いは拒否。"""
    parts = re.split(r'^-- migration: (\d{3}_[a-z_]+\.sql)\s*$',
                     (root / 'schema.sql').read_text(encoding='utf-8'), flags=re.MULTILINE)
    if tuple(parts[1::2]) != ('001_init.sql', *SCRIPTS):
        raise ValueError('schema.sql migration sections are missing, duplicated, or out of order')
    return dict(zip(parts[1::2], parts[2::2]))


# mind.db の表 → PostgreSQL の表と、時刻として読み直す列。
# phrases / graph_edges / meta は 005 で廃止したので取り込まない。
MIND_TABLES = (
    ('emotions', 'living_emotion', ('updated_at',)),
    ('traits', 'persona_favorite', ('updated_at',)),
    ('open_loops', 'memory_concern', ('opened_at', 'due_at', 'last_asked_at', 'resolved_at')),
)


def _stamp(value):
    """ファイルの時刻はタイムゾーンなしのこともある。そのときはこのPCの時刻とみなす。"""
    if not value:
        return None
    stamp = datetime.fromisoformat(str(value))
    return stamp if stamp.tzinfo else stamp.astimezone()


def _import_settings(conn, path: Path) -> None:
    if conn.execute("SELECT 1 FROM app_settings WHERE key='options'").fetchone():
        return
    loaded = json.loads(path.read_text(encoding='utf-8'))
    for key in ('options', 'ledger'):
        value = loaded.get(key)
        if isinstance(value, dict):
            conn.execute('INSERT INTO app_settings(key,value,updated_at) VALUES (%s,%s,now())',
                         (key, Jsonb(value)))


def _import_calendar(conn, path: Path) -> None:
    if conn.execute('SELECT 1 FROM calendar_events LIMIT 1').fetchone():
        return
    items = json.loads(path.read_text(encoding='utf-8'))
    for item in items if isinstance(items, list) else []:
        start_at, end_at = _stamp(item.get('start')), _stamp(item.get('end'))
        title = str(item.get('title', '')).strip()
        if not title or not start_at or (end_at and end_at <= start_at):
            continue
        try:
            event_id = UUID(str(item.get('id')))
        except (TypeError, ValueError):
            event_id = uuid4()
        conn.execute("""INSERT INTO calendar_events(id,title,start_at,end_at,note)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                     (event_id, title, start_at, end_at, str(item.get('note', ''))))


def _import_mind(conn, path: Path) -> None:
    source = sqlite3.connect(path)
    source.row_factory = sqlite3.Row
    try:
        for old_name, new_name, stamps in MIND_TABLES:
            if conn.execute(f'SELECT 1 FROM {new_name} LIMIT 1').fetchone():
                continue
            try:
                rows = source.execute(f'SELECT * FROM {old_name}').fetchall()
            except sqlite3.Error:
                continue  # 古いmind.dbにはまだ無い表がある。
            for row in rows:
                values = {key: row[key] for key in row.keys()}
                for key in stamps:
                    values[key] = _stamp(values.get(key))
                columns = ','.join(f'"{key}"' for key in values)
                placeholders = ','.join(['%s'] * len(values))
                conn.execute(f'INSERT INTO {new_name}({columns}) VALUES ({placeholders}) ON CONFLICT DO NOTHING',
                             tuple(values.values()))
    finally:
        source.close()


def import_local_files(conn, data_dir: Path) -> list[Path]:
    """読み込んだファイルを返す。改名はしない。コミット前に消すと取り込みごと失うため。"""
    done = []
    for name, load in (('settings.json', _import_settings), ('calendar.json', _import_calendar),
                       ('mind.db', _import_mind)):
        path = data_dir / name
        if not path.exists():
            continue
        load(conn, path)
        done.append(path)
    return done


def retire(paths: list[Path]) -> list[str]:
    """コミット後に呼ぶ。*.migrated にして、次の起動では存在確認だけで済むようにする。"""
    for path in paths:
        path.replace(path.with_name(path.name + '.migrated'))
        for suffix in ('-wal', '-shm'):
            Path(str(path) + suffix).unlink(missing_ok=True)
    return [path.name for path in paths]


def _run(conn, root: Path, name: str, done: set) -> None:
    """まだ当てていないスクリプトだけを流し、当てたことを記録する。

    以前は毎回すべて流していた（IF NOT EXISTS / ON CONFLICT で冪等なため）。
    005 で表の改名が入り、改名は2度流せないので、適用済みを記録する方式にした。
    """
    if name in done:
        return
    # Drop BEGIN/COMMIT wrappers; every script belongs to this one transaction.
    conn.execute(load_scripts(root)[name].replace('BEGIN;', '').replace('COMMIT;', ''))
    conn.execute('INSERT INTO schema_migrations(name) VALUES (%s) ON CONFLICT DO NOTHING', (name,))
    done.add(name)


def migrate():
    settings = Settings()
    with database_connection(settings) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(8765001)')
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        done = {row['name'] for row in conn.execute('SELECT name FROM schema_migrations').fetchall()}
        # 005 の前後どちらの名前でも「初期化済み」と判断する。ここを取り違えると、
        # 改名後のDBへ 001 を流し直してしまう。
        exists = conn.execute("""SELECT coalesce(to_regclass('public.memory_short'),
            to_regclass('public.raw_memory')) AS name""").fetchone()['name']
        root = Path(__file__).parent / 'migrations'
        if not exists:
            _run(conn, root, '001_init.sql', done)
        for name in SCRIPTS:
            _run(conn, root, name, done)
        imported = import_local_files(conn, settings.data_dir)
    # ここまで来ていればDBへ入っている。ファイルを片付けるのはコミットの後。
    moved = retire(imported)
    print('Database schema ready. Existing conversations were preserved.')
    if moved:
        print('Moved into the database: ' + ', '.join(moved)
              + ' (the old files are kept next to them as *.migrated).')


if __name__ == '__main__':
    try:
        migrate()
    except Exception:
        print('Database setup failed. Check PostgreSQL and backend/.env locally. No credentials are printed.')
        sys.exit(1)
