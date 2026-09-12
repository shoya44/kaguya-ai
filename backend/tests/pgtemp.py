"""使い捨てのローカルPostgreSQL。backend/.env は読まず、本番DBには触れない。

クラスタはプロセスごとに1つだけ作り、終了時に止める。PostgreSQLの実行ファイルが
見つからない環境では available() が False を返し、DBを使うテストはskipされる。
"""
import atexit
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / 'backend' / 'migrations'
SCRIPTS = ('001_init.sql', '002_memory_jobs.sql', '003_reminders.sql', '004_local_state.sql')
# 設定・予定・Mindの表。各テストの前に空へ戻す。会話側は使うテストが自分で消す。
LOCAL_TABLES = ('app_settings', 'calendar_events', 'mind_emotions', 'mind_traits',
                'mind_phrases', 'mind_graph_edges', 'mind_open_loops', 'mind_meta')

_state: dict = {'dsn': '', 'reason': '', 'started': False}


def _version_key(path: Path) -> tuple:
    return tuple(int(part) if part.isdigit() else -1 for part in path.name.split('.'))


def _binary(name: str) -> str:
    suffix = '.exe' if os.name == 'nt' else ''
    directory = os.environ.get('TEST_POSTGRES_BIN')
    if directory:
        candidate = Path(directory) / (name + suffix)
        return str(candidate) if candidate.exists() else ''
    found = shutil.which(name)
    if found:
        return found
    patterns = ([Path(r'C:\Program Files\PostgreSQL')] if os.name == 'nt'
                else [Path('/usr/lib/postgresql'), Path('/usr/local/pgsql')])
    for base in patterns:
        if not base.exists():
            continue
        # 新しいバージョンから先に試す。文字列順だと '9.6' が '18' より後になる。
        for directory in sorted(base.iterdir(), key=_version_key, reverse=True):
            candidate = directory / 'bin' / (name + suffix)
            if candidate.exists():
                return str(candidate)
    return ''


def _start() -> None:
    _state['started'] = True
    try:
        _boot()
    except Exception as exc:  # 起動できない環境ではskipに落とす。テスト全体は止めない。
        _state['reason'] = f'{type(exc).__name__}: {exc}'


def _boot() -> None:
    initdb, pg_ctl = _binary('initdb'), _binary('pg_ctl')
    if not initdb or not pg_ctl:
        _state['reason'] = 'PostgreSQL was not found. Set TEST_POSTGRES_BIN to its bin directory.'
        return
    import psycopg

    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    # Windowsでは、管理者がinitdbを実行すると制限トークンに降格される。降格後は
    # リポジトリ配下に書けずPermission deniedになるため、一時フォルダに作り、
    # BUILTIN\Users（降格後も残るグループ）へ書き込みを許可しておく。
    base = Path(tempfile.gettempdir()) if os.name == 'nt' else ROOT / '.test-output'
    base.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='pg-test-', dir=base)).resolve()
    assert temp.is_relative_to(base.resolve())
    if os.name == 'nt':
        subprocess.run(['icacls', str(temp), '/grant', '*S-1-5-32-545:(OI)(CI)F'],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    data = temp / 'data'
    atexit.register(shutil.rmtree, temp, True)
    # 失敗したときに何が起きたか読めるよう、initdbの出力はそのまま例外に載せる。
    setup = subprocess.run([initdb, '-D', str(data), '-A', 'trust', '-U', 'tester', '--no-locale', '-E', 'UTF8'],
                           capture_output=True, text=True, errors='replace',
                           timeout=120, creationflags=flags)
    if setup.returncode:
        raise RuntimeError(f'initdb failed ({setup.returncode}): {setup.stdout}{setup.stderr}')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # Unixソケットの既定の置き場（/var/run/postgresql）は書けないことがある。
    # 使い捨てディレクトリに向ける。Windowsにはこの指定が無いので付けない。
    options = f'-h 127.0.0.1 -p {port} -F'
    if os.name != 'nt':
        options += f' -k "{temp}"'
    startup = subprocess.run([pg_ctl, '-D', str(data), '-l', str(temp / 'postgres.log'), '-w',
                              '-o', options, 'start'],
                             capture_output=True, timeout=60, creationflags=flags)
    if startup.returncode:
        log = temp / 'postgres.log'
        detail = log.read_text(encoding='utf-8', errors='replace') if log.exists() else ''
        raise RuntimeError('pg_ctl start failed: ' + (detail or startup.stderr.decode('utf-8', 'replace')))
    atexit.register(subprocess.run, [pg_ctl, '-D', str(data), '-m', 'immediate', '-w', 'stop'],
                    capture_output=True, timeout=60)
    dsn = f'host=127.0.0.1 port={port} user=tester dbname=postgres connect_timeout=5'
    with psycopg.connect(dsn, autocommit=True) as conn:
        for name in SCRIPTS:
            conn.execute((MIGRATIONS / name).read_text(encoding='utf-8'))
    _state['dsn'] = dsn


def dsn() -> str:
    if not _state['started']:
        _start()
    return _state['dsn']


def available() -> bool:
    value = bool(dsn())
    # CIでPostgreSQLが無いまま全部skipされると、緑なのに何も試していない状態になる。
    if not value and os.environ.get('KAGUYA_REQUIRE_DB'):
        raise RuntimeError('KAGUYA_REQUIRE_DB is set but PostgreSQL could not start: ' + reason())
    return value


def reason() -> str:
    return _state['reason'] or 'PostgreSQL is unavailable'


def new():
    """使い捨てクラスタへの接続をもう1本開く。中身はそのまま。"""
    from app.db import Database
    return Database(dsn())


def database():
    """テスト用のDatabase。毎回、設定・予定・Mindの表を空に戻してから返す。"""
    db = new()
    with db.session() as conn:
        conn.execute('TRUNCATE ' + ','.join(LOCAL_TABLES))
    return db
