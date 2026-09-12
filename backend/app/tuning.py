"""かぐやの反応を決める調整値を1箇所に集める。

ここにあるのは「変えても壊れないが、変えると性格が変わる」数値だけ。
- 秘密情報は .env（app/config.py）
- ユーザーが設定画面で変える値は Options（app/runtime.py）
- 文面・正規表現など、数値でない「中身」は各モジュールのまま

数値の意味が分かるよう、単位と効き方をこのファイルだけで読めるようにしている。
"""
from datetime import timedelta


# --- 表情（Kaguya MindがOFFのとき） ---------------------------------------
# 反応してから、その表情を保つ長さ。切れると時刻だけの判定へ戻る。
MOOD_HOLD = {
    'happy': timedelta(minutes=12),
    'sulky': timedelta(minutes=8),
    'sleepy': timedelta(minutes=30),
}
# 会話が無くても眠そうにする時間帯（この時刻以降と、朝この時刻まで）。
NIGHT_FROM_HOUR = 23
NIGHT_UNTIL_HOUR = 6


# --- 感情（Kaguya MindがON） ----------------------------------------------
# 何もない時に戻っていく基準値。0〜100。
EMOTION_BASELINE = {
    'happiness': 58.0,
    'curiosity': 60.0,
    'boredom': 25.0,
    'affection': 55.0,
    'jealousy': 5.0,
    'concern': 10.0,
}
# 基準値へ戻る速さ（時間）。長いほど尾を引く。affectionが最も長く残る。
EMOTION_HALF_LIFE_HOURS = {
    'happiness': 2.5,
    'curiosity': 5.0,
    'boredom': 1.5,
    'affection': 240.0,
    'jealousy': .75,
    'concern': 1.5,
}
# 言われたことによる増減。閾値に一度で届くかどうかがそのまま反応の速さになる。
EMOTION_REACTION = {
    'idle': {'boredom': -4},
    'praised': {'happiness': +14, 'affection': +1.5},
    'compared': {'jealousy': +32, 'happiness': -2},
    'asked': {'curiosity': +3},
    'worried': {'concern': +12, 'happiness': -4, 'affection': +.5},
    'goodnight': {'boredom': -2},
}
# 気分ラベルと表情を決める境目。ラベルと表情で同じ値を使い、食い違わせない。
EMOTION_THRESHOLD = {
    'concern': 45,
    'jealousy': 35,
    'happiness': 70,
    'curiosity': 72,
    'boredom': 55,
    'energy_sleepy': 35,
}
# 時間帯ごとの元気さ。energy_sleepyを下回る時間帯が「眠そう」になる。
ENERGY_BY_HOUR = ((6, 24.0), (10, 58.0), (18, 78.0), (23, 64.0), (24, 30.0))


# --- 好み ------------------------------------------------------------------
TRAIT_VALENCE = {'like': .78, 'dislike': .22}
TRAIT_CONFIDENCE_START = .35        # 初めて言ったときの確信度
TRAIT_CONFIDENCE_UP = .12           # 同じ向きを繰り返したとき
TRAIT_CONFIDENCE_DOWN = .08         # 逆のことを言ったとき
TRAIT_CONFIDENCE_RANGE = (.20, .95)
TRAIT_EVIDENCE_WEIGHT_MAX = 5       # 平均を取るときに見る過去の件数
TRAIT_SETTLED = .5                  # これ未満は「定着していない」
TRAIT_FORGET_DAYS = 30              # 定着しなかった好みを忘れるまで
# 表示ラベルの境目。好き寄り / 苦手寄り / まだ曖昧。
TRAIT_STANCE = {'like': .62, 'dislike': .38}
TRAIT_STABILITY = {'settled': .75, 'forming': .5}


# --- 気にかけていること（未完の話題） --------------------------------------
LOOP_DUE_HOUR = 21                  # 予定の日のこの時刻を過ぎてから話題にする
LOOP_TODAY_AFTER = timedelta(hours=5)     # 「今日」の予定はこれだけ経ってから
LOOP_CONCERN_AFTER = timedelta(hours=14)  # 体調の話はこれだけ経ってから
LOOP_MAX_ASKS = 2                   # 返事がなくても諦める回数
LOOP_QUIET = timedelta(hours=12)    # 一度触れてから次に触れるまで
LOOP_KEEP_RESOLVED_DAYS = 14        # 片付いた話題を覚えている日数
LOOP_FORGET_DAYS = 30               # 動きのない話題を忘れるまで


# --- よく使う言い方 --------------------------------------------------------
PHRASE_CANDIDATE_COUNT = 3          # 何回繰り返したら候補にするか
PHRASE_LENGTH = (2, 32)             # 候補として記録する文字数の範囲


# --- 育ち具合の表示 --------------------------------------------------------
GROWTH_GROWN = {'traits': 8, 'interactions': 100}
GROWTH_GROWING = {'traits': 3, 'interactions': 30}


# --- 音声通話 --------------------------------------------------------------
# 聞き取りと読み上げの言語。指定しないと発話ごとに自動判別され、
# 日本語が別の言語（韓国語など）として扱われることがあるため固定する。
# 声の名前は聞き比べて選ぶものなので、設定画面から変える（Options.voice_name）。
VOICE_LANGUAGE = 'ja-JP'


# --- 配信 ------------------------------------------------------------------
FACE_REFRESH_TICKS = 12             # 5秒ごとのtick何回ごとにMindの気分を読み直すか
