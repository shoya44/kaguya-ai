"""端末に依存しないかぐやの表情。Kaguya MindのON/OFFに関わらず常に動く。

以前は frontend/src/living.ts が端末のlocalStorageで同じ判定を持っていたため、
PCで褒めてもiPhoneのかぐやは無反応、という食い違いが起きていた。iPhoneはPC上の
FastAPIへ繋いでいるだけなので、判定はここ1箇所に置く。
"""
import random
import re
from datetime import datetime

from .tuning import (MOOD_HOLD, NIGHT_FROM_HOUR, NIGHT_UNTIL_HOUR,
                     THINK_DELAY_HEAVY, THINK_DELAY_JITTER, THINK_DELAY_MAX,
                     THINK_DELAY_READ_FROM, THINK_DELAY_READ_PER_100, THINK_DELAY_SLEEPY)


# 画面に出せる表情。frontend/src/avatar.ts の LifeMood と同じ並びにする。
FACES = ('normal', 'happy', 'sleepy', 'sulky', 'worried', 'bored')

# 比較を先に見る。「ChatGPTの方が好き」は褒め言葉ではない。
_COMPARED = re.compile(r'(Claude|ChatGPT|チャットGPT).*(の方が|より).*(好き|賢い|すごい|良い|いい)', re.I)
_PRAISE = re.compile(r'(かわいい|好き|ありがとう|助かった|えらい|いい子)')
_GOODNIGHT = re.compile(r'(おやすみ|寝るね|寝よう)')
# 相手が弱っているときに、かぐやだけ普段の顔をしているのは冷たく見える。
_WORRIED = re.compile(r'(つら|しんど|疲れた|無理|最悪|落ち込|不安|怖い|泣き)')


class Mood:
    """Mindを使わないときの控えめな表情。会話の言葉と時刻だけで決める。"""

    def __init__(self):
        self.value = ''
        self.until = None

    def react(self, text: str, now: datetime) -> None:
        value = str(text or '').strip()
        if not value:
            return
        # 弱音は褒め言葉より先に見る。「ありがとう、でもつらい」で笑わせない。
        if _COMPARED.search(value):
            face = 'sulky'
        elif _WORRIED.search(value):
            face = 'worried'
        elif _PRAISE.search(value):
            face = 'happy'
        elif _GOODNIGHT.search(value):
            face = 'sleepy'
        else:
            return
        self.value, self.until = face, now + MOOD_HOLD[face]

    def current(self, now: datetime) -> str:
        if self.until and now < self.until:
            return self.value
        self.value, self.until = '', None
        return 'sleepy' if now.hour < NIGHT_UNTIL_HOUR or now.hour >= NIGHT_FROM_HOUR else 'normal'


def think_delay(text: str, mood: str = '') -> float:
    """返事を書き始めるまでの待ち（秒）。0なら待たない。

    即答が続くと機械的に見え、重い相談ほど不自然になる。逆に待たせ過ぎると
    ただ遅いアプリになるので、材料は「話の重さ」「眠さ」「読む量」だけに絞る。

    待つと決めた回は、毎回わずかに違う長さで待つ。0.9秒きっかりが繰り返されると、
    間を置いていること自体が規則として見えてしまうため。
    """
    value = str(text or '')
    delay = 0.0
    if _WORRIED.search(value):
        delay = THINK_DELAY_HEAVY
    elif mood in ('眠そう', 'sleepy'):
        delay = THINK_DELAY_SLEEPY
    # 長文はまず読む時間がかかる。短い雑談は今までどおり即答のまま。
    if len(value) > THINK_DELAY_READ_FROM:
        delay += (len(value) - THINK_DELAY_READ_FROM) / 100 * THINK_DELAY_READ_PER_100
    if not delay:
        return 0.0
    delay *= random.uniform(1 - THINK_DELAY_JITTER, 1 + THINK_DELAY_JITTER)
    return min(delay, THINK_DELAY_MAX)
