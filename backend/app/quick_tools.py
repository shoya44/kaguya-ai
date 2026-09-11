"""Compact Function Calling tools for weather, settings, calendar and references."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .proactive import JST, tokyo_now
from .weather_tool import weather as fetch_weather

DECLARATIONS = [
    {
        'name': 'weather',
        'description': '現在・今日・明日の天気を取得する。天気、雨、気温、傘について聞かれた時だけ使う。',
        'parameters': {'type': 'OBJECT', 'properties': {
            'location': {'type': 'STRING', 'description': '地点名。省略時は設定済みの地点。'},
        }},
    },
    {
        'name': 'app_settings',
        'description': 'かぐやAIの設定を確認または1項目変更する。設定変更を明示された時だけ使う。',
        'parameters': {'type': 'OBJECT', 'properties': {
            'action': {'type': 'STRING', 'enum': ['get', 'set']},
            'name': {'type': 'STRING', 'description': 'quiet, proactive_minutes, always_on_top, font_size, weather_location のいずれか。'},
            'value': {'type': 'STRING', 'description': 'set時の新しい値。'},
        }, 'required': ['action']},
    },
    {
        'name': 'calendar',
        'description': 'ローカル予定表を追加・一覧・削除する。日時は日本時間ISO 8601で扱う。',
        'parameters': {'type': 'OBJECT', 'properties': {
            'action': {'type': 'STRING', 'enum': ['add', 'list', 'remove']},
            'title': {'type': 'STRING'}, 'start': {'type': 'STRING'}, 'end': {'type': 'STRING'},
            'note': {'type': 'STRING'}, 'query': {'type': 'STRING'},
        }, 'required': ['action']},
    },
    {
        'name': 'reference_search',
        'description': 'referencesフォルダのmd/txt/json/csvを検索する。空文字なら一覧を返す。',
        'parameters': {'type': 'OBJECT', 'properties': {
            'query': {'type': 'STRING', 'description': '探す語句。一覧確認なら空文字。'},
        }, 'required': ['query']},
    },
]

SETTING_NAMES = {'quiet', 'proactive_minutes', 'always_on_top', 'font_size', 'weather_location'}


def _setting_value(name: str, raw: Any) -> Any:
    value = str(raw).strip()
    if name in {'quiet', 'always_on_top'}:
        if value.lower() in {'true', '1', 'on', 'yes', 'はい', '有効'}:
            return True
        if value.lower() in {'false', '0', 'off', 'no', 'いいえ', '無効'}:
            return False
        raise ValueError('オン/オフを解釈できませんでした。')
    if name in {'proactive_minutes', 'font_size'}:
        return int(value)
    if name == 'weather_location':
        if not value or len(value) > 80:
            raise ValueError('地点名を確認してください。')
        return value
    raise ValueError('変更できない設定です。')


def _iso(value: Any) -> str:
    stamp = datetime.fromisoformat(str(value))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=JST)
    return stamp.isoformat()


async def run(name: str, args: dict[str, Any], memory) -> dict[str, Any]:
    try:
        if name == 'weather':
            location = str(args.get('location', '')).strip()
            if not location:
                body = await memory.call('GET', '/runtime/settings')
                location = str(body['options'].get('weather_location') or '東京')
            try:
                return await fetch_weather(location)
            except Exception:
                return {'ok': False, 'error': f'{location}の天気を取得できませんでした。通信状態や地点名を確認してね。'}
        if name == 'app_settings':
            action = str(args.get('action', '')).lower()
            if action == 'get':
                body = await memory.call('GET', '/runtime/settings')
                return {'ok': True, 'settings': {k: v for k, v in body['options'].items() if k in SETTING_NAMES}}
            if action == 'set':
                setting = str(args.get('name', '')).strip()
                if setting not in SETTING_NAMES:
                    return {'ok': False, 'error': '変更できる設定名ではありません。'}
                body = await memory.call('PATCH', '/runtime/settings',
                                         json={setting: _setting_value(setting, args.get('value', ''))})
                return {'ok': True, 'name': setting, 'value': body['options'][setting]}
            return {'ok': False, 'error': 'actionはgetまたはsetです。'}
        if name == 'calendar':
            action = str(args.get('action', '')).lower()
            if action == 'add':
                title = str(args.get('title', '')).strip()[:120]
                if not title or not args.get('start'):
                    return {'ok': False, 'error': '予定名と開始日時が必要です。'}
                return {'ok': True, 'event': await memory.call('POST', '/calendar', json={
                    'title': title, 'start': _iso(args['start']),
                    'end': _iso(args['end']) if args.get('end') else None,
                    'note': str(args.get('note', '')).strip()[:300],
                })}
            if action == 'list':
                start = _iso(args['start']) if args.get('start') else tokyo_now().isoformat()
                end = _iso(args['end']) if args.get('end') else (tokyo_now() + timedelta(days=7)).isoformat()
                return await memory.call('GET', '/calendar', params={'start': start, 'end': end})
            if action == 'remove':
                return await memory.call('POST', '/calendar/remove', json={
                    'query': str(args.get('query', '')).strip(),
                    'start': _iso(args['start']) if args.get('start') else None,
                    'end': _iso(args['end']) if args.get('end') else None,
                })
            return {'ok': False, 'error': 'actionはadd/list/removeです。'}
        if name == 'reference_search':
            return await memory.call('GET', '/references', params={'q': str(args.get('query', ''))[:200]})
    except (ValueError, TypeError) as exc:
        return {'ok': False, 'error': str(exc)}
    except Exception:
        return {'ok': False, 'error': '操作を完了できませんでした。'}
    return {'ok': False, 'error': '未対応の操作です。'}
