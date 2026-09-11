import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError, Field
from typing import Literal

from . import memory_api
from .config import PRIVATE_ORIGIN_PATTERN, Settings
from .controller import Controller
from .errors import ChatError
from .llm import Gemini
from .memory_api import MemoryClient
from .models import Turn
from .runtime import RuntimeStore
from .proactive import tokyo_now

settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Loopback-only milestone 2: a single random token shared between the
    # FastAPI process and its own internal HTTP client. Never exposed to UI.
    app.state.settings = settings
    app.state.internal_token = secrets.token_urlsafe(32)
    app.state.sessions = {}

    internal_client = httpx.AsyncClient(
        base_url=settings.internal_base_url,
        headers={'x-internal-token': app.state.internal_token},
        timeout=10.0,
    )
    memory = MemoryClient(internal_client)
    llm = Gemini(settings)
    connections: set[WebSocket] = set()

    async def broadcast(event: dict):
        async def send(ws):
            try:
                async with asyncio.timeout(3):
                    await ws.send_json(event)
            except Exception:
                connections.discard(ws)
                try:
                    async with asyncio.timeout(1):
                        await ws.close(code=1011)
                except Exception:
                    pass
        await asyncio.gather(*(send(ws) for ws in tuple(connections)))

    runtime = RuntimeStore(settings.data_dir)
    controller = Controller(memory, llm, broadcast, runtime)
    app.state.controller = controller
    app.state.connections = connections

    try:
        # Uvicorn cannot serve our internal HTTP API before lifespan yields.
        # Recover directly before accepting health/history/chat requests.
        await asyncio.to_thread(memory_api.recover_pending, settings)
        controller.start()
        yield
    finally:
        await controller.close()
        await llm.close()
        await internal_client.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(memory_api.router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    # Stage 6: also allow the iPhone's Safari, whose Origin is the PC's LAN
    # address (e.g. http://192.168.1.20:8765) and cannot be listed in advance.
    allow_origin_regex=PRIVATE_ORIGIN_PATTERN.pattern,
    allow_methods=['GET', 'POST', 'PATCH', 'DELETE'],
    allow_headers=['*'],
)


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_id: UUID | None = None


class SessionResponse(BaseModel):
    client_id: UUID
    session_token: str


def require_session(request: Request) -> UUID:
    """Milestone 2: loopback-only, single user. A bearer token issued by
    /session identifies the browser tab (client_id) for turn ownership and
    single-device playback rules; it is not a security boundary yet.
    """
    auth = request.headers.get('authorization', '')
    if not auth.startswith('Bearer '):
        raise HTTPException(401, 'Missing session token')
    token = auth.removeprefix('Bearer ')
    sessions: dict[str, UUID] = request.app.state.sessions
    client_id = sessions.get(token)
    if client_id is None:
        raise HTTPException(401, 'Invalid session token')
    return client_id


@app.get('/health')
async def health():
    return {'status': 'ok', 'backend_instance': settings.backend_instance}


@app.post('/session', response_model=SessionResponse)
async def create_session(body: SessionRequest, request: Request):
    client_id = body.client_id or uuid4()
    token = secrets.token_urlsafe(32)
    sessions = request.app.state.sessions
    # Single user: reloads and reconnects would otherwise grow this forever.
    # Clients that lose their token simply ask for a new one on the next 401.
    if len(sessions) >= 32:
        sessions.clear()
    sessions[token] = client_id
    return SessionResponse(client_id=client_id, session_token=token)


@app.get('/history')
async def get_history(request: Request, cursor: str | None = Query(None, max_length=500),
                       limit: int = Query(20, ge=1, le=50), _client_id: UUID = Depends(require_session)):
    memory: MemoryClient = request.app.state.controller.memory
    try:
        return await memory.history(cursor, limit)
    except ChatError as exc:
        raise HTTPException(502, exc.message) from None


@app.get('/settings')
async def get_settings(request: Request, _client_id: UUID = Depends(require_session)):
    controller = request.app.state.controller
    ledger = controller.runtime.data['ledger']
    try:
        reminders = (await controller.memory.call('GET', '/reminders'))['items']
    except ChatError:
        # DBが落ちていても設定画面自体は開けるようにする。
        reminders = []
    return {'options': controller.runtime.options.model_dump(),
            'jobs': {'running': controller.jobs.running, 'status': controller.jobs.status,
                     'last_status': ledger.get('last_job_status', ''),
                     'calls_today': ledger.get('calls', 0) if ledger.get('call_day') == tokyo_now().date().isoformat() else 0},
            'reminders': reminders,
            'model': settings.gemini_model, 'configured': bool(settings.gemini_api_key.get_secret_value())}


@app.patch('/settings')
async def change_settings(body: dict, request: Request, _client_id: UUID = Depends(require_session)):
    controller = request.app.state.controller
    try:
        options = controller.runtime.update(body)
        if 'quiet' in body or 'proactive_minutes' in body:
            controller.proactive.reset()
    except ValidationError:
        raise HTTPException(422, '設定値を確認してください。') from None
    except OSError:
        raise HTTPException(503, '設定ファイルを保存できませんでした。') from None
    await controller.broadcast({'type': 'settings.changed', 'options': options.model_dump()})
    return {'options': options.model_dump()}


@app.delete('/reminders/{reminder_id}')
async def delete_reminder(reminder_id: UUID, request: Request, _client_id: UUID = Depends(require_session)):
    return await memory_request(request, 'DELETE', f'/reminders/{reminder_id}')


@app.post('/jobs/run')
async def run_jobs(request: Request, _client_id: UUID = Depends(require_session)):
    try:
        request.app.state.controller.jobs.start(manual=True)
    except ChatError as exc:
        raise HTTPException(409, exc.message) from None
    return {'status': '整理を開始しました。'}


@app.post('/reminders/{reminder_id}/ack')
async def acknowledge_reminder(reminder_id: UUID, request: Request, _client_id: UUID = Depends(require_session)):
    result = await memory_request(request, 'POST', f'/reminders/{reminder_id}/ack')
    await request.app.state.controller.broadcast({'type': 'reminder.ack', 'id': str(reminder_id)})
    return result


async def memory_request(request, method, path, **kwargs):
    try:
        return await request.app.state.controller.memory.call(method, path, **kwargs)
    except ChatError as exc:
        raise HTTPException(409 if exc.code == 'memory_changed' else 502, exc.message) from None


def memory_key(layer, key):
    if layer == 'persona':
        if key not in ('base_personality', 'reply_style', 'addressing', 'support_style'):
            raise HTTPException(422, '記憶の項目を確認してください。')
        return key
    try:
        return str(UUID(key))
    except ValueError:
        raise HTTPException(422, '記憶のIDを確認してください。') from None


@app.get('/memories/{layer}')
async def memories(layer: Literal['raw', 'wisdom', 'persona'], request: Request,
                   q: str = Query('', max_length=200), offset: int = Query(0, ge=0, le=100000),
                   _client_id: UUID = Depends(require_session)):
    return await memory_request(request, 'GET', f'/browse/{layer}', params={'q': q, 'offset': offset})


@app.get('/memory-summary')
async def memory_summary(request: Request, _client_id: UUID = Depends(require_session)):
    result = await memory_request(request, 'GET', '/summary')
    controller = request.app.state.controller
    return {**result, 'running': controller.jobs.running, 'auto': controller.runtime.options.auto_jobs,
            'status': controller.jobs.status}


@app.get('/memories/{layer}/{key}/impact')
async def memory_impact(layer: Literal['raw', 'wisdom', 'persona'], key: str, request: Request,
                        _client_id: UUID = Depends(require_session)):
    return await memory_request(request, 'GET', f'/browse/{layer}/{memory_key(layer, key)}/impact')


class RevisionConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    confirmed: Literal[True]


class MemoryChange(RevisionConfirmation):
    impact_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    value: str = Field(default='', max_length=2000)
    locked: bool = True
    delete: bool = False


@app.patch('/memories/{layer}/{key}')
async def change_memory(layer: Literal['raw', 'wisdom', 'persona'], key: str, body: MemoryChange,
                        request: Request, _client_id: UUID = Depends(require_session)):
    key = memory_key(layer, key)
    controller = request.app.state.controller
    if controller.active or controller.unsaved or controller.editing:
        raise HTTPException(409, '会話・保存の完了後に記憶を変更してください。')
    controller.editing = True
    try:
        result = await memory_request(request, 'POST', f'/browse/{layer}/{key}/change', json=body.model_dump())
        await controller.broadcast({'type': 'memories.changed'})
        return result
    finally:
        controller.editing = False


@app.post('/memories/persona/{key}/restore')
async def restore_memory(key: str, body: RevisionConfirmation, request: Request, _client_id: UUID = Depends(require_session)):
    key = memory_key('persona', key)
    controller = request.app.state.controller
    if controller.active or controller.unsaved or controller.editing:
        raise HTTPException(409, '会話・保存の完了後に操作してください。')
    controller.editing = True
    try:
        result = await memory_request(request, 'POST', f'/browse/persona/{key}/restore', json=body.model_dump())
        await controller.broadcast({'type': 'memories.changed'})
        return result
    finally:
        controller.editing = False


async def ws_authenticate(ws: WebSocket) -> UUID:
    token = ws.query_params.get('token', '')
    sessions: dict[str, UUID] = ws.app.state.sessions
    client_id = sessions.get(token)
    if client_id is None:
        await ws.close(code=4401)
        raise WebSocketDisconnect(4401)
    origin = ws.headers.get('origin')
    if origin is not None and not ws.app.state.settings.origin_allowed(origin):
        await ws.close(code=4403)
        raise WebSocketDisconnect(4403)
    return client_id


@app.websocket('/ws')
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        client_id = await ws_authenticate(ws)
    except WebSocketDisconnect:
        return

    controller: Controller = ws.app.state.controller
    connections: set[WebSocket] = ws.app.state.connections
    connections.add(ws)

    async def emit(event: dict):
        await ws.send_json(event)

    try:
        await emit(controller.state())
        if controller.unsaved:
            await emit(controller.unsaved_event())
        while True:
            message = await ws.receive_json()
            msg_type = message.get('type')
            turn_id = message.get('turn_id')
            if msg_type == 'chat.send':
                try:
                    turn = Turn(
                        turn_id=turn_id, text=message.get('text', ''),
                        client_id=client_id, input_mode='text',
                        retry=bool(message.get('retry', False)),
                    )
                except ValidationError:
                    await emit(ChatError('invalid_turn', '送信内容を確認してください。').event(turn_id))
                    continue
                await controller.send(turn.model_dump(mode='json'), emit)
            elif msg_type == 'chat.cancel':
                await controller.cancel(turn_id, str(client_id), emit)
            elif msg_type == 'chat.retry_save':
                await controller.retry_save(turn_id, emit)
            elif msg_type == 'presence':
                controller.presence[ws] = asyncio.get_running_loop().time() + 12 if message.get('visible') is True else 0
            else:
                await emit(ChatError('unknown_event', '未知のイベントです。').event(turn_id))
    except WebSocketDisconnect:
        pass
    finally:
        connections.discard(ws)
        controller.presence.pop(ws, None)


# Stage 6 (home Wi-Fi only): serve the built frontend so the iPhone's Safari
# can open http://<PC's LAN address>:8765/ directly — same shared web code
# as Tauri, no separate iPhone build. Mounted last so it never shadows an
# API route above; a missing dist/ (not yet built) leaves the API-only
# behavior Tauri already relies on unchanged.
_dist = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
if _dist.is_dir():
    app.mount('/', StaticFiles(directory=_dist, html=True), name='frontend')
