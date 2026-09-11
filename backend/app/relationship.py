"""Small deterministic relationship state. No LLM calls and no conversation copies."""
import re
from datetime import datetime


_STYLE_WORDS = ('返し', '返事', '言い方', '話し方', '口調', '回答')
_ONE_OFF = ('今回だけ', '今だけ', 'この回答だけ', 'この返事だけ')


def style_feedback(text: str) -> str | None:
    """明示的な話し方フィードバックだけを、誤学習しにくい定型値へ丸める。"""
    value = str(text or '').strip()
    if not value or any(word in value for word in _ONE_OFF):
        return None
    if not any(word in value for word in _STYLE_WORDS):
        return None

    if re.search(r'素っ気|そっけ|冷た|柔らか|やわらか|優しく|親しみ', value):
        return '簡潔でも素っ気なくせず、親しみのある柔らかい一言を自然に添える。'
    if re.search(r'質問.*(多|減ら)|毎回.*質問', value):
        return '質問で終える回数を減らし、必要なときだけ自然に質問する。'
    if re.search(r'(もっと|もう少し).*(詳しく|話して|長く)|詳しめ', value):
        return '普段は簡潔にしつつ、説明が役立つ話題では少し詳しく返す。'
    if re.search(r'(短く|短め|簡潔)', value):
        return '普段は短めに返すが、素っ気なくならないよう親しみのある一言は残す。'
    if re.search(r'(好き|いい感じ|良い感じ|この感じ|その感じ)', value):
        return '今のような、簡潔で親しみのある自然な返し方を基本にする。'
    return None


def _ledger(store) -> dict:
    try:
        value = store.data.get('ledger', {})
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def capture_feedback(store, text: str, now: datetime) -> str | None:
    hint = style_feedback(text)
    if hint:
        try:
            store.record(relationship_style_hint=hint, relationship_style_at=now.isoformat())
        except (OSError, ValueError, TypeError):
            pass
    return hint


def record_success(store, now: datetime) -> None:
    try:
        ledger = _ledger(store)
        chats = max(0, int(ledger.get('relationship_chats', 0) or 0)) + 1
        raw_days = ledger.get('relationship_days', [])
        days = [str(day) for day in raw_days if day] if isinstance(raw_days, list) else []
        today = now.date().isoformat()
        if today not in days:
            days.append(today)
        # 実利用日だけなので肥大化しにくいが、異常データ対策で上限を持つ。
        days = days[-1000:]
        changes = {'relationship_chats': chats, 'relationship_days': days}
        if not ledger.get('relationship_first_seen'):
            changes['relationship_first_seen'] = now.isoformat()
        store.record(**changes)
    except (OSError, ValueError, TypeError):
        # 関係性演出の保存失敗で本体会話を失敗させない。
        return


def context(store) -> dict:
    ledger = _ledger(store)
    try:
        chats = max(0, int(ledger.get('relationship_chats', 0) or 0))
    except (TypeError, ValueError):
        chats = 0
    raw_days = ledger.get('relationship_days', [])
    days = len({str(day) for day in raw_days if day}) if isinstance(raw_days, list) else 0
    if chats < 5:
        familiarity = 'まだ知り合ったばかり'
    elif chats < 30:
        familiarity = '少し慣れてきた'
    elif chats < 100:
        familiarity = 'かなり慣れている'
    else:
        familiarity = '長く話していて気心が知れている'
    result = {'慣れ': familiarity, '利用日数': days}
    hint = str(ledger.get('relationship_style_hint', '') or '').strip()
    if hint:
        result['直近の話し方フィードバック'] = hint
    return result
