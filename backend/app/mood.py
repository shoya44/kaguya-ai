"""端末に依存しないかぐやの表情。Kaguya MindのON/OFFに関わらず常に動く。

以前は frontend/src/living.ts が端末のlocalStorageで同じ判定を持っていたため、
PCで褒めてもiPhoneのかぐやは無反応、という食い違いが起きていた。iPhoneはPC上の
FastAPIへ繋いでいるだけなので、判定はここ1箇所に置く。
"""
import re
from datetime import datetime, timedelta


FACES = ('normal', 'happy', 'sleepy', 'sulky')

# 比較を先に見る。「ChatGPTの方が好き」は褒め言葉ではない。
_COMPARED = re.compile(r'(Claude|ChatGPT|チャットGPT).*(の方が|より).*(好き|賢い|すごい|良い|いい)', re.I)
_PRAISE = re.compile(r'(かわいい|好き|ありがとう|助かった|えらい|いい子)')
_GOODNIGHT = re.compile(r'(おやすみ|寝るね|寝よう)')

# living.tsが持っていた継続時間をそのまま引き継ぐ。
_HOLD = {
    'happy': timedelta(minutes=12),
    'sulky': timedelta(minutes=8),
    'sleepy': timedelta(minutes=30),
}


class Mood:
    """Mindを使わないときの控えめな表情。会話の言葉と時刻だけで決める。"""

    def __init__(self):
        self.value = ''
        self.until = None

    def react(self, text: str, now: datetime) -> None:
        value = str(text or '').strip()
        if not value:
            return
        if _COMPARED.search(value):
            face = 'sulky'
        elif _PRAISE.search(value):
            face = 'happy'
        elif _GOODNIGHT.search(value):
            face = 'sleepy'
        else:
            return
        self.value, self.until = face, now + _HOLD[face]

    def current(self, now: datetime) -> str:
        if self.until and now < self.until:
            return self.value
        self.value, self.until = '', None
        return 'sleepy' if now.hour < 6 or now.hour >= 23 else 'normal'
