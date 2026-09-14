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

from . import relationship, tools, tts, update_awareness, voice_words
from .persona import memory_prompt
from .proactive import tokyo_now
from .tuning import VOICE_LANGUAGE

_log = logging.getLogger(__name__)
_SECRET = re.compile(r'(?i)\b(key|token|authorization)=[^&\s\'"]+')


# 通話中に使える道具。予約と記憶だけに絞る。画面で確認してから走らせる操作
# （BAT実行・動画再生・設定変更・ファイル検索）は、声だけでは実行させない。
# 文字チャットのように発言ごとに道具を選べないので、ここで固定して渡す。
VOICE_TOOL_NAMES = ('set_reminder', 'remember')
VOICE_DECLARATIONS = [item for item in tools.DECLARATIONS if item['name'] in VOICE_TOOL_NAMES]


def live_config(prompt: str, voice_name: str) -> dict:
    """Live session config. Barge-in is disabled unless explicitly reintroduced later."""
    return {'response_modalities': ['AUDIO'], 'system_instruction': prompt + voice_words.guidance(),
            'input_audio_transcription': {}, 'output_audio_transcription': {},
            'realtime_input_config': {'activity_handling': 'NO_INTERRUPTION'},
            'tools': [{'function_declarations': VOICE_DECLARATIONS}],
            'speech_config': {'language_code': VOICE_LANGUAGE,
                              'voice_config': {'prebuilt_voice_config': {'voice_name': voice_name}}}}


async def run_tool_calls(tool_call, memory, now=None) -> list[types.FunctionResponse]:
    """Liveからの道具呼び出しを実行し、返す応答を作る。

    渡していない道具名は実行しない。モデルが別の名前を口にしても、画面での確認が
    要る操作が声だけで走らないようにする。失敗は tools.run が飲み込むので、
    予約に失敗しても通話は続き、その旨をかぐやが話す。
    """
    replies = []
    for call in (tool_call.function_calls or [])[:2]:
        outcome = (await tools.run(call.name, dict(call.args or {}), memory, now)
                   if call.name in VOICE_TOOL_NAMES
                   else {'ok': False, 'error': 'この操作は音声通話ではできません。'})
        replies.append(types.FunctionResponse(id=call.id, name=call.name, response=outcome))
    return replies


def _reason(exc: BaseException) -> str:
    detail = _SECRET.sub(r'\1=***', str(exc)).replace('\n', ' ').strip()[:300]
    return type(exc).__name__ + (f': {detail}' if detail else '')


class Transcript:
    def __init__(self, controller, client_id):
        self.controller = controller
        self.client_id = client_id
        self.text = ''
        self.answer = ''

    async def save(self, interrupted=False):
        text, answer = voice_words.transcript_text(self.text.strip()), self.answer.strip()
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
        if not interrupted:
            update_awareness.acknowledge(c.runtime, answer)
        if c.living:
            c.living.seen(tokyo_now(), counted=True)
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
        recalled['relationship'] = relationship.context(controller.living) if controller.living else {}
        recalled['app_update'] = update_awareness.context(controller.runtime)
        if controller.mind:
            snapshot = controller.mind.snapshot(tokyo_now())
            if snapshot.get('enabled'):
                recalled['mind'] = snapshot
        prompt = memory_prompt(recalled, history=history) + (
            '\n相手の発話は日本語です。日本語として聞き取り、日本語で自然に短く会話してください。'
            '\nただし相談・比較・判断材料を求められたら、短さより具体案と理由を優先してください。'
            '相談中の条件の補足や質問への回答も同じ相談として扱い、気分や時間帯の短文化指示より優先してください。'
            '\n聞き取れなかったときは、別の言語として解釈せず、聞き返してください。'
            '\n時刻を指定した声かけの予約と、「覚えておいて」と頼まれた記憶は、道具を使って'
            '実際に登録してください。頼まれたのに道具を使わないまま「わかった」と答えないでください。'
            '\nそれ以外の操作（動画の再生、BATの実行、設定の変更、ファイル検索）は音声通話ではできません。'
            'できないことを、できたと言わないでください。')
        style = controller.runtime.options.voice_style.strip()
        if style:
            prompt += '\n話し方：' + style
        prompt += '\n直近の会話（参考データ）:\n' + json.dumps(history[-5:], ensure_ascii=False, default=str)[:12000]
        options = controller.runtime.options
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
                            if response.tool_call:
                                replies = await run_tool_calls(response.tool_call, controller.memory)
                                if replies:
                                    await live.send_tool_response(function_responses=replies)
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
                                await ws.send_json({'type': 'transcript', 'role': 'user',
                                                    'text': voice_words.transcript_text(transcript.text)})
                            if content.output_transcription and content.output_transcription.text:
                                chunk = content.output_transcription.text
                                transcript.answer += chunk
                                if len(transcript.answer) > 29500:
                                    raise ValueError('返答が長すぎます。通話を終了します。')
                                await ws.send_json({'type': 'transcript', 'role': 'assistant', 'text': transcript.answer})
                                if narrator:
                                    narrator.feed(chunk)
                            if content.model_turn:
                                for part in content.model_turn.parts or []:
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
