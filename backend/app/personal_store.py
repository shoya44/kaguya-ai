"""Calendar events (PostgreSQL) and the user's own reference files (on disk).

references/ stays a folder on purpose: the user drops files into it with
Explorer, so there would be no way to put anything in if it lived in the DB.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .db import Database

SAFE_REFERENCE_SUFFIXES = {'.md', '.txt', '.json', '.csv'}
MAX_REFERENCE_BYTES = 200_000
MAX_REFERENCE_MATCHES = 8


class CalendarStore:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _stamp(value: str) -> datetime:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            raise ValueError('timezone required')
        return stamp

    @staticmethod
    def _item(row: dict) -> dict:
        """画面とツールが読む形はcalendar.json時代と同じにしておく。"""
        return {'id': str(row['id']), 'title': row['title'],
                'start': row['start_at'].isoformat(),
                'end': row['end_at'].isoformat() if row['end_at'] else None,
                'note': row['note']}

    def add(self, title: str, start: str, end: str | None = None, note: str = '') -> dict:
        title = str(title).strip()
        if not title:
            raise ValueError('title required')
        start_at = self._stamp(start)
        end_at = self._stamp(end) if end else None
        if end_at and end_at <= start_at:
            raise ValueError('end must be after start')
        with self.db.session() as conn:
            row = conn.execute("""INSERT INTO calendar_events(id,title,start_at,end_at,note)
                VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                               (uuid4(), title, start_at, end_at, note)).fetchone()
        return self._item(row)

    def list(self, start: str, end: str) -> list[dict]:
        start_at, end_at = self._stamp(start), self._stamp(end)
        with self.db.session() as conn:
            # 終了のない予定は開始時刻だけの点として扱う。calendar.json と同じ判定。
            rows = conn.execute("""SELECT * FROM calendar_events
                WHERE start_at < %s AND coalesce(end_at,start_at) >= %s
                ORDER BY start_at LIMIT 30""", (end_at, start_at)).fetchall()
        return [self._item(row) for row in rows]

    def remove(self, query: str, start: str | None = None, end: str | None = None) -> dict:
        needle = query.strip().lower()
        if not needle:
            return {'removed': False, 'error': '削除する予定の名前かIDが必要です。'}
        start_at = self._stamp(start) if start else None
        end_at = self._stamp(end) if end else None
        with self.db.session() as conn:
            # 取り出しと削除を同じトランザクションで行い、間に増えた予定を消さない。
            rows = conn.execute("""SELECT * FROM calendar_events
                WHERE (%(start)s::timestamptz IS NULL OR start_at >= %(start)s)
                  AND (%(end)s::timestamptz IS NULL OR start_at < %(end)s)
                ORDER BY start_at""", {'start': start_at, 'end': end_at}).fetchall()
            # IDはUUIDとは限らない文字列で来るため、突き合わせはPython側で行う。
            matches = [row for row in rows
                       if str(row['id']) == query or needle in str(row['title']).lower()]
            if len(matches) != 1:
                return {'removed': False, 'matches': [self._item(row) for row in matches[:8]],
                        'error': '候補が1件に絞れませんでした。' if matches else '該当する予定がありません。'}
            target = matches[0]
            conn.execute('DELETE FROM calendar_events WHERE id=%s', (target['id'],))
        return {'removed': True, 'item': self._item(target)}


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
