"""Small local stores for calendar events and user reference files."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

SAFE_REFERENCE_SUFFIXES = {'.md', '.txt', '.json', '.csv'}
MAX_REFERENCE_BYTES = 200_000
MAX_REFERENCE_MATCHES = 8


class CalendarStore:
    def __init__(self, directory: Path):
        self.path = directory / 'calendar.json'
        self.lock = RLock()

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return value if isinstance(value, list) else []
        except (OSError, ValueError, TypeError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='calendar-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(items, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _stamp(value: str) -> datetime:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            raise ValueError('timezone required')
        return stamp

    def add(self, title: str, start: str, end: str | None = None, note: str = '') -> dict:
        start_at = self._stamp(start)
        end_at = self._stamp(end) if end else None
        if end_at and end_at <= start_at:
            raise ValueError('end must be after start')
        item = {
            'id': str(uuid4()), 'title': title, 'start': start_at.isoformat(),
            'end': end_at.isoformat() if end_at else None, 'note': note,
        }
        with self.lock:
            items = self._load()
            items.append(item)
            items.sort(key=lambda row: row['start'])
            self._save(items)
        return item

    def list(self, start: str, end: str) -> list[dict]:
        start_at, end_at = self._stamp(start), self._stamp(end)
        with self.lock:
            items = self._load()
        result = []
        for item in items:
            try:
                item_start = self._stamp(item['start'])
                item_end = self._stamp(item['end']) if item.get('end') else item_start
            except (KeyError, TypeError, ValueError):
                continue
            if item_start < end_at and item_end >= start_at:
                result.append(item)
        return result[:30]

    def remove(self, query: str, start: str | None = None, end: str | None = None) -> dict:
        needle = query.strip().lower()
        if not needle:
            return {'removed': False, 'error': '削除する予定の名前かIDが必要です。'}
        start_at = self._stamp(start) if start else None
        end_at = self._stamp(end) if end else None
        with self.lock:
            items = self._load()
            matches = []
            for item in items:
                try:
                    item_start = self._stamp(item['start'])
                except (KeyError, TypeError, ValueError):
                    continue
                if start_at and item_start < start_at:
                    continue
                if end_at and item_start >= end_at:
                    continue
                if item.get('id') == query or needle in str(item.get('title', '')).lower():
                    matches.append(item)
            if len(matches) != 1:
                return {'removed': False, 'matches': matches[:8],
                        'error': '候補が1件に絞れませんでした。' if matches else '該当する予定がありません。'}
            target = matches[0]
            self._save([item for item in items if item.get('id') != target.get('id')])
        return {'removed': True, 'item': target}


class ReferenceLibrary:
    def __init__(self, directory: Path):
        self.path = directory / 'references'
        self.path.mkdir(parents=True, exist_ok=True)

    def files(self) -> list[Path]:
        result = []
        for path in sorted(self.path.rglob('*')):
            if not path.is_file() or path.suffix.lower() not in SAFE_REFERENCE_SUFFIXES:
                continue
            try:
                if path.stat().st_size <= MAX_REFERENCE_BYTES:
                    result.append(path)
            except OSError:
                continue
        return result

    def search(self, query: str) -> dict:
        needle = query.strip().lower()
        files = self.files()
        if not needle:
            return {'folder': str(self.path), 'files': [p.relative_to(self.path).as_posix() for p in files]}
        matches = []
        for path in files:
            rel = path.relative_to(self.path).as_posix()
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
            except (OSError, UnicodeError):
                continue
            if needle in rel.lower():
                excerpt = '\n'.join(f'{i}: {line}' for i, line in enumerate(lines[:20], 1))
                matches.append({'path': rel, 'line': 1, 'excerpt': excerpt})
            for number, line in enumerate(lines, 1):
                if needle not in line.lower():
                    continue
                lo, hi = max(1, number - 2), min(len(lines), number + 2)
                excerpt = '\n'.join(f'{i}: {lines[i - 1]}' for i in range(lo, hi + 1))
                matches.append({'path': rel, 'line': number, 'excerpt': excerpt})
                if len(matches) >= MAX_REFERENCE_MATCHES:
                    return {'folder': str(self.path), 'matches': matches, 'truncated': True}
            if len(matches) >= MAX_REFERENCE_MATCHES:
                break
        return {'folder': str(self.path), 'matches': matches[:MAX_REFERENCE_MATCHES],
                'truncated': len(matches) > MAX_REFERENCE_MATCHES}
