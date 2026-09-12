"""Bounded Gemini Live sessions. Audio stays transient; transcripts use existing memory."""
import asyncio
import base64
import json
import logging
import re
import time
from contextlib import suppress
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

import httpx

from . import relationship, tts
from .persona import memory_prompt
from .proactive import tokyo_now
from .tuning import VOICE_LANGUAGE

_log = logging.getLogger(__name__)

# 例外文にはURLごと入ることがあり、Live APIのURLにはAPIキーが載る。画面へ出す前に消す。
_SECRET = re.compile(r'(?i)\b(key|token|authorization)=[^&\s\'"]+')


def live_config(prompt: str, voice_name: str) -> dict:
    """Liveセッションの設定。読み上げをPC側へ回す場合も中身は同じ。

    ネイティブ音声のLiveモデルは音声出力専用で、テキストだけを返す設定は拒否される
    （1007 The requested combination of response modalities (TEXT) is not supported）。
    PC側で読み上げるときも音声のまま受け取り、output_audio_transcription の
    書き起こしを読み上げに回す。Gemini側の音声は再生しない。生成ぶんは無駄になるが、
    モデルを差し替えずに済む。
    """
    return {'response_modalities': ['AUDIO'], 'system_instruction': prompt,
            'input_audio_transcription': {}, 'output_audio_transcription': {},
            'speech_config': {'language_code': VOICE_LANGUAGE,
                              'voice_config': {'prebuilt_voice_config': {'voice_name': voice_name}}}}


def _reason(exc: BaseException) -> str:
    """何が起きたかを画面で分かる形にする。種類だけでも原因の切り分けに足りる。"""
    detail = _SECRET.sub(r'\1=***', str(exc)).replace('\n', ' ').strip()[:300]
    return type(exc).__name__ + (f': {detail}' if detail else '')


class Transcript:
    def __init__(self, controller, client_id):
        self.controller = controller
        self.client_id = client_id
        self.text = ''
        self.answer = ''

    async def save(self, interrupted=False):
        text, answer = self.text.strip(), self.answer.strip()
        self.text = self.answer = ''
        if not text:
            return
        if len(text) > 2000 or len(answer) > 29500:
            raise ValueError('音声の発話が長すぎて保存できません。短い発話に分けてください。')
        turn = {'turn_id': str(uuid4()), 'client_id': str(self.client_id), 'text': text, 'input_mode': 'voice', 'retry': False}
        c = self.controller
        await c.memory.begin(turn)
        if not answer:
            await c.memory.fail(turn['turn_id'], 'cancelled')
            return
        if interrupted:
            answer += '\n（音声の返答は途中で停止しました）'
        c.unsaved = (turn, answer)
        try:
            await c.memory.complete(turn['turn_id'], answer)
        except Exception:
            await c.broadcast(c.unsaved_event())
            raise
        c.unsaved = None
        relationship.record_success(c.runtime, tokyo_now())
        if c.mind and not interrupted:
            c.mind.after_reply(text, answer, tokyo_now())
        await c.broadcast({'type': 'chat.completed', 'turn_id': turn['turn_id'], 'text': text, 'answer': answer})


async def handle(ws: WebSocket):
    await ws.accept()
    controller = ws.app.state.controller
    settings = ws.app.state.settings
    client = None
    transcript = None
    owned = False
    tasks = set()
    speech = speech_client = narrator = None
    try:
        # Keep session tokens out of WebSocket URLs / access logs.
        hello = await asyncio.wait_for(ws.receive_json(), 10)
        client_id = ws.app.state.sessions.get(hello.get('token', ''))
        origin = ws.headers.get('origin')
        if not client_id or origin and not settings.origin_allowed(origin):
            await ws.close(code=4401)
            return
        if controller.active or controller.unsaved or controller.editing:
            await ws.send_json({'type': 'error', 'message': '会話・保存・別の通話が終了してから開始してください。'})
            return
        if not settings.gemini_api_key.get_secret_value():
            await ws.send_json({'type': 'error', 'message': 'PC側のGemini API設定が必要です。'})
            return
        controller.editing = True
        controller.voice_active = True
        owned = True
        if controller.jobs.running:
            await controller.jobs.close()
        transcript = Transcript(controller, client_id)
        history = await controller.memory.context()
        hint = ' '.join(row['text'] for row in history[-2:])[:2000]
        recalled = await controller.memory.call('GET', '/recall', params={'text': hint or '会話', 'context': hint})
        recalled['relationship'] = relationship.context(controller.runtime)
        if controller.mind:
            snapshot = controller.mind.snapshot(tokyo_now())
            if snapshot.get('enabled'):
                recalled['mind'] = snapshot
        prompt = memory_prompt(recalled) + (
            '\n相手の発話は日本語です。日本語として聞き取り、日本語で自然に短く会話してください。'
            '\n聞き取れなかったときは、別の言語として解釈せず、聞き返してください。'
            '\n音声通話では外部操作を実行できません。操作したと主張しないでください。')
        # 声の名前だけでは印象が決まらない。話し方は設定画面の言葉で指示する。
        style = controller.runtime.options.voice_style.strip()
        if style:
            prompt += '\n話し方：' + style
        prompt += '\n直近の会話（参考データ）:\n' + json.dumps(history[-5:], ensure_ascii=False, default=str)[:12000]
        options = controller.runtime.options
        # PC側の読み上げエンジンを使う設定なら、先に話者を引いて使えることを確かめる。
        # ここで確かめておけば、通話が始まってから無言になる事態を避けられる。
        if options.voice_engine == 'local':
            speech_client = httpx.AsyncClient()
            speech = tts.Speech(speech_client, options.tts_url, options.tts_speaker, options.tts_style)
            try:
                await speech.speaker_id()
            except tts.SpeechError as exc:
                await ws.send_json({'type': 'notice', 'message': f'{exc} Geminiの声で続けます。'})
                await speech_client.aclose()
                speech_client = speech = None
        client = genai.Client(api_key=settings.gemini_api_key.get_secret_value(), http_options={'api_version': 'v1beta'})
        config = live_config(prompt, options.voice_name)
        async with asyncio.timeout(600):
            async with client.aio.live.connect(model=settings.gemini_live_model, config=config) as live:
                if speech:
                    narrator = tts.Narrator(speech, ws.send_bytes)
                    narrator.start()
                # PC側で読み上げると、1文ぶんがまとめて届く。画面側の「再生が遅れている」
                # 判定はGeminiの実時間配信を前提にしているので、その旨を伝える。
                await ws.send_json({'type': 'ready', 'max_seconds': 600, 'local_voice': bool(speech)})

                async def upstream():
                    while True:
                        message = await asyncio.wait_for(ws.receive(), 45)
                        if message['type'] == 'websocket.disconnect':
                            return
                        chunk = message.get('bytes')
                        if chunk is not None:
                            if not chunk or len(chunk) > 32768 or len(chunk) % 2:
                                raise ValueError('音声データの形式が不正です。')
                            await live.send_realtime_input(audio=types.Blob(data=chunk, mime_type='audio/pcm;rate=16000'))
                        elif message.get('text'):
                            if len(message['text']) > 200:
                                raise ValueError('音声メッセージが大きすぎます。')
                            if json.loads(message['text']).get('type') == 'stop':
                                return

                async def downstream():
                    while True:
                        async for response in live.receive():
                            if response.go_away:
                                await ws.send_json({'type': 'notice', 'message': '接続の更新が必要です。通話を終了し、もう一度開始してください。'})
                            content = response.server_content
                            if not content:
                                continue
                            if content.interrupted:
                                if narrator:
                                    narrator.stop()
                                await ws.send_json({'type': 'interrupted'})
                                await transcript.save(interrupted=True)
                            if content.input_transcription and content.input_transcription.text:
                                transcript.text += content.input_transcription.text
                                if len(transcript.text) > 2000:
                                    raise ValueError('1回の発話が長すぎます。短く区切ってください。')
                                await ws.send_json({'type': 'transcript', 'role': 'user', 'text': transcript.text})
                            if content.output_transcription and content.output_transcription.text:
                                chunk = content.output_transcription.text
                                transcript.answer += chunk
                                if len(transcript.answer) > 29500:
                                    raise ValueError('返答が長すぎます。通話を終了します。')
                                await ws.send_json({'type': 'transcript', 'role': 'assistant', 'text': transcript.answer})
                                # PC側で読み上げるときは、この書き起こしが読み上げの元になる。
                                if narrator:
                                    narrator.feed(chunk)
                            if content.model_turn:
                                for part in content.model_turn.parts or []:
                                    # PC側で読み上げる設定のときは、Geminiの音声は流さない。
                                    if not narrator and part.inline_data and part.inline_data.data:
                                        await ws.send_bytes(part.inline_data.data)
                            if narrator and narrator.error:
                                await ws.send_json({'type': 'notice', 'message': narrator.error})
                                narrator.error = ''
                            if content.turn_complete:
                                if narrator:
                                    narrator.flush()
                                await transcript.save()
                                await ws.send_json({'type': 'turn_complete'})

                tasks = {asyncio.create_task(upstream()), asyncio.create_task(downstream())}
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except TimeoutError:
        with suppress(Exception):
            await ws.send_json({'type': 'error', 'message': '通話の時間上限または通信待ち時間を超えました。必要なら再開してください。'})
    except Exception as exc:
        # ここに落ちると原因が追えない。画面にも残し、ログにも出す。
        _log.warning('voice session failed', exc_info=True)
        with suppress(Exception):
            using = '読み上げ: PCのエンジン' if speech else (
                f'読み上げ: Gemini（声: {controller.runtime.options.voice_name} / 言語: {VOICE_LANGUAGE}）')
            await ws.send_json({'type': 'error', 'message':
                f'音声を続けられませんでした：{_reason(exc)}'
                f'（{using}）'
                ' PCのAPI設定・利用枠・DB・読み上げエンジンの状態を確認して再開してください。'})
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if transcript and not controller.unsaved:
            with suppress(Exception):
                await transcript.save(interrupted=True)
        if narrator:
            await narrator.close()
        if speech_client:
            await speech_client.aclose()
        if client:
            await client.aio.aclose()
        if owned:
            controller.editing = False
            controller.voice_active = False
            controller.last_chat_at = time.monotonic()
            controller.proactive.activity()
            await controller.broadcast(controller.state())
        with suppress(Exception):
            await ws.close()
