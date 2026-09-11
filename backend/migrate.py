"""Apply only the local application's additive schema setup. Never reset data."""
from pathlib import Path
import sys

from app.config import Settings
from app.memory_api import database_connection


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
        for name in ('002_memory_jobs.sql', '003_reminders.sql'):
            conn.execute((root / name).read_text(encoding='utf-8').replace('BEGIN;', '').replace('COMMIT;', ''))
    print('Database schema ready. Existing conversations were preserved.')


if __name__ == '__main__':
    try:
        migrate()
    except Exception:
        print('Database setup failed. Check PostgreSQL and backend/.env locally. No credentials are printed.')
        sys.exit(1)
