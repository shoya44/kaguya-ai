"""既存の会話材料だけから作る、小さな返答ヒント。

ここでは追加のDB問い合わせもLLM呼び出しも行わない。渡されるのは、
そのターンですでに引いてある recall 結果・直近の会話・ユーザー発言だけ。
"""
import re


# --- 気がかりの状態判定（Memory：memory_concern） ---------------------------
# 「まだ不安」と「解決した」を取り違えると、触れてほしい話題を打ち切ったり、
# 済んだ話題を蒸し返したりする。話題に触れただけでは解決にしない。
# どちらとも取れる言い方は未解決側（打ち切らない側）へ倒す。
_STILL = re.compile(r'(まだ|これから|終わってな|済んでな|治ってな|続いて|変わらず|不安|心配|怖い)')
_DONE = re.compile(r'(終わった|終わりました|終わったよ|済んだ|済みました|治った|よくなった'
                   r'|解決した|片付いた|中止|無事に|受かった|合格|退院)')


def concern_status(topic: str, said: str) -> str:
    """ユーザー発言から、その気がかりの状態を返す。

    'resolved' … もう触れない  / 'still' … まだ不安なので諦めない
    ''         … 判断がつかない（従来どおり、間隔を空けてまた触れてよい）
    判定はその話題が出てきた一文の中だけを見る。1回の発言で複数の話題に触れると、
    別の話題の「まだ」「終わった」を取り違えるため。
    「まだ」と「終わった」が同じ文にあれば「まだ」を採る（例：まだ終わってない）。
    """
    sentence = _sentence_with(topic, str(said or ''))
    if not sentence:
        return ''
    if _STILL.search(sentence):
        return 'still'
    if _DONE.search(sentence):
        return 'resolved'
    return ''


def _sentence_with(topic: str, text: str) -> str:
    if not topic:
        return ''
    return next((part for part in re.split(r'[。．！？!?\n]', text) if topic in part), '')
