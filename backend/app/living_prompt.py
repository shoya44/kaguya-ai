"""既存のLivingデータから、問い合わせも保存もせず返答のヒントを作る。"""
from datetime import datetime, timedelta

from .proactive import tokyo_now


ACTIVITY_LABELS = {
    'idle': 'のんびりしていた', 'reading': '本を読んでいた',
    'working': '作業していた', 'playing': '遊んでいた',
    'snacking': 'おやつを食べていた', 'daydreaming': 'ぼんやり考え事をしていた',
    'sleeping': '眠っていた',
}


def derive_mood(emotions: dict, activity: dict) -> str:
    if not emotions and activity.get('energy') is None:
        return ''
    if activity.get('energy', 100) < 30:
        return 'sleepy'
    if emotions.get('concern', 0) > 60:
        return 'worried'
    if emotions.get('jealousy', 0) > 60:
        return 'sulky'
    if emotions.get('happiness', 0) > 60:
        return 'happy'
    return 'calm'


def time_hint(now: datetime) -> str:
    if 5 <= now.hour < 11:
        return '朝は少し眠そうに、やわらかい口調で。'
    if now.hour >= 23 or now.hour < 5:
        return '深夜は静かに、短めで落ち着いた口調で。'
    if now.hour >= 18:
        return '夜はゆったりと、くつろいだ口調で。'
    return '昼は自然で明るい口調で。'


def living_context(activity=None, mood='', now=None) -> dict:
    now = now or tokyo_now()
    activity = activity or {}
    result = {}
    if mood:
        result['mood'] = mood
    label = ACTIVITY_LABELS.get(activity.get('activity'))
    if label:
        result['直前の活動'] = label + '。自然な流れでだけ「今〜してた」と触れてよい。'
    last = activity.get('last_seen_at')
    if not last:
        return result
    if isinstance(last, str):
        last = datetime.fromisoformat(last)
    last = last.astimezone(now.tzinfo)
    elapsed = now - last
    if elapsed >= timedelta(days=7):
        result['再会'] = '1週間以上ぶり。会えてうれしい気持ちを短く伝えてよい。留守を責めない。'
    elif elapsed >= timedelta(days=3):
        result['再会'] = '3日以上ぶり。久しぶりだね、とやさしく迎えてよい。'
    elif elapsed >= timedelta(days=1):
        result['再会'] = '24時間以上ぶり。また話せてうれしい気持ちを軽く添えてよい。'
    if last.date() != now.date() and last < now:
        result['今日の初回'] = ('朝の挨拶「おはよう」を自然に添える。' if 5 <= now.hour < 11
                            else '今日初めての会話。今の時間帯に合う挨拶を自然に添える。')
    return result
