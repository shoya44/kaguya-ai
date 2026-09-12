"""Explicit local allowlists. No arbitrary paths or command arguments from clients."""
import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'pc_access.json'


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    path: str
    description: str = Field(default='', max_length=300)
    timeout_seconds: int = Field(default=300, ge=5, le=3600)


class AccessConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    video_folders: dict[str, str] = Field(default_factory=dict)
    commands: dict[str, Command] = Field(default_factory=dict)
    tailscale_origin: str = ''
    tailscale_login: str = ''


def load_config() -> AccessConfig:
    if not CONFIG_PATH.exists():
        return AccessConfig()
    try:
        return AccessConfig.model_validate_json(CONFIG_PATH.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        raise HTTPException(503, 'PC設定を読み取れません。backend/pc_access.jsonを確認してください。') from None


def safe_config() -> AccessConfig:
    """読めない設定は「未設定」として扱う。PC連携の設定ミスで、チャットや音声など
    関係のない機能まで止めないため。設定エラーを利用者へ返すのは /pc/ の役目。"""
    try:
        return load_config()
    except HTTPException:
        return AccessConfig()


def trusted(scope, config=None) -> bool:
    """Only a local client or our local Serve proxy. Run uvicorn --no-proxy-headers."""
    config = config or load_config()
    if (scope.get('client') or ('',))[0] not in ('127.0.0.1', '::1', 'testclient'):
        return False
    headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
    host = headers.get('host', '').lower()
    origin = headers.get('origin', '')
    local_hosts = ('127.0.0.1', 'localhost', '[::1]', 'testserver')
    local = any(host == item or host.startswith(item + ':') for item in local_hosts)
    if local and not headers.get('tailscale-user-login'):
        return not origin or origin in ('http://127.0.0.1:5173', 'http://127.0.0.1:8765',
            'http://localhost:8765', 'http://tauri.localhost', 'https://tauri.localhost', 'tauri://localhost')
    remote = urlsplit(config.tailscale_origin)
    return bool(remote.scheme == 'https' and remote.hostname and remote.hostname.endswith('.ts.net')
        and host == remote.netloc.lower() and (not origin or origin == config.tailscale_origin)
        and config.tailscale_login and headers.get('tailscale-user-login') == config.tailscale_login)


class LocalAccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            headers = dict(scope.get('headers', []))
            protected = scope.get('path', '').startswith(('/pc/', '/voice'))
            proxied = b'tailscale-user-login' in headers or b'.ts.net' in headers.get(b'host', b'')
            if protected or proxied:
                try:
                    allowed = trusted(scope)
                except HTTPException:
                    allowed = False
                if not allowed:
                    if scope['type'] == 'websocket':
                        await send({'type': 'websocket.close', 'code': 4403})
                    else:
                        from fastapi.responses import JSONResponse
                        await JSONResponse({'detail': 'PC本体または本人のTailscale接続から利用してください。'}, status_code=403)(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def video_path(folder: str, relative: str) -> Path:
    root_value = load_config().video_folders.get(folder)
    if not root_value:
        raise HTTPException(404, '動画フォルダが登録されていません。')
    root = Path(root_value).resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or candidate.suffix.lower() != '.mp4' or not candidate.is_file():
        raise HTTPException(404, '動画が見つかりません。')
    return candidate


def _atoms(stream, end):
    """MP4の箱を順に返す。箱は [4バイトの長さ][4文字の種類][中身] の並び。"""
    while stream.tell() < end:
        head = stream.read(8)
        if len(head) < 8:
            return
        size = int.from_bytes(head[:4], 'big')
        kind = head[4:8]
        start = stream.tell()
        if size == 1:                      # 長さが4バイトに収まらない場合は次の8バイト
            size = int.from_bytes(stream.read(8), 'big') - 16
            start = stream.tell()
        elif size == 0:                    # 0はファイル末尾までを指す
            size = end - start
        else:
            size -= 8
        if size < 0:
            return
        yield kind, start, size
        stream.seek(start + size)


def seconds(path: Path) -> float | None:
    """mp4の再生時間。moov/mvhd から読む。読めなければNoneを返して表示を省く。"""
    try:
        end = path.stat().st_size
        with path.open('rb') as stream:
            for kind, start, size in _atoms(stream, end):
                if kind != b'moov':
                    continue
                stream.seek(start)
                for inner, at, _ in _atoms(stream, start + size):
                    if inner != b'mvhd':
                        continue
                    stream.seek(at)
                    version = stream.read(4)[0]
                    body = stream.read(28 if version == 1 else 16)
                    if version == 1:
                        scale = int.from_bytes(body[16:20], 'big')
                        length = int.from_bytes(body[20:28], 'big')
                    else:
                        scale = int.from_bytes(body[8:12], 'big')
                        length = int.from_bytes(body[12:16], 'big')
                    return length / scale if scale and length else None
                return None
    except (OSError, IndexError):
        return None
    return None


def videos(query='', offset=0):
    items = []
    scanned = 0
    truncated = False
    query = unicodedata.normalize('NFKC', query).casefold()
    # 設定の読み込みは1回だけ。ファイルごとに読み直すと、走査した数だけ
    # ディスク読み込みと検証が走る。
    for label, directory in sorted(load_config().video_folders.items()):
        root = Path(directory).resolve()
        if not root.is_dir():
            continue
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not Path(base, d).is_symlink() and not Path(base, d).is_junction())
            for name in sorted(files):
                scanned += 1
                if scanned > 10000:
                    truncated = True
                    break
                path = Path(base, name)
                if path.suffix.lower() != '.mp4' or path.is_symlink():
                    continue
                relative = path.relative_to(root).as_posix()
                if query not in unicodedata.normalize('NFKC', label + '/' + relative).casefold():
                    continue
                try:
                    # video_path と同じ条件で確かめる（実体がroot配下、通常ファイル）。
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root) or not resolved.is_file():
                        continue
                    items.append({'folder': label, 'path': relative, 'name': name,
                                  'size': resolved.stat().st_size, 'full': str(resolved)})
                except OSError:
                    continue
            if truncated:
                break
        if truncated:
            break
    page = items[offset:offset + 30]
    # 再生時間はファイルを開いて読むので、実際に返す分だけ調べる。
    for item in page:
        item['seconds'] = seconds(Path(item.pop('full')))
    return {'items': page, 'next_offset': offset + 30 if len(items) > offset + 30 else None,
            'truncated': truncated}


def commands():
    return [{'id': key, 'name': value.name, 'description': value.description}
            for key, value in load_config().commands.items()]


def _normalized(value: str) -> str:
    return unicodedata.normalize('NFKC', str(value or '')).casefold().strip()


def _question_only(value: str) -> bool:
    # Requests that merely ask whether/how an operation can be done must never
    # open an execution confirmation on their own.
    return any(word in value for word in (
        'していい', 'してもいい', '大丈夫', '危険', '問題ある', 'どうなる',
        'できる?', 'できる？', 'できますか', '可能?', '可能？', '方法', 'やり方',
    ))


def _video_query(text: str) -> str:
    # Keep this intentionally conservative. Generic requests such as
    # "音楽を流して" should remain ordinary chat; local PC playback is only
    # selected when the user explicitly says video/MP4.
    value = unicodedata.normalize('NFKC', str(text or '')).strip()
    lower = value.casefold()
    if not any(word in lower for word in ('動画', 'ビデオ', 'mp4')):
        return ''
    if _question_only(lower):
        return ''
    if not any(word in lower for word in ('再生して', '見せて', 'みせて', '開いて', 'ひらいて', '探して', 'さがして', '見たい', '流して', 'ながして')):
        return ''
    # Remove only request boilerplate; the remaining text is used as the PC-tab
    # filename/folder search. Trailing Japanese particles are trimmed so
    # "猫の動画を再生して" becomes "猫" rather than "猫の".
    query = value
    query = re.sub(r'(?i)\.?(?:mp4)|動画|ビデオ', ' ', query)
    query = re.sub(r'(?:を)?(?:再生|見せ|みせ|開い|ひらい|探し|さがし|流し|ながし)(?:してください|してほしい|てほしい|てよ|てね|お願い|して|て)?', ' ', query)
    query = re.sub(r'(?:が)?見たい', ' ', query)
    query = re.sub(r'[「」『』"“”!?！？。、,]+', ' ', query)
    query = re.sub(r'\s+', ' ', query).strip()
    query = re.sub(r'(?:の|を|が|は|で|から)$', '', query).strip()
    if query in {'こ', 'この', 'その', 'あの', 'これ', 'それ', 'あれ'}:
        return ''
    return query[:200]


def chat_action(text: str) -> dict | None:
    """Resolve explicit chat requests that should hand off to the PC tab.

    This never executes a command itself. BAT execution still goes through the
    existing prepare -> visible confirmation -> run flow in the browser.
    """
    value = str(text or '').strip()
    lower = _normalized(value)
    if not lower:
        return None

    # 1件も登録されていないなら、この機能は使えない。案内を返さず通常の会話へ返す。
    config = safe_config()
    if not config.video_folders and not config.commands:
        return None

    execute_hint = any(word in lower for word in ('実行', '起動', '動かして', '走らせ', 'run'))
    request_hint = any(word in lower for word in ('実行して', '起動して', '動かして', '走らせて', 'runして', 'お願い', 'やって'))
    command_request = (execute_hint and request_hint and not _question_only(lower)
                       and bool(config.commands))
    bat_hint = 'bat' in lower or 'バッチ' in lower
    matched = []
    if command_request:
        for key, command in config.commands.items():
            name = _normalized(command.name)
            if name and name in lower:
                matched.append((len(name), key, command))
        matched.sort(reverse=True, key=lambda row: row[0])

    if command_request and (bat_hint or matched):
        if matched:
            # Prefer the longest registered display name when registrations
            # overlap (e.g. "Backup" and "Backup Full").
            _, key, command = matched[0]
            return {
                'event': {'command_id': key},
                'reply': f'「{command.name}」の実行確認をファイルタブに開いたよ。内容を確認して、実行してね。',
            }
        return {
            'event': {},
            'reply': 'ファイルタブを開いたよ。実行するBATを選んで、内容を確認してね。',
        }

    if not config.video_folders:
        return None
    query = _video_query(value)
    video_request = (not _question_only(lower)
                     and any(word in lower for word in ('動画', 'ビデオ', 'mp4'))
                     and any(word in lower for word in ('再生して', '見せて', 'みせて', '開いて', 'ひらいて', '探して', 'さがして', '見たい', '流して', 'ながして')))
    if query or video_request:
        return {
            'event': {'query': query, 'autoload_video': bool(query)},
            'reply': (f'ファイルタブで「{query}」の動画を探したよ。候補が1件なら再生画面まで開くね。'
                      if query else 'ファイルタブを開いたよ。再生する動画を選んでね。'),
        }

    # 画面のタブ名は「ファイル」。以前の呼び名「PCタブ」で頼まれても通す。
    if (('pc' in lower or 'ファイル' in lower) and 'タブ' in lower
            and any(word in lower for word in ('開', '表示', '見せ'))):
        return {'event': {}, 'reply': 'ファイルタブを開いたよ。'}
    return None


class PCService:
    def __init__(self):
        self.tickets = {}
        self.confirmations = {}
        self.jobs = {}
        self.tasks = set()

    def ticket(self, folder, relative):
        video_path(folder, relative)
        self.tickets = {k: v for k, v in self.tickets.items() if v[0] > time.monotonic()}
        if len(self.tickets) >= 64:
            raise HTTPException(429, '動画の再生枠がいっぱいです。時間を置いて再試行してください。')
        ticket = secrets.token_urlsafe(32)
        self.tickets[ticket] = (time.monotonic() + 7200, folder, relative)
        return ticket

    def prepare(self, key, owner):
        command = load_config().commands.get(key)
        if not command:
            raise HTTPException(404, '登録されたBATがありません。')
        path = Path(command.path)
        if not path.is_absolute() or path.suffix.lower() != '.bat' or not path.is_file():
            raise HTTPException(400, 'BATの登録パスを確認してください。')
        # cmd expands these even inside quoted arguments. No client-supplied arguments.
        if any(c in str(path) for c in ('%', '!', '"', '\r', '\n')):
            raise HTTPException(400, 'BATのパスに使用できない文字があります。')
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            raise HTTPException(400, 'BATを読み取れません。登録パスを確認してください。') from None
        self.confirmations = {k: v for k, v in self.confirmations.items() if v[0] > time.monotonic()}
        if len(self.confirmations) >= 32:
            raise HTTPException(429, '確認要求が多すぎます。')
        token = secrets.token_urlsafe(32)
        self.confirmations[token] = (time.monotonic() + 120, owner, key, command.model_dump(), digest)
        return {'confirmation': token, 'name': command.name, 'description': command.description,
                'timeout_seconds': command.timeout_seconds}

    def start(self, token, owner):
        if any(job['status'] == 'running' for job in self.jobs.values()):
            raise HTTPException(409, '別のBATが実行中です。')
        record = self.confirmations.get(token)
        if not record or record[0] < time.monotonic() or record[1] != owner:
            raise HTTPException(409, '確認が期限切れです。もう一度選んでください。')
        _, _, key, snapshot, digest = record
        command = load_config().commands.get(key)
        if not command or command.model_dump() != snapshot:
            raise HTTPException(409, 'BATまたは設定が変更されました。もう一度確認してください。')
        try:
            current_digest = hashlib.sha256(Path(command.path).read_bytes()).hexdigest()
        except OSError:
            raise HTTPException(409, 'BATまたは設定が変更されました。もう一度確認してください。') from None
        if current_digest != digest:
            raise HTTPException(409, 'BATまたは設定が変更されました。もう一度確認してください。')
        if os.name != 'nt':
            raise HTTPException(400, 'BATはWindowsでのみ実行できます。')
        del self.confirmations[token]
        self.jobs = dict(list(self.jobs.items())[-19:])
        job_id = secrets.token_urlsafe(16)
        job = {'id': job_id, 'name': command.name, 'status': 'running', 'exit_code': None,
               'message': '実行中', 'owner': str(owner)}
        self.jobs[job_id] = job
        task = asyncio.create_task(self._execute(command, job))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return self.public(job)

    @staticmethod
    def public(job):
        return {k: v for k, v in job.items() if k != 'owner'}

    async def _execute(self, command, job):
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe'),
                '/d', '/v:off', '/s', '/c', f'""{command.path}""',
                cwd=str(Path(command.path).parent), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            await asyncio.wait_for(process.wait(), command.timeout_seconds)
            job.update(status='completed' if process.returncode == 0 else 'failed',
                       exit_code=process.returncode, message='BAT終了。起動した別アプリの処理完了は保証しません。')
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if process and process.returncode is None:
                killer = await asyncio.create_subprocess_exec('taskkill.exe', '/PID', str(process.pid), '/T', '/F',
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                await killer.wait()
            job.update(status='stopped', message='時間上限またはアプリ終了により停止しました。完了済みの変更は戻りません。')
        except Exception:
            job.update(status='failed', message='起動できませんでした。登録したBATを確認してください。')

    async def close(self):
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


class VideoRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    folder: str = Field(max_length=80)
    path: str = Field(max_length=1000)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation: str = Field(max_length=100)
    confirmed: bool


def router(require_session):
    routes = APIRouter(prefix='/pc')

    @routes.get('/status')
    def status(request: Request, owner=Depends(require_session)):
        config = load_config()
        return {'video_folders': list(config.video_folders), 'commands': commands(),
                'remote_url': config.tailscale_origin, 'voice_model': request.app.state.settings.gemini_live_model,
                'voice_configured': bool(request.app.state.settings.gemini_api_key.get_secret_value())}

    @routes.get('/videos')
    def list_videos(q: str = Query('', max_length=200), offset: int = Query(0, ge=0, le=10000), owner=Depends(require_session)):
        return videos(q, offset)

    @routes.post('/videos/ticket')
    def ticket(body: VideoRequest, request: Request, owner=Depends(require_session)):
        token = request.app.state.pc.ticket(body.folder, body.path)
        return {'url': '/pc/stream/' + token, 'expires_seconds': 7200}

    @routes.api_route('/stream/{token}', methods=['GET', 'HEAD'])
    def stream(token: str, request: Request):
        entry = request.app.state.pc.tickets.get(token)
        if not entry or entry[0] < time.monotonic():
            raise HTTPException(403, '再生リンクが期限切れです。動画を選び直してください。')
        return FileResponse(video_path(entry[1], entry[2]), media_type='video/mp4',
            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    @routes.post('/commands/{key}/prepare')
    def prepare(key: str, request: Request, owner=Depends(require_session)):
        return request.app.state.pc.prepare(key, owner)

    @routes.post('/commands/run')
    async def run(body: RunRequest, request: Request, owner=Depends(require_session)):
        if not body.confirmed:
            raise HTTPException(400, '実行前の確認が必要です。')
        return request.app.state.pc.start(body.confirmation, owner)

    @routes.get('/jobs')
    def jobs(request: Request, owner=Depends(require_session)):
        return {'items': [PCService.public(j) for j in request.app.state.pc.jobs.values() if j['owner'] == str(owner)]}

    return routes
