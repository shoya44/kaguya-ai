"""Read-only inspection helpers for Kaguya's own project files."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MAX_FILE_BYTES = 200_000
MAX_LINES = 400
MAX_MATCHES = 12
CONTEXT_LINES = 5
SAFE_SUFFIXES = {'.py', '.ts', '.css', '.html', '.rs', '.md', '.json', '.toml', '.bat', '.txt', '.sql'}
SKIP_PARTS = {'.git', '.venv', 'node_modules', 'target', 'dist', '__pycache__', '.pytest_cache'}

DECLARATIONS = [
    {
        'name': 'project_status',
        'description': 'かぐやAI自身のGitブランチと未確定変更の有無を読み取り専用で確認する。',
        'parameters': {'type': 'OBJECT', 'properties': {}},
    },
    {
        'name': 'project_search',
        'description': 'かぐやAI自身のREADME、docs、ソースコードを読み取り専用で検索する。'
                       '自分の仕様、画面文言、関数、バグ原因を確認するときに使う。検索結果には前後のコードも含まれる。',
        'parameters': {
            'type': 'OBJECT',
            'properties': {'query': {'type': 'STRING', 'description': '関数名、画面文言、仕様語などの検索文字列。'}},
            'required': ['query'],
        },
    },
    {
        'name': 'project_read',
        'description': 'かぐやAI自身のテキストファイルを読み取り専用で読む。既知のファイルを詳しく確認するときに使う。',
        'parameters': {
            'type': 'OBJECT',
            'properties': {
                'path': {'type': 'STRING', 'description': 'リポジトリルートからの相対パス。'},
                'start_line': {'type': 'INTEGER', 'description': '読み始める1始まり行番号。省略時1。'},
                'end_line': {'type': 'INTEGER', 'description': '読み終える行番号。最大400行。'},
            },
            'required': ['path'],
        },
    },
]


def _safe_relative(raw: str) -> str:
    value = str(raw or '').replace('\\', '/').strip()
    if value.startswith('./'):
        value = value[2:]
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts:
        raise ValueError('安全な相対パスを指定してください。')
    if {part.lower() for part in path.parts} & {part.lower() for part in SKIP_PARTS}:
        raise ValueError('生成物・依存フォルダは読み取り対象外です。')
    if path.suffix.lower() not in SAFE_SUFFIXES:
        raise ValueError('この種類のファイルは読み取れません。')
    resolved = (ROOT / Path(*path.parts)).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError:
        raise ValueError('リポジトリ外のファイルは読み取れません。') from None
    return path.as_posix()


def project_status() -> dict[str, Any]:
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        branch_proc = subprocess.run(['git', 'branch', '--show-current'], cwd=ROOT, text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     timeout=10, creationflags=flags)
        status = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=10, creationflags=flags)
        changes = [line[3:].strip() for line in status.stdout.splitlines() if line.strip()]
        return {'ok': status.returncode == 0,
                'branch': branch_proc.stdout.strip() or '(detached)',
                'tracked_changes': changes, 'clean': not changes}
    except (OSError, subprocess.SubprocessError):
        return {'ok': False, 'error': 'Git状態を確認できませんでした。'}


def project_search(query: str) -> dict[str, Any]:
    needle = str(query or '').strip()
    if len(needle) < 2:
        return {'ok': False, 'error': '検索文字列は2文字以上にしてください。'}
    matches = []
    for path in sorted(ROOT.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in SAFE_SUFFIXES:
            continue
        rel_parts = path.relative_to(ROOT).parts
        if {part.lower() for part in rel_parts} & {part.lower() for part in SKIP_PARTS}:
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            _safe_relative(rel)
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            lines = path.read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeError, ValueError):
            continue
        for number, line in enumerate(lines, 1):
            if needle.lower() not in line.lower():
                continue
            start = max(1, number - CONTEXT_LINES)
            end = min(len(lines), number + CONTEXT_LINES)
            context = '\n'.join(f'{i}: {lines[i - 1]}' for i in range(start, end + 1))
            matches.append({'path': rel, 'line': number, 'context': context})
            if len(matches) >= MAX_MATCHES:
                return {'ok': True, 'query': needle, 'matches': matches, 'truncated': True}
    return {'ok': True, 'query': needle, 'matches': matches, 'truncated': False}


def project_read(raw_path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
    try:
        rel = _safe_relative(raw_path)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    path = ROOT / rel
    if not path.is_file():
        return {'ok': False, 'error': 'ファイルが見つかりません。'}
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return {'ok': False, 'error': 'ファイルが大きすぎるため読み取れません。'}
        lines = path.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeError):
        return {'ok': False, 'error': 'テキストとして読み取れませんでした。'}
    start = max(1, int(start_line or 1))
    requested_end = int(end_line) if end_line is not None else start + 199
    end = min(len(lines), requested_end, start + MAX_LINES - 1)
    content = '\n'.join(f'{i}: {lines[i - 1]}' for i in range(start, end + 1))
    return {'ok': True, 'path': rel, 'start_line': start, 'end_line': end,
            'total_lines': len(lines), 'content': content}


def run(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    try:
        if name == 'project_status':
            return project_status()
        if name == 'project_search':
            return project_search(args.get('query', ''))
        if name == 'project_read':
            return project_read(args.get('path', ''), args.get('start_line', 1), args.get('end_line'))
    except Exception:
        return {'ok': False, 'error': 'プロジェクトの読み取りに失敗しました。'}
    return {'ok': False, 'error': '未対応の読み取り操作です。'}
