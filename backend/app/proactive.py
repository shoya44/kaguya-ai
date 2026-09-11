import random
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# 定型文のまま（LLMは呼ばない）だが、時間帯ごとに数パターン持たせる。
# 起動時の挨拶と、間隔が空いたときの一声を分ける。
GREETINGS = {
    'morning': ['おはよ。今日も来てくれたんだ。', 'おはよう。あたし、ちゃんと起きてたよ。'],
    'day': ['おかえり。あたし、ここにいるよ。', 'やっほ。ちょっと退屈してたところ。'],
    'evening': ['おかえり。今日はどんな日だった？', 'おかえり。あたし、待ってたんだからね。'],
    'night': ['こんばんは。まだ起きてたんだ。', 'おかえり、夜更かしさん。'],
}
NUDGES = {
    'morning': ['ねえ、今日の予定はもう決めた？', 'ちょっとだけ話さない？'],
    'day': ['ねえ、ちょっと休憩しない？', '根を詰めすぎてない？'],
    'evening': ['そろそろ一息つかない？', '今日はもう十分がんばったんじゃない？'],
    'night': ['あんまり夜更かししないでよ。', 'そろそろ休んだら？あたしは付き合うけどさ。'],
}
# 前に聞いた話題があるときだけ使う。定型文より「覚えていた」ことが伝わる。
FOLLOW_UPS = {
    'greeting': ['おかえり。そういえば{topic}、どうだった？', 'おかえり。{topic}のこと、聞いてもいい？'],
    'nudge': ['ねえ、{topic}ってその後どう？', 'そういえば{topic}、どうなった？'],
}


def time_slot(now):
    hour = now.hour
    if 5 <= hour < 11:
        return 'morning'
    if 11 <= hour < 17:
        return 'day'
    if 17 <= hour < 23:
        return 'evening'
    return 'night'


def tokyo_now():
    return datetime.now(JST)


class Proactive:
    """Pure clock-driven policy. The controller owns the one periodic task."""
    def __init__(self, store, now=None):
        self.store = store
        self.last_activity = now or tokyo_now()
        self.started = self.last_activity
        saved = store.data['ledger'].get('last_proactive')
        self.last_sent = datetime.fromisoformat(saved) if saved else None
        self.awaiting = bool(store.data['ledger'].get('proactive_awaiting', False))
        self.greeting_pending = True
        self.last_message = None

    def activity(self, now=None):
        self.last_activity = now or tokyo_now()
        self.awaiting = False
        self.greeting_pending = False
        self.store.record(proactive_awaiting=False)
        message, self.last_message = self.last_message, None
        return message

    def reset(self, now=None):
        self.last_activity = now or tokyo_now()
        self.awaiting = False
        self.greeting_pending = False
        self.last_message = None
        self.store.record(proactive_awaiting=False)

    def tick(self, visible: bool, busy: bool, now=None, topic=None):
        """topicは「声かけを出すと決まったとき」だけ呼ぶ引き当て関数。
        送らない回で未完の話題を消費しないよう、判定を全部通ってから呼ぶ。"""
        now = now or tokyo_now()
        options = self.store.options
        if options.quiet or self.awaiting or busy or not visible:
            # A startup greeting is not deferred until a much later re-show.
            if now - self.started > timedelta(minutes=1):
                self.greeting_pending = False
            return None
        interval = timedelta(minutes=options.proactive_minutes)
        if self.last_sent and now - self.last_sent < interval:
            return None
        if not self.greeting_pending and now - self.last_activity < interval:
            return None
        subject = str((topic() if topic else '') or '').strip()
        if subject:
            text = random.choice(FOLLOW_UPS['greeting' if self.greeting_pending else 'nudge']).format(topic=subject)
        else:
            text = random.choice((GREETINGS if self.greeting_pending else NUDGES)[time_slot(now)])
        self.greeting_pending = False
        self.last_sent = now
        self.awaiting = True
        self.last_message = text
        self.store.record(last_proactive=now.isoformat(), proactive_awaiting=True)
        return {'type': 'proactive.message', 'text': text}
