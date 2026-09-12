"""二人の距離。会った量は living_activity、話し方の希望は persona_character が持つ。

以前は app_settings の ledger（スケジューラの記録）へ混ぜて置いていた。
記憶に当たるものはそれぞれの持ち主のテーブルへ移し、ledger は整理回数と
声かけの記録だけに戻している。LLMは呼ばず、会話のコピーも残さない。
"""
import re
from datetime import datetime

from .tuning import FAMILIARITY_STEPS

_STYLE_WORDS = ('返し', '返事', '言い方', '話し方', '口調', '回答')
_ONE_OFF = ('今回だけ', '今だけ', 'この回答だけ', 'この返事だけ')

STYLE_KEY = 'style_feedback'


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
    if re.search(r'(嫌い|苦手|やめて)', value):
        return '同じ定型的な言い回しを繰り返さず、簡潔でも自然で親しみのある返し方にする。'
    if re.search(r'(好き|いい感じ|良い感じ|この感じ|その感じ)', value):
        return '今のような、簡潔で親しみのある自然な返し方を基本にする。'
    return None




def familiarity(chats: int) -> str:
    for limit, label in FAMILIARITY_STEPS:
        if chats < limit:
            return label
    return FAMILIARITY_STEPS[-1][1]


def context(living) -> dict:
    """プロンプトに渡す関係性。会った量だけ。

    話し方フィードバックは persona_character の1行なので、recall() が返す
    「接し方」にそのまま含まれる。ここで二重に渡さない。
    """
    counts = living.familiarity()
    return {'慣れ': familiarity(counts['chats']), '利用日数': counts['days']}
