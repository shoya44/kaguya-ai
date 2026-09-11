"""Small cached Open-Meteo weather lookup."""
from __future__ import annotations

import time
from typing import Any

import httpx

WEATHER_CODES = {
    0: '快晴', 1: '晴れ', 2: '晴れ時々曇り', 3: '曇り', 45: '霧', 48: '霧',
    51: '弱い霧雨', 53: '霧雨', 55: '強い霧雨', 61: '弱い雨', 63: '雨', 65: '強い雨',
    71: '弱い雪', 73: '雪', 75: '強い雪', 80: 'にわか雨', 81: 'にわか雨', 82: '強いにわか雨',
    85: 'にわか雪', 86: '強いにわか雪', 95: '雷雨', 96: '雷雨', 99: '強い雷雨',
}
_GEO_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_WEATHER_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


async def _json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=6.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError('unexpected response')
        return value


async def _resolve(name: str) -> dict[str, Any]:
    key = name.strip().lower()
    cached = _GEO_CACHE.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    value = await _json('https://geocoding-api.open-meteo.com/v1/search', {
        'name': name, 'count': 1, 'language': 'ja', 'format': 'json',
    })
    rows = value.get('results') or []
    if not rows:
        raise ValueError('地点が見つかりませんでした。')
    row = rows[0]
    result = {'latitude': row['latitude'], 'longitude': row['longitude'],
              'label': ' / '.join(str(v) for v in [row.get('name'), row.get('admin1')] if v)}
    _GEO_CACHE[key] = (time.monotonic() + 86400, result)
    return result


async def weather(location: str) -> dict[str, Any]:
    key = location.strip().lower()
    cached = _WEATHER_CACHE.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    place = await _resolve(location)
    value = await _json('https://api.open-meteo.com/v1/forecast', {
        'latitude': place['latitude'], 'longitude': place['longitude'], 'timezone': 'Asia/Tokyo',
        'forecast_days': 2,
        'current': 'temperature_2m,apparent_temperature,precipitation,weather_code',
        'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max',
    })
    current, daily = value.get('current') or {}, value.get('daily') or {}

    def day(index: int) -> dict[str, Any]:
        def item(key_name: str):
            rows = daily.get(key_name) or []
            return rows[index] if index < len(rows) else None
        code = item('weather_code')
        return {'天気': WEATHER_CODES.get(code, '不明'), '最高気温C': item('temperature_2m_max'),
                '最低気温C': item('temperature_2m_min'), '降水確率%': item('precipitation_probability_max')}

    code = current.get('weather_code')
    result = {'ok': True, '場所': place['label'],
              '現在': {'天気': WEATHER_CODES.get(code, '不明'), '気温C': current.get('temperature_2m'),
                     '体感C': current.get('apparent_temperature'), '降水mm': current.get('precipitation')},
              '今日': day(0), '明日': day(1), '取得元': 'Open-Meteo'}
    _WEATHER_CACHE[key] = (time.monotonic() + 1800, result)
    return result
