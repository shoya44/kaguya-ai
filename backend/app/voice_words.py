"""音声の用語辞書。音声モデルへのヒントと、表示・合成直前の小さな補正。"""
import re

READINGS = {'朝会': 'あさかい'}


def guidance():
    readings = '、'.join(f'「{word}」は「{reading}」' for word, reading in READINGS.items())
    return ('\n音声の用語：あなたの名前は「かぐや」（か・ぐ・や）。相手があなたに呼びかける'
            '「かぐや」を、店の「家具屋」と取り違えない。家具を売る店の話なら「家具屋」のまま扱う。'
            f'読み上げ辞書：{readings}と読む。ここでの朝会は「ちょうかい」と読まない。'
            '相手の名前「しょうや」もそのまま読む。辞書の説明自体は求められない限り話さない。')


def for_speech(text):
    for word, reading in READINGS.items():
        text = text.replace(word, reading)
    return text


def transcript_text(text):
    # 累積した原文に毎回適用する。断片ごとの置換では「家具」+「屋」を直せない。
    # 店の説明・検索依頼まで改変しないよう、明確な呼びかけだけに絞る。
    text = re.sub(r'^(\s*(?:ねえ|ねぇ|おーい)[、, 　]*)家具屋(?=[、,！？!?。 　]|$)',
                  r'\1かぐや', text)
    return re.sub(r'^\s*家具屋(?=[、, 　]*(?:(?:おはよう|こんにちは|こんばんは|聞いて|何してる|いる)[。!！?？ 　]*)?[。!！?？ 　]*$)',
                  'かぐや', text)
