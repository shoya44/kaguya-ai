"""Apply only the local application's additive schema setup. Never reset data.

Also moves the last three local files (settings.json / calendar.json / mind.db)
into the database, once. After that the check costs three os.stat calls: the
files are renamed to *.migrated, so they are simply no longer there.
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.config import Settings
from app.memory_api import database_connection

SCRIPTS = ('002_memory_jobs.sql', '003_reminders.sql', '004_local_state.sql')
# mind.db の表 → PostgreSQL の表と、時刻として読み直す列。
MIND_TABLES = (
    ('emotions', 'mind_emotions', ('updated_at',)),
    ('traits', 'mind_traits', ('updated_at',)),
    ('phrases', 'mind_phrases', ('last_seen_at',)),
    ('graph_edges', 'mind_graph_edges', ('updated_at',)),
    ('open_loops', 'mind_open_loops', ('opened_at', 'due_at', 'last_asked_at', 'resolved_at')),
    ('meta', 'mind_meta', ('updated_at',)),
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


def migrate():
    settings = Settings()
    with database_connection(settings) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(8765001)')
        exists = conn.execute("SELECT to_regclass('public.raw_memory') AS name").fetchone()['name']
        root = Path(__file__).parent / 'migrations'
        if not exists:
            # Drop BEGIN/COMMIT wrappers; both scripts belong to this transaction.
            conn.execute((root / '001_init.sql').read_text(encoding='utf-8').replace('BEGIN;', '').replace('COMMIT;', ''))
        # 追加分は毎回流す（IF NOT EXISTS / ON CONFLICT で冪等）。
        for name in SCRIPTS:
            conn.execute((root / name).read_text(encoding='utf-8').replace('BEGIN;', '').replace('COMMIT;', ''))
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
