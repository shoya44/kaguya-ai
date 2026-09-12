"""Settings and the scheduler ledger. Stored in PostgreSQL, not in a file."""
import logging
from threading import RLock

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .db import Database

_log = logging.getLogger(__name__)


class Options(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    quiet: bool = False
    auto_jobs: bool = True
    # Experimental Kaguya Mind is opt-in. OFF preserves the pre-Mind behavior.
    mind_enabled: bool = False
    proactive_minutes: int = Field(default=60, ge=60, le=240)
    # 1回の整理で生の発言30件を処理する。上限3なら1日90件ぶん。よく話した日に
    # 追いつけるよう上限は広げ、既定は3のままにする。未整理は消えず溜まるだけ。
    daily_call_limit: int = Field(default=3, ge=1, le=10)
    # Thinking-capable models consume this budget before writing the answer,
    # so the ceiling has to allow more than a plain reply would need.
    reply_tokens: int = Field(default=1024, ge=256, le=8192)
    always_on_top: bool = True
    font_size: int = Field(default=14, ge=12, le=22)
    # 会話から天気を聞かれたときの既定地点。UI項目を増やさず、会話から変更できる。
    weather_location: str = Field(default='東京', min_length=1, max_length=80)


class RuntimeStore:
    """app_settings の options / ledger 2行が実体。読みは頻繁なので手元に持つ。"""

    def __init__(self, db: Database):
        self.db = db
        self.lock = RLock()
        self.data = {'options': Options().model_dump(), 'ledger': {}}
        self._load()

    def _load(self) -> None:
        with self.db.session() as conn:
            rows = conn.execute("SELECT key,value FROM app_settings WHERE key IN ('options','ledger')").fetchall()
        stored = {row['key']: row['value'] for row in rows}
        ledger = stored.get('ledger')
        self.data['ledger'] = ledger if isinstance(ledger, dict) else {}
        if 'options' not in stored:
            return
        try:
            self.data['options'] = Options.model_validate(stored['options']).model_dump()
        except Exception:
            # 壊れた設定でアプリが起動しなくなるのは困る。退避して既定値で続ける。
            _log.warning('stored options were unreadable; defaults are used', exc_info=True)
            with self.db.session() as conn:
                conn.execute("""INSERT INTO app_settings(key,value,updated_at) VALUES ('options_bad',%s,now())
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                             (Jsonb(stored['options']),))
                conn.execute("DELETE FROM app_settings WHERE key='options'")

    @property
    def options(self):
        return Options.model_validate(self.data['options'])

    def save(self, options=None, ledger=None):
        with self.lock:
            value = {'options': options if options is not None else self.data['options'],
                     'ledger': ledger if ledger is not None else self.data['ledger']}
            with self.db.session() as conn:
                for key in ('options', 'ledger'):
                    conn.execute("""INSERT INTO app_settings(key,value,updated_at) VALUES (%s,%s,now())
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                                 (key, Jsonb(value[key])))
            self.data = value

    def update(self, changes):
        with self.lock:
            options = Options.model_validate(self.data['options'] | changes)
            self.save(options=options.model_dump())
            return options

    def record(self, **changes):
        with self.lock:
            self.save(ledger=self.data['ledger'] | changes)

    def reserve_call(self, day: str):
        with self.lock:
            ledger = self.data['ledger']
            used = ledger.get('calls', 0) if ledger.get('call_day') == day else 0
            if used >= self.options.daily_call_limit:
                return False
            # Charge before calling: crashes and API failures still consume budget.
            self.record(call_day=day, calls=used + 1)
            return True
