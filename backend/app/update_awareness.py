"""かぐや向けの短い更新メモ。リリース時にID・内容・紹介の一文を一緒に更新する。"""
import logging
import re

UPDATE_ID = '2026-09-14-listening-and-updates'
UPDATE_NOTES = (
    '親身に話を聞き、共感して一緒に悩む方針に調整された。解決策は求められたときだけ提案する。',
    '相手の呼び名は「しょうや」固定になった。',
    'アプリから渡された更新メモをもとに、自分の変更内容を説明できるようになった。',
)
# 紹介を実際に含む返答だけを記録するための一文。次の更新では内容と一緒に変える。
INTRO = '今回、話の聞き方を調整してもらったんだ'
_GREETING = re.compile(r'(?:かぐや[、, 　]*)?(?:おはよう|こんにちは|こんばんは|ただいま|やっほー|おかえり)[！!。〜ー 　]*')
_log = logging.getLogger(__name__)


def context(runtime, text=None):
    """起動で読み込んだ紹介済みIDと比較。Noneは音声通話の開始時。"""
    pending = runtime.data['ledger'].get('introduced_update_id') != UPDATE_ID
    eligible = text is None or bool(_GREETING.fullmatch(text.strip()))
    return {'更新ID': UPDATE_ID, '変更内容': list(UPDATE_NOTES),
            '紹介してよい': pending and eligible and not runtime.options.quiet,
            '紹介の一文': INTRO}


def acknowledge(runtime, answer):
    """会話保存の成功後だけ呼ぶ。補助記録の失敗で保存済み会話を失敗扱いにしない。"""
    if (INTRO not in answer or answer.endswith('（音声の返答は途中で停止しました）')
            or runtime.data['ledger'].get('introduced_update_id') == UPDATE_ID):
        return
    try:
        runtime.record(introduced_update_id=UPDATE_ID)
    except Exception:
        _log.warning('Update introduction could not be recorded')
