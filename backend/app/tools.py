"""会話の中からかぐやが使える道具。宣言と実行をここにまとめる。

ツールが呼ばれた回だけGeminiへの往復が1回増えるため、道具は最小限に絞る。
実行結果はモデルへ文章で返し、最終的な言い回しはモデルに任せる。
"""
from datetime import datetime, timedelta

from . import project_inspector
from .proactive import JST, tokyo_now

# 遠すぎる予約は誤解釈（年の取り違えなど）の可能性が高いので受け付けない。
MAX_AHEAD = timedelta(days=366)
# 「1分前」程度のずれは許容し、明らかな過去だけを弾く。
MAX_BEHIND = timedelta(minutes=1)

DECLARATIONS = [
    {
        'name': 'set_reminder',
        'description': 'ユーザーが指定した日時に声をかける予約をする。'
                       '「明日の朝9時に〜と言って」「30分後に教えて」のように、'
                       '時刻と伝える内容が読み取れるときだけ使う。',
        'parameters': {
            'type': 'OBJECT',
            'properties': {
                'at': {'type': 'STRING',
                       'description': '日本時間の予定時刻。ISO 8601形式（例: 2026-09-12T09:00:00+09:00）。'
                                      '現在日時を基準に、相対的な言い方も絶対時刻へ直して渡す。'},
                'message': {'type': 'STRING', 'description': 'その時刻に伝える内容。かぐやの言葉で短く。'},
            },
            'required': ['at', 'message'],
        },
    },
    {
        'name': 'remember',
        'description': 'ユーザーが「覚えておいて」と明示的に頼んだ事実を、その場で長期記憶へ保存する。'
                       '日々の自動整理を待たずに覚えたいときだけ使う。雑談の内容を勝手に保存しない。',
        'parameters': {
            'type': 'OBJECT',
            'properties': {
                'topic': {'type': 'STRING', 'description': '話題を表す短い日本語の名詞句（例: 好きな飲み物）。'},
                'fact': {'type': 'STRING', 'description': '覚えておく内容を1文で。'},
            },
            'required': ['topic', 'fact'],
        },
    },
] + project_inspector.DECLARATIONS


def parse_due(value, now=None):
    """予約時刻を検証してJSTのdatetimeにする。不正なら理由を添えてValueError。"""
    now = now or tokyo_now()
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError('日時を解釈できませんでした。') from None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=JST)
    if stamp < now - MAX_BEHIND:
        raise ValueError('過ぎた時刻は予約できません。')
    if stamp > now + MAX_AHEAD:
        raise ValueError('1年より先は予約できません。')
    return stamp


async def run(name, args, memory, now=None):
    """1つの道具を実行し、モデルへ返す結果を組み立てる。例外は投げない。"""
    args = args or {}
    try:
        if name in {'project_status', 'project_search', 'project_read'}:
            return project_inspector.run(name, args)
        if name == 'set_reminder':
            message = str(args.get('message', '')).strip()[:500]
            if not message:
                return {'ok': False, 'error': '伝える内容が空でした。'}
            due = parse_due(args.get('at'), now)
            await memory.call('POST', '/reminders',
                              json={'due_at': due.isoformat(), 'message': message})
            return {'ok': True, '予約時刻': f'{due:%Y-%m-%d %H:%M}', '内容': message}
        if name == 'remember':
            topic = str(args.get('topic', '')).strip()[:80]
            fact = str(args.get('fact', '')).strip()[:400]
            if not topic or not fact:
                return {'ok': False, 'error': '話題と内容の両方が必要です。'}
            await memory.call('POST', '/remember', json={'topic': topic, 'fact': fact})
            return {'ok': True, '話題': topic, '内容': fact}
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    except Exception:
        # 保存・読み取りに失敗しても会話は続ける。モデルには失敗だけ伝える。
        return {'ok': False, 'error': '操作を完了できませんでした。'}
    return {'ok': False, 'error': '未対応の操作です。'}
