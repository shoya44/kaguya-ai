import base64
import json
import secrets
from datetime import datetime
from uuid import UUID, uuid4

import httpx
import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from psycopg.rows import dict_row
from pydantic import ValidationError

from .errors import ChatError
from .models import Completion, Failure, Turn
from . import memory_store
from .personal_store import CalendarStore, ReferenceLibrary


def internal_auth(request: Request, x_internal_token: str = Header(default='')):
    if not secrets.compare_digest(x_internal_token, request.app.state.internal_token):
        raise HTTPException(403, 'Internal access only')


router = APIRouter(prefix='/internal/memory', dependencies=[Depends(internal_auth)])


def connection(request: Request):
    return database_connection(request.app.state.settings)


def database_connection(settings):
    dsn = settings.database_url.get_secret_value()
    if not dsn:
        raise HTTPException(503, 'Database unavailable')
    return psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)


TURN_SELECT = '''SELECT u.turn_id, u.content AS text, a.content AS answer,
    u.status, u.created_at, u.id, u.origin_client_id AS client_id, u.input_mode
    FROM memory_short u LEFT JOIN memory_short a
    ON a.turn_id=u.turn_id AND a.role='assistant' WHERE u.role='user' '''


def recover_pending(settings):
    with database_connection(settings) as conn:
        conn.execute("UPDATE memory_short SET status='failed' WHERE role='user' AND status='pending'")
    return {'ok': True}


@router.post('/begin')
def begin(turn: Turn, request: Request):
    with connection(request) as conn:
        # Serialize admission including the unprocessed-count limit.
        conn.execute('SELECT pg_advisory_xact_lock(8765001)')
        row = conn.execute(TURN_SELECT + ' AND u.turn_id=%s', (turn.turn_id,)).fetchone()
        if row:
            if (row['text'] != turn.text or row['client_id'] != turn.client_id
                    or row['input_mode'] != turn.input_mode):
                raise HTTPException(409, 'turn_conflict')
            if row['status'] == 'completed' or not turn.retry:
                return row
            if row['status'] in ('failed', 'cancelled'):
                conn.execute("""UPDATE memory_short SET status='pending',processed_at=NULL,
                    processing_reason=NULL,revision=revision+1 WHERE turn_id=%s AND role='user'""", (turn.turn_id,))
                row['status'] = 'pending'
            return row
        count = conn.execute('SELECT count(*) AS n FROM memory_short WHERE processed_at IS NULL').fetchone()['n']
        # Reserve room for the assistant message as well.
        if count >= 9999:
            raise HTTPException(409, 'memory_full')
        conn.execute('''INSERT INTO memory_short
            (id,turn_id,role,content,status,origin_client_id,input_mode)
            VALUES (%s,%s,'user',%s,'pending',%s,%s)''',
            (uuid4(), turn.turn_id, turn.text, turn.client_id, turn.input_mode))
        return conn.execute(TURN_SELECT + ' AND u.turn_id=%s', (turn.turn_id,)).fetchone()


@router.post('/turns/{turn_id}/complete')
def complete(turn_id: UUID, body: Completion, request: Request):
    with connection(request) as conn:
        user = conn.execute("SELECT * FROM memory_short WHERE turn_id=%s AND role='user' FOR UPDATE", (turn_id,)).fetchone()
        if not user:
            raise HTTPException(404, 'Turn not found')
        if user['status'] == 'cancelled':
            raise HTTPException(409, 'Turn cancelled')
        existing = conn.execute("SELECT content FROM memory_short WHERE turn_id=%s AND role='assistant'", (turn_id,)).fetchone()
        if existing and existing['content'] != body.answer:
            raise HTTPException(409, 'Answer conflict')
        if not existing:
            conn.execute('''INSERT INTO memory_short
                (id,turn_id,role,content,status,origin_client_id,input_mode)
                VALUES (%s,%s,'assistant',%s,'completed',%s,%s)''',
                (uuid4(), turn_id, body.answer, user['origin_client_id'], user['input_mode']))
        conn.execute("UPDATE memory_short SET status='completed' WHERE turn_id=%s AND role='user'", (turn_id,))
    return {'ok': True}


@router.post('/turns/{turn_id}/fail')
def fail(turn_id: UUID, body: Failure, request: Request):
    with connection(request) as conn:
        conn.execute("UPDATE memory_short SET status=%s WHERE turn_id=%s AND role='user' AND status='pending'", (body.status, turn_id))
    return {'ok': True}


@router.get('/context')
def context(request: Request):
    with connection(request) as conn:
        rows = conn.execute(TURN_SELECT + " AND u.status='completed' AND a.content IS NOT NULL ORDER BY u.created_at DESC,u.id DESC LIMIT 10").fetchall()
    return list(reversed(rows))


@router.get('/history')
def history(request: Request, cursor: str | None = Query(None, max_length=500), limit: int = Query(20, ge=1, le=50)):
    params: list = []
    query = TURN_SELECT
    if cursor:
        try:
            stamp, row_id = json.loads(base64.urlsafe_b64decode(cursor).decode())
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None:
                raise ValueError
            params = [parsed, UUID(row_id)]
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(400, 'Invalid cursor') from None
        query += ' AND (u.created_at,u.id)<(%s,%s)'
    with connection(request) as conn:
        rows = conn.execute(query + ' ORDER BY u.created_at DESC,u.id DESC LIMIT %s', (*params, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more:
        tail = rows[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps([tail['created_at'].isoformat(), str(tail['id'])]).encode()).decode()
    return {'items': list(reversed(rows)), 'next_cursor': next_cursor}


class MemoryClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def call(self, method: str, path: str, **kwargs):
        try:
            result = await self.client.request(method, '/internal/memory' + path, **kwargs)
            if result.status_code == 409:
                detail = result.json().get('detail')
                if detail == 'memory_changed':
                    raise ChatError('memory_changed', '記憶が更新されました。再読み込みしてから操作してください。')
                if detail == 'memory_full':
                    raise ChatError('memory_full', '未整理の記憶が上限に達しました。設定画面の「今すぐ整理」を実行してください。')
                raise ChatError('turn_conflict', '同じ会話IDに異なる内容は送信できません。')
            if result.status_code == 400:
                detail = result.json().get('detail', '')
                raise ChatError('invalid_request', detail if isinstance(detail, str) else '入力内容を確認してください。')
            if result.status_code == 404:
                raise ChatError('memory_changed', '対象の記憶が見つかりません。再読み込みしてください。')
            result.raise_for_status()
            return result.json()
        except (httpx.HTTPError, ValueError):
            raise ChatError('storage_error', '会話を保存・取得できませんでした。PC側のDB接続を確認してください。') from None

    async def begin(self, turn: dict):
        return await self.call('POST', '/begin', json=turn)

    async def context(self):
        return await self.call('GET', '/context')

    async def complete(self, turn_id: str, answer: str):
        return await self.call('POST', f'/turns/{turn_id}/complete', json={'answer': answer})

    async def fail(self, turn_id: str, status: str):
        return await self.call('POST', f'/turns/{turn_id}/fail', json={'status': status})

    async def history(self, cursor: str | None, limit: int = 20):
        params = {'limit': limit}
        if cursor:
            params['cursor'] = cursor
        return await self.call('GET', '/history', params=params)


# Function Callingからのみ使う軽量ローカル機能。外部UIへ新APIは増やさない。
@router.get('/runtime/settings')
def runtime_settings(request: Request):
    return {'options': request.app.state.controller.runtime.options.model_dump()}


@router.patch('/runtime/settings')
async def change_runtime_settings(body: dict, request: Request):
    controller = request.app.state.controller
    try:
        options = controller.runtime.update(body)
    except ValidationError:
        raise HTTPException(400, '設定値を確認してください。') from None
    except OSError:
        raise HTTPException(503, '設定ファイルを保存できませんでした。') from None
    if 'quiet' in body or 'proactive_minutes' in body:
        controller.proactive.reset()
    await controller.broadcast({'type': 'settings.changed', 'options': options.model_dump()})
    return {'options': options.model_dump()}


@router.post('/calendar')
def add_calendar_event(body: dict, request: Request):
    try:
        return CalendarStore(request.app.state.db).add(
            str(body.get('title', '')), str(body.get('start', '')),
            str(body['end']) if body.get('end') else None, str(body.get('note', '')),
        )
    except (TypeError, ValueError, OSError):
        raise HTTPException(400, '予定の日時または内容を確認してください。') from None


@router.get('/calendar')
def list_calendar_events(request: Request, start: str = Query(..., max_length=64),
                         end: str = Query(..., max_length=64)):
    try:
        items = CalendarStore(request.app.state.db).list(start, end)
    except (TypeError, ValueError, OSError):
        raise HTTPException(400, '予定の検索期間を確認してください。') from None
    return {'ok': True, 'items': items}


@router.post('/calendar/remove')
def remove_calendar_event(body: dict, request: Request):
    try:
        result = CalendarStore(request.app.state.db).remove(
            str(body.get('query', '')), str(body['start']) if body.get('start') else None,
            str(body['end']) if body.get('end') else None,
        )
    except (TypeError, ValueError, OSError):
        raise HTTPException(400, '削除条件を確認してください。') from None
    return {'ok': result.get('removed', False), **result}


@router.get('/references')
def references(request: Request, q: str = Query('', max_length=200)):
    try:
        return {'ok': True, **ReferenceLibrary(request.app.state.settings.data_dir).search(q)}
    except OSError:
        raise HTTPException(503, '参照フォルダを読み取れませんでした。') from None


@router.get('/recall')
def recalled(request: Request, text: str = Query('', max_length=2000),
             context: str = Query('', max_length=2000), mood: str = Query('', max_length=40)):
    with connection(request) as conn:
        return memory_store.recall(conn, text, context, mood)


@router.get('/summary')
def memory_summary(request: Request):
    with connection(request) as conn:
        return memory_store.summary(conn)


@router.get('/organize/snapshot')
def organize_snapshot(request: Request):
    with connection(request) as conn:
        return memory_store.snapshot(conn)


@router.post('/organize/commit')
def organize_commit(body: dict, request: Request):
    with connection(request) as conn:
        return memory_store.commit_wisdom(conn, body['snapshot'], body['result'])


@router.get('/weekly/snapshot')
def weekly_snapshot(request: Request):
    with connection(request) as conn:
        return memory_store.weekly_snapshot(conn)


@router.post('/weekly/commit')
def weekly_commit(body: dict, request: Request):
    with connection(request) as conn:
        return memory_store.commit_persona(conn, body['snapshot'], body['result'])


@router.post('/cleanup')
def cleanup(request: Request):
    with connection(request) as conn:
        return memory_store.cleanup(conn)


@router.post('/reminders')
def create_reminder(body: dict, request: Request):
    with connection(request) as conn:
        return memory_store.add_reminder(conn, body['due_at'], body['message'])


@router.get('/reminders')
def reminders(request: Request):
    with connection(request) as conn:
        return memory_store.pending_reminders(conn)


@router.post('/reminders/due')
def due_reminders(body: dict, request: Request):
    with connection(request) as conn:
        return memory_store.take_due_reminders(conn, body['now'])


@router.delete('/reminders/{reminder_id}')
def remove_reminder(reminder_id: UUID, request: Request):
    with connection(request) as conn:
        return memory_store.delete_reminder(conn, str(reminder_id))


@router.post('/reminders/{reminder_id}/ack')
def acknowledge_reminder(reminder_id: UUID, request: Request):
    with connection(request) as conn:
        return memory_store.acknowledge_reminder(conn, reminder_id)


@router.post('/remember')
def remember(body: dict, request: Request):
    with connection(request) as conn:
        return memory_store.remember(conn, body['topic'], body['fact'])


@router.post('/persona/style')
def persona_style(body: dict, request: Request):
    with connection(request) as conn:
        return memory_store.set_style(conn, body['value'], body.get('key', 'style_feedback'))


@router.get('/browse/{layer}')
def browse(layer: str, request: Request, q: str = Query('', max_length=200), offset: int = Query(0, ge=0, le=100000)):
    with connection(request) as conn:
        return memory_store.list_memories(conn, layer, q, offset)


@router.get('/browse/{layer}/{key}/impact')
def memory_impact(layer: str, key: str, request: Request):
    with connection(request) as conn:
        return memory_store.impact(conn, layer, key)


@router.post('/browse/{layer}/{key}/change')
def memory_change(layer: str, key: str, body: dict, request: Request):
    with connection(request) as conn:
        return memory_store.mutate(conn, layer, key, body, body.get('delete', False))


@router.post('/browse/persona/{key}/restore')
def memory_restore(key: str, body: dict, request: Request):
    with connection(request) as conn:
        return memory_store.restore_persona(conn, key, body['revision'])
