"""Settings and the scheduler ledger. Stored in PostgreSQL, not in a file."""
import logging
from threading import RLock

from psycopg.types.json import Jsonb
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .db import Database

_log = logging.getLogger(__name__)

# 同じ日にこれだけ続けて失敗したら、その日の自動整理は止める。枠を戻す以上、
# 直らない理由（設定・スキーマ・モデル名）で延々と叩き続けないための歯止め。
JOB_FAIL_STOP = 2


class Options(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    quiet: bool = False
    auto_jobs: bool = True
    # Kaguya Mind は既定ON。OFFのままだと感情も好みも育たず、表情は時刻だけの
    # 簡易判定になる。切りたいときは app_settings の options で false にする。
    mind_enabled: bool = True
    proactive_minutes: int = Field(default=60, ge=60, le=240)
    # 1回の整理で生の発言60件を処理する。既定8なら1日480件ぶん。既定3・30件では
    # よく話した日に追いつけず、反映待ちが数百件たまったまま上限に当たっていた。
    # 未整理の原文は消えず溜まるだけなので、上限は「その日の追いつきやすさ」で決める。
    daily_call_limit: int = Field(default=8, ge=1, le=10)
    # Thinking-capable models consume this budget before writing the answer,
    # so the ceiling has to allow more than a plain reply would need.
    reply_tokens: int = Field(default=1024, ge=256, le=8192)
    always_on_top: bool = True
    font_size: int = Field(default=14, ge=12, le=22)
    # 会話から天気を聞かれたときの既定地点。UI項目を増やさず、会話から変更できる。
    weather_location: str = Field(default='東京', min_length=1, max_length=80)
    # 通話の声。名前の一覧はGemini側に従うため、選択式にせず入力できるようにする。
    # 無効な名前だと通話開始時に失敗し、その旨と使った名前が画面に出る。
    voice_name: str = Field(default='Leda', min_length=1, max_length=40, pattern=r'^[A-Za-z][A-Za-z0-9]*$')
    # 話し方。声の名前だけでは印象が決まらないので、トーン・語尾・テンポを言葉で指示する。
    # 空にすると指示なし（モデルの素の話し方）になる。
    voice_style: str = Field(default='少し高めの明るいトーンで、やわらかい語尾でかわいらしく話す。'
                                     '固くならず、友達に話しかけるように、短くテンポよく。',
                             max_length=300)
    # 読み上げをどこに任せるか。gemini はGemini Liveの声をそのまま流す（PC側に
    # 追加の用意が要らない）。local はPCのVOICEVOX互換エンジンで読み上げる。
    # local を選んでもエンジンに繋がらなければ gemini へ自動で戻す。
    voice_engine: Literal['gemini', 'local'] = 'gemini'
    # AivisSpeech は 10101、VOICEVOX は 50021。APIは互換なので同じ実装で動く。
    tts_url: str = Field(default='http://127.0.0.1:10101', min_length=1, max_length=200)
    tts_speaker: str = Field(default='コハク', min_length=1, max_length=80)
    tts_style: str = Field(default='ノーマル', min_length=1, max_length=80)


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
            # 先に引いておく。落ちた場合も二重に使わないため。呼び出しが
            # 失敗したと分かった時点で release_call が戻す。
            self.record(call_day=day, calls=used + 1)
            return True

    def release_call(self, day: str):
        """使えなかった枠を戻す。

        スキーマ不正のように毎回同じ理由で失敗する状態だと、戻さない限り
        1日の枠が処理ゼロのまま溶ける。実際それで原文が330件溜まっていた。
        """
        with self.lock:
            ledger = self.data['ledger']
            if ledger.get('call_day') != day:
                return
            self.record(calls=max(0, ledger.get('calls', 0) - 1))

    def note_failure(self, day: str):
        """その日の連続失敗を数える。枠を戻すぶん、止める条件が要る。"""
        with self.lock:
            ledger = self.data['ledger']
            fails = ledger.get('job_fails', 0) if ledger.get('fail_day') == day else 0
            self.record(fail_day=day, job_fails=fails + 1)

    def clear_failures(self, day: str):
        with self.lock:
            self.record(fail_day=day, job_fails=0)

    def failing(self, day: str) -> bool:
        ledger = self.data['ledger']
        if ledger.get('fail_day') != day:
            return False
        return ledger.get('job_fails', 0) >= JOB_FAIL_STOP
