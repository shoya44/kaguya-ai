"""VOICEVOX互換エンジン（AivisSpeech / VOICEVOX）で読み上げる。

PC上で動くエンジンへHTTPで頼むだけ。話者は名前で指定し、IDは起動時に引く。
IDを直接書かないのは、エンジンや配布バージョンで番号が変わりうるため。

出力は 24kHz・モノラル・16bit のPCM。Gemini Liveの音声と同じ形なので、
画面側の再生経路をそのまま使える。
"""
from __future__ import annotations

import asyncio
import io
import re
import wave
from contextlib import suppress

import httpx

# 画面側が再生する形。audio_query でこの形を指定してから合成する。
SAMPLE_RATE = 24000
# 1回に読み上げる長さの上限。長いと合成が返るまで黙る時間が延びる。
MAX_TEXT = 200
# ここで区切って読み上げる。全部そろうまで待つと、最初の一声までが遅い。
SENTENCE_END = '。！？!?\n'
# 句点が来ないまま伸びた場合に、読点で妥協して区切る長さ。
SOFT_BREAK = 40
# 返答の一言目だけは短く区切る。ここが「話しかけてから返ってくるまで」の体感を決める。
# 2文目以降まで短くすると細切れに聞こえるので、最初だけ。
FIRST_BREAK = 10


class SpeechError(RuntimeError):
    """エンジンに繋がらない・話者が見つからない。通話は続け、案内だけ出す。"""


def sentences(buffer: str, soft_break: int = SOFT_BREAK) -> tuple[list[str], str]:
    """読み上げてよい分と、まだ手元に残す分に分ける。"""
    ready, rest = [], ''
    for piece in re.split(f'(?<=[{re.escape(SENTENCE_END)}])', buffer):
        if not piece:
            continue
        if piece[-1] in SENTENCE_END:
            ready.append(piece)
        else:
            rest = piece
    if not ready and len(rest) >= soft_break and '、' in rest:
        # 最初の読点で切る。後ろの読点まで待つと、そのぶん喋り出しが遅れる。
        head, _, rest = rest.partition('、')
        ready.append(head + '、')
    # 句読点がまったく来ない場合でも、say() で切り捨てずに済むよう必ず区切る。
    while len(rest) >= MAX_TEXT:
        ready.append(rest[:MAX_TEXT])
        rest = rest[MAX_TEXT:]
    return [text for text in (t.strip() for t in ready) if text], rest


def pcm(wav: bytes) -> bytes:
    """WAVからそのまま再生できる生のPCMを取り出す。"""
    with wave.open(io.BytesIO(wav)) as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, SAMPLE_RATE):
            raise SpeechError('読み上げエンジンの音声形式が想定と違います。')
        return stream.readframes(stream.getnframes())


class Speech:
    def __init__(self, client: httpx.AsyncClient, url: str, speaker: str, style: str):
        self.client = client
        self.url = url.rstrip('/')
        self.speaker = speaker
        self.style = style
        self._id: int | None = None

    async def speaker_id(self) -> int:
        """名前とスタイルからIDを引く。通話中は変わらないので1度だけ。"""
        if self._id is not None:
            return self._id
        try:
            response = await self.client.get(self.url + '/speakers', timeout=10)
            response.raise_for_status()
            rows = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SpeechError(f'読み上げエンジン（{self.url}）に接続できません。起動しているか確認してください。') from exc
        for row in rows if isinstance(rows, list) else []:
            if row.get('name') != self.speaker:
                continue
            for style in row.get('styles') or []:
                if style.get('name') == self.style:
                    self._id = int(style['id'])
                    return self._id
        found = '/ '.join(str(row.get('name')) for row in rows[:20]) if isinstance(rows, list) else ''
        raise SpeechError(f'話者「{self.speaker}（{self.style}）」が見つかりません。'
                          + (f'このエンジンにあるのは: {found}' if found else ''))

    async def say(self, text: str) -> bytes:
        text = text.strip()[:MAX_TEXT]
        if not text:
            return b''
        speaker = await self.speaker_id()
        try:
            query = await self.client.post(self.url + '/audio_query',
                                           params={'speaker': speaker, 'text': text}, timeout=30)
            query.raise_for_status()
            body = query.json()
            # 画面側が扱える形に揃える。エンジン既定は44.1kHzのこともある。
            body['outputSamplingRate'] = SAMPLE_RATE
            body['outputStereo'] = False
            audio = await self.client.post(self.url + '/synthesis',
                                           params={'speaker': speaker}, json=body, timeout=60)
            audio.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            raise SpeechError('読み上げに失敗しました。エンジンの状態を確認してください。') from exc
        return pcm(audio.content)


class Narrator:
    """文がそろった分から順に読み上げて流す。

    合成には数百ミリ秒かかる。その間も受信を続けられるよう、合成と送信は
    別のタスクへ回す。割り込まれたら、まだ読んでいない分は捨てる。
    """

    def __init__(self, speech: Speech, send):
        self.speech = speech
        self.send = send
        self.buffer = ''
        self.queue: asyncio.Queue = asyncio.Queue()
        self.spoken = 0
        self.generation = 0
        self.task: asyncio.Task | None = None
        self.error = ''

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            generation, text = await self.queue.get()
            try:
                if generation != self.generation:
                    continue
                audio = await self.speech.say(text)
                if audio and generation == self.generation:
                    await self.send(audio)
            except SpeechError as exc:
                # 読み上げが止まっても会話は続ける。理由は画面へ1度だけ出す。
                self.error = str(exc)
            finally:
                self.queue.task_done()

    def feed(self, text: str) -> None:
        self.buffer += text
        # 一言目だけ短く区切り、返事が始まるまでの間を詰める。
        ready, self.buffer = sentences(self.buffer, FIRST_BREAK if not self.spoken else SOFT_BREAK)
        for item in ready:
            self.spoken += 1
            self.queue.put_nowait((self.generation, item))

    def flush(self) -> None:
        """返答が終わったら、句点が来ていない残りも読み上げる。"""
        text, self.buffer = self.buffer.strip(), ''
        if text:
            self.queue.put_nowait((self.generation, text))
        self.spoken = 0

    def stop(self) -> None:
        """割り込まれた。まだ読んでいない分は捨てる。"""
        self.generation += 1
        self.buffer = ''
        self.spoken = 0
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
