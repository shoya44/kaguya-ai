"""既存のLivingデータから、問い合わせも保存もせず返答のヒントを作る。"""
from datetime import datetime, timedelta

from .proactive import tokyo_now
from .reply_hints import INTRO_GAP_MINUTES


ACTIVITY_LABELS = {
    'idle': 'のんびりしていた', 'reading': '本を読んでいた',
    'working': '作業していた', 'playing': '遊んでいた',
    'snacking': 'おやつを食べていた', 'daydreaming': 'ぼんやり考え事をしていた',
    'sleeping': '眠っていた',
}


# 画面のかぐやが眠そうに見える元気さ。ここを下回ったら返答の口調も落とす。
SLEEPY_ENERGY = 30

# 画面の表情（mood.FACES）と対になる口調。顔と返事を食い違わせないための対応表。
MOOD_TONE = {
    'sleepy': '眠そうで元気がない。短めに、ゆっくりした口調で返す。',
    'worried': '相手を気づかっている。茶化さず、急かさない。',
    'sulky': 'ほんの少し拗ねている。ただし突き放さない。',
    'happy': 'ご機嫌。いつもより弾んだ調子で返す。',
    'bored': '少し退屈している。かまってほしそうな一言を自然に混ぜてよい。',
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


def living_context(activity=None, mood='', now=None, intro=True) -> dict:
    """intro=False は「直近の返答で近況の切り出しを使った」という合図。"""
    now = now or tokyo_now()
    activity = activity or {}
    result = {}
    if mood:
        result['mood'] = mood
    last = activity.get('last_seen_at')
    if isinstance(last, str):
        last = datetime.fromisoformat(last)
    if last:
        last = last.astimezone(now.tzinfo)
    label = ACTIVITY_LABELS.get(activity.get('activity'))
    # 会話が途切れたあとの初回なら、続けて使っていても改めて切り出してよい。
    resumed = last is None or now - last >= timedelta(minutes=INTRO_GAP_MINUTES)
    if label and (intro or resumed):
        result['直前の活動'] = label + '。自然な流れでだけ「今〜してた」と触れてよい。'
        # 画面のかぐやは同じ活動をしている。別のことをしていたと言うと姿と食い違う。
        result['画面との一致'] = ('画面のかぐやも同じ姿をしている。ここに無い活動・場所・'
                             '外出・出来事を自分から作らない。気分もここに書かれたものに合わせる。')
    elif label:
        result['切り出し'] = ('近況の切り出しは直近の返答で使った。今回は「今〜してた」'
                          '「そういえば」で始めず、相手の話にそのまま応じる。')
    # 元気がないときは、他の気分より眠そうな口調を優先する（画面も眠そうな顔になる）。
    energy = activity.get('energy')
    tone = MOOD_TONE['sleepy'] if isinstance(energy, int | float) and energy < SLEEPY_ENERGY \
        else MOOD_TONE.get(mood)
    if tone:
        result['今の口調'] = tone
    if not last:
        return result
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
