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


# --- 会話の導入句（毎回の自己紹介をやめる） --------------------------------
# 毎ターン「今〜してた」「そういえば」で切り出されると、同じ挨拶を繰り返す
# 初対面の相手のように見える。直近の自分の返答を見て、続けて使わない。
RECENT_TURNS = 3
# これだけ間が空いたら会話が途切れたとみなし、もう一度切り出してよい。
INTRO_GAP_MINUTES = 30

_ACTIVITY_INTRO = re.compile(r'(今|いま|さっき|ちょうど|寝てた|読んでた)[^。！？\n]{0,10}?(てた|でた)')
_CALLBACK_INTRO = re.compile(r'(そういえば|そういや|前に(?:言って|話して|聞いて))')
_ASKED_ACTIVITY = re.compile(r'(何|なに)(を)?して(た|る)|どう過ご|暇|起きてる')


def recent_answers(history, turns: int = RECENT_TURNS) -> str:
    """直近数ターンのかぐや側の返答。memory_short の既存取得結果だけを使う。"""
    return ' '.join(str(row.get('answer') or '') for row in (history or [])[-turns:])


def allow_activity_intro(text: str, history) -> bool:
    """「今〜してた」の切り出しを、このターンで使ってよいか。

    相手が「何してた？」と聞いたときは答えないと不自然なので常に許す。
    それ以外は、直近の返答で使っていたら今回は省く（時間が空いた場合の再開は
    living_context 側が判定する）。
    """
    if _ASKED_ACTIVITY.search(str(text or '')):
        return True
    return not _ACTIVITY_INTRO.search(recent_answers(history))


def allow_callback(history) -> bool:
    """「そういえば」「前に話した〜」の振り返りを、このターンで使ってよいか。"""
    return not _CALLBACK_INTRO.search(recent_answers(history))


# --- 前の話への短い振り返り（会話の余韻） ----------------------------------
# 想起できた記憶のうち、しばらく触れていない大事な話題だけを1件。
# 毎ターン振り返るとくどくなるので、導入句と同じ判定で連続を止める。
CALLBACK_MIN_IMPORTANCE = 3
CALLBACK_MIN_DAYS = 7


def _timestamp(value):
    from datetime import datetime
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return value if hasattr(value, 'tzinfo') else None


def callback(recalled: dict, text: str, history, now) -> dict:
    """振り返る話題を最大1件返す。無ければ空の辞書。

    材料は想起済みの memory_long だけで、DBは引き直さない。想起の順位も変えず、
    上位5件の中から「最後に触れたのが古く、重要度が高いもの」を選ぶ。
    いま話している話題と、直近の返答で触れた話題は除く（同じ話を蒸し返さない）。
    """
    if not allow_callback(history):
        return {}
    recent = recent_answers(history)
    said = str(text or '')
    best, oldest = {}, None
    for row in (recalled.get('wisdom') or [])[:5]:
        topic = str(row.get('topic_key') or '')
        seen = _timestamp(row.get('last_seen_at'))
        if not topic or topic in recent or topic in said or not seen:
            continue
        if int(row.get('importance') or 0) < CALLBACK_MIN_IMPORTANCE:
            continue
        if (now - seen).days < CALLBACK_MIN_DAYS:
            continue
        if oldest is None or seen < oldest:
            best, oldest = row, seen
    if not best:
        return {}
    return {'話題': best['topic_key'], '覚えていること': best['summary'],
            '触れ方': '「前に話した◯◯だね」のように、短く一度だけ触れる。'
                    '記録にない日時・発言・出来事は足さない。今の話題を押しのけない。'}


# --- 応答方針（共感・相談・雑談） ------------------------------------------
# 「疲れた」に助言を返す、「どうしたらいい？」に共感だけで終わる、という
# ずれを防ぐ。判定は発言の言葉づかいだけで行い、LLMもDBも使わない。
# 分からないときは雑談（何も足さない側）に倒す。
_NO_ADVICE = re.compile(r'アドバイス(?:は)?いらない|聞いて(?:ほしい|て)|愚痴|吐き出')
# 相談として扱う言い方。「どうしよう」「決められない」のような迷いの独り言は
# ここに入れない（迷っているだけの相手に解決策を並べないため）。
# 一方で「どう思う」「相談したい」「整理して」は、はっきり考えを求めている。
_CONSULT = re.compile(r'どうしたら|どうすれば|どうやって|教えて|どう思う|どっちがいい|どれがいい'
                      r'|相談(?:したい|に乗って|できる|がある)|意見(?:を|が)?(?:ほしい|欲しい|聞かせ|ちょうだい)'
                      r'|何から|整理して|まとめて|おすすめ(?:は|を|が)?'
                      r'|(?:アドバイス|解決策|具体案|対処法|提案|案)(?:を|が|は)?(?:ほしい|欲しい|ください|して|ある|教えて|出して)')
_EMPATHY = re.compile(r'疲れた|つらい|しんど|きつい|悲しい|落ち込|不安|最悪|泣き')

RESPONSE_PLAN = {
    # 「聞いてほしい」と言われた回。ここだけは何も足さない。
    'listen': '聞くことに徹する。助言・解決策・原因の掘り下げは出さない。'
              '相手の言葉をそのまま受け止め、話の続きを促す。',
    'empathy': 'つらさや迷いの具体的な内容を受け止め、一緒に悩む。'
               '受け止めたうえで、相手がすぐ試せる小さな一歩を1つだけ添えてよい（複数案や長い分析は出さない）。'
               '相手が話を続けたそうなら、その話に沿って反応したり一つ聞いてもよい。',
    'consult': '質問にはその内容に答える。気持ちを踏まえたうえで、実行できる具体案を先に伝える。'
               '前置きを長くせず、必要なら手順や選択肢を並べてよい。'
               '答えを押しつけず、迷いが残っていれば一緒に考える。',
    'chat': '相手の話の具体的な部分に反応する。直近のやり取りを踏まえ、関心が続いている話を自然に深める。'
            '助言・解決策は求められたときだけ。質問を無理に足さず、短い返答が合う場面は短く返す。',
}


def intent(text: str) -> str:
    """ユーザー発話の意図。'listen' / 'empathy' / 'consult' / 'chat' のいずれか。"""
    value = str(text or '')
    # 「聞いてほしい」が最優先。ここで相談側へ倒すと、望まれていない助言になる。
    if _NO_ADVICE.search(value):
        return 'listen'
    # 弱音と一緒に聞かれたときは、聞かれている側を採る（相談を共感で流さない）。
    if _CONSULT.search(value):
        return 'consult'
    if _EMPATHY.search(value):
        return 'empathy'
    return 'chat'
