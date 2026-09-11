"""Small, atomic local settings and scheduler ledger. No conversation copies."""
import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field


class Options(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    quiet: bool = False
    auto_jobs: bool = True
    proactive_minutes: int = Field(default=60, ge=60, le=240)
    daily_call_limit: int = Field(default=3, ge=1, le=3)
    # Thinking-capable models consume this budget before writing the answer,
    # so the ceiling has to allow more than a plain reply would need.
    reply_tokens: int = Field(default=1024, ge=256, le=8192)
    always_on_top: bool = True
    font_size: int = Field(default=14, ge=12, le=22)


class RuntimeStore:
    def __init__(self, directory: Path):
        self.path = directory / 'settings.json'
        self.lock = RLock()
        self.data = {'options': Options().model_dump(), 'ledger': {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding='utf-8'))
                self.data = {'options': Options.model_validate(loaded['options']).model_dump(),
                             'ledger': loaded.get('ledger', {})}
            except Exception:
                # A damaged settings file must never stop the app from starting.
                # Keep the unreadable copy aside and continue with defaults.
                self.path.replace(self.path.with_suffix('.json.bad'))

    @property
    def options(self):
        return Options.model_validate(self.data['options'])

    def save(self, options=None, ledger=None):
        with self.lock:
            value = {'options': options if options is not None else self.data['options'],
                     'ledger': ledger if ledger is not None else self.data['ledger']}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='settings-', suffix='.tmp', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(value, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
                self.data = value
            finally:
                if os.path.exists(name):
                    os.unlink(name)

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
