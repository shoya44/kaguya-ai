"""会話の中からかぐやが使える道具。宣言と実行をここにまとめる。

通常会話の入力トークンを増やさないため、発言に関係する道具だけをGeminiへ渡す。
天気のようにローカルで意図を確定できるものはGeminiを経由せず直接実行する。
単純な道具は結果をローカルで短く文章化し、2回目のGemini呼び出しも省く。
"""
import re
from datetime import datetime, timedelta

from . import project_inspector, quick_tools
from .proactive import JST, tokyo_now

MAX_AHEAD = timedelta(days=366)
MAX_BEHIND = timedelta(minutes=1)

CORE_DECLARATIONS = [
    {
        'name': 'set_reminder',
        'description': 'ユーザーが指定した日時に声をかける予約をする。時刻と伝える内容が読み取れるときだけ使う。',
        'parameters': {'type': 'OBJECT', 'properties': {
            'at': {'type': 'STRING', 'description': '日本時間のISO 8601予定時刻。'},
            'message': {'type': 'STRING', 'description': 'その時刻に伝える内容。'},
        }, 'required': ['at', 'message']},
    },
    {
        'name': 'remember',
        'description': '「覚えておいて」と明示的に頼まれた事実を長期記憶へ保存する。',
        'parameters': {'type': 'OBJECT', 'properties': {
            'topic': {'type': 'STRING'}, 'fact': {'type': 'STRING'},
        }, 'required': ['topic', 'fact']},
    },
]

DECLARATIONS = CORE_DECLARATIONS + project_inspector.DECLARATIONS + quick_tools.DECLARATIONS
_DECLARATION_BY_NAME = {item['name']: item for item in DECLARATIONS}
_QUICK_NAMES = {item['name'] for item in quick_tools.DECLARATIONS}
_PROJECT_NAMES = {item['name'] for item in project_inspector.DECLARATIONS}


def declarations_for(text: str) -> list[dict]:
    value = str(text or '')
    lower = value.lower()
    names: set[str] = set()
    clock_hint = bool(re.search(r'\d{1,2}\s*(?:時|:)|\d+\s*分後', value))
    date_hint = any(word in value for word in ('今日', '明日', '明後日', '来週', '今週', 'あとで', '後で'))
    time_hint = clock_hint or date_hint

    if any(word in value for word in ('覚えて', '覚えといて', '記憶して')):
        names.add('remember')
    if any(word in value for word in ('リマインド', '知らせて', '声かけて', '言って', '教えて')) and time_hint:
        names.add('set_reminder')
    # 明示的な天気質問はdirect_reply()で先に処理する。ここは「暑い？」「寒い？」等、
    # 雑談か天気照会かモデル判断が必要な曖昧ケースだけのフォールバック。
    if any(word in value for word in ('暑い？', '暑い?', '寒い？', '寒い?')):
        names.add('weather')
    settings_hint = any(word in value for word in (
        '設定', '静かに', '文字サイズ', 'フォント', '最前面', '天気の場所',
        '声かけ間隔', '声かけの間隔', '声かけを停止', '声かけ停止', '声かけを再開'))
    if settings_hint:
        names.add('app_settings')
    schedule_hint = any(word in value for word in ('予定', 'カレンダー', 'スケジュール', '会議', '予定に入れ', '予定入れ'))
    if schedule_hint or (date_hint and any(word in value for word in ('何ある', '何かある'))):
        names.add('calendar')
    if any(word in lower for word in ('references', 'reference')) or any(word in value for word in ('参照資料', '参照ファイル', '手順書', '資料から', 'ファイルから', 'メモから')):
        names.add('reference_search')
    if any(word in lower for word in ('readme', 'ソース', 'コード', 'project_inspector')) or any(word in value for word in ('自分の仕様', 'かぐやの仕様', '実装', 'バグ原因')):
        names.update(_PROJECT_NAMES)
    return [_DECLARATION_BY_NAME[name] for name in _DECLARATION_BY_NAME if name in names]


def _weather_location(text: str) -> str:
    value = str(text or '').strip()
    # 「足立区の天気」「東京の明日の天気」のような明示地点だけ拾う。
    match = re.search(r'([一-龯ぁ-んァ-ヶーA-Za-z0-9・\- ]{1,30})の(?:今日の|明日の)?(?:天気|気温|予報)', value)
    if not match:
        return ''
    candidate = match.group(1).strip()
    if candidate in {'今日', '明日', '明後日', '今', '現在', 'こっち', 'ここ'}:
        return ''
    return candidate


async def direct_reply(text: str, memory) -> str | None:
    """Geminiを呼ばずに確定できる軽量リクエストを処理する。"""
    value = str(text or '')
    weather_request = (
        any(word in value for word in ('天気', '気温', '予報', '傘'))
        or any(word in value for word in ('雨降る', '雨降り', '雪降る', '雪降り'))
    )
    if not weather_request:
        return None
    outcome = await quick_tools.run('weather', {'location': _weather_location(value)}, memory)
    return fast_reply('weather', outcome, value)


def parse_due(value, now=None):
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


def _when(value):
    try:
        stamp = datetime.fromisoformat(str(value))
        return f'{stamp.month}/{stamp.day} {stamp:%H:%M}'
    except (TypeError, ValueError):
        return str(value or '')


def fast_reply(name: str, outcome: dict, user_text: str = '') -> str | None:
    """考察不要なtool結果だけローカルで短く返す。NoneならGeminiに文章化させる。"""
    if not isinstance(outcome, dict):
        return None
    if outcome.get('ok') is False:
        return f"うまくできなかった。{outcome.get('error', 'もう一度確認してみて。')}"

    if name == 'weather' and outcome.get('現在'):
        stale = '（直近の取得結果）' if outcome.get('キャッシュ利用') else ''
        if '明日' in user_text and isinstance(outcome.get('明日'), dict):
            day = outcome['明日']
            return (f"明日は{day.get('天気', '不明')}。最高{day.get('最高気温C', '?')}℃、"
                    f"最低{day.get('最低気温C', '?')}℃、降水確率は{day.get('降水確率%', '?')}%だよ。{stale}")
        current = outcome['現在']
        today = outcome.get('今日') or {}
        rain = today.get('降水確率%')
        base = (f"{outcome.get('場所', '')}はいま{current.get('天気', '不明')}、"
                f"{current.get('気温C', '?')}℃くらい。")
        if '傘' in user_text and isinstance(rain, (int, float)):
            base += (f"降水確率{rain}%だから、傘は持ってった方がよさそう。" if rain >= 40
                     else f"降水確率{rain}%だから、傘はたぶん大丈夫そう。")
        elif rain is not None:
            base += f"今日の降水確率は最大{rain}%だよ。"
        return base + stale

    if name == 'set_reminder':
        return f"{outcome.get('予約時刻', '')}に「{outcome.get('内容', '')}」って声かけるね。"
    if name == 'remember':
        return f"うん、「{outcome.get('内容', '')}」って覚えておく。"

    if name == 'app_settings':
        if 'name' in outcome:
            labels = {'quiet': '静音', 'proactive_minutes': '声かけ間隔', 'always_on_top': '最前面',
                      'font_size': '文字サイズ', 'weather_location': '天気の場所'}
            label = labels.get(str(outcome['name']), str(outcome['name']))
            return f"{label}を「{outcome.get('value')}」に変えたよ。"
        settings = outcome.get('settings')
        if isinstance(settings, dict):
            shown = '、'.join(f'{key}={value}' for key, value in list(settings.items())[:5])
            return f"今の設定は {shown} だよ。"

    if name == 'calendar':
        event = outcome.get('event')
        if isinstance(event, dict):
            return f"{_when(event.get('start'))}に「{event.get('title', '予定')}」を入れたよ。"
        if outcome.get('removed') is True and isinstance(outcome.get('item'), dict):
            return f"「{outcome['item'].get('title', '予定')}」は消したよ。"
        if outcome.get('removed') is False and outcome.get('error'):
            return str(outcome['error'])
        items = outcome.get('items')
        if isinstance(items, list):
            if not items:
                return 'その期間の予定はないよ。'
            rows = [f"{_when(item.get('start'))} {item.get('title', '予定')}" for item in items[:5] if isinstance(item, dict)]
            suffix = ' ほかにもあるよ。' if len(items) > 5 else ''
            return '予定は、' + '／'.join(rows) + '。' + suffix

    # referencesやソース調査は取得結果を読んで回答する必要があるので2回目のLLMへ渡す。
    return None


async def run(name, args, memory, now=None):
    args = args or {}
    try:
        if name in _PROJECT_NAMES:
            return project_inspector.run(name, args)
        if name in _QUICK_NAMES:
            return await quick_tools.run(name, args, memory)
        if name == 'set_reminder':
            message = str(args.get('message', '')).strip()[:500]
            if not message:
                return {'ok': False, 'error': '伝える内容が空でした。'}
            due = parse_due(args.get('at'), now)
            await memory.call('POST', '/reminders', json={'due_at': due.isoformat(), 'message': message})
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
        return {'ok': False, 'error': '操作を完了できませんでした。'}
    return {'ok': False, 'error': '未対応の操作です。'}
