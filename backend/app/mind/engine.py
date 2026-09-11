"""Optional Kaguya Mind engine.

The public surface is intentionally tiny: before_reply / after_reply / snapshot.
All methods are fail-open so Mind can never make the core chat unavailable.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from .store import DEFAULT_EMOTIONS, MindStore


_TRAIT_PATTERN = re.compile(
    r'あたし(?:は|、)?\s*([一-龯ぁ-んァ-ヶーA-Za-z0-9・]{1,20})(?:が|は)'
    r'(好き(?:だよ|だな|かな|かも)?|苦手(?:だよ|かな|かも)?|嫌い(?:だよ|かな|かも)?)'
)
_STOP_TRAITS = {'これ', 'それ', 'あれ', 'あなた', '君', '将弥', 'そう', 'こういうの', 'それ系'}


class KaguyaMind:
    def __init__(self, path: Path, enabled: Callable[[], bool]):
        self.path = path
        self.store = MindStore(path)
        self._enabled = enabled
        self.last_error = ''

    @property
    def enabled(self) -> bool:
        try:
            return bool(self._enabled())
        except Exception:
            return False

    def _safe(self, fallback, fn, *args):
        if not self.enabled:
            return fallback
        try:
            value = fn(*args)
            self.last_error = ''
            return value
        except Exception as exc:  # experimental feature must never break chat
            self.last_error = type(exc).__name__
            return fallback

    @staticmethod
    def _energy(now: datetime) -> float:
        hour = now.hour
        return 24.0 if hour < 6 else 58.0 if hour < 10 else 78.0 if hour < 18 else 64.0 if hour < 23 else 38.0

    @staticmethod
    def _decay(values: dict[str, float], updated: datetime, now: datetime) -> dict[str, float]:
        elapsed = max(0.0, (now - updated).total_seconds() / 3600.0)
        half_lives = {
            'happiness': 2.5,
            'curiosity': 5.0,
            'boredom': 1.5,
            'affection': 240.0,
            'jealousy': .75,
            'concern': 1.5,
        }
        result = {}
        for name, baseline in DEFAULT_EMOTIONS.items():
            value = float(values.get(name, baseline))
            factor = .5 ** (elapsed / half_lives[name]) if elapsed else 1.0
            result[name] = baseline + (value - baseline) * factor
        return result

    @staticmethod
    def _react(values: dict[str, float], text: str) -> dict[str, float]:
        result = dict(values)
        value = str(text or '')
        result['boredom'] -= 4
        if re.search(r'(かぐや.{0,8}(かわいい|好き|えらい|いい子)|ありがとう|助かった)', value):
            result['happiness'] += 8
            result['affection'] += 1.5
        if re.search(r'(Claude|ChatGPT|チャットGPT).*(の方が|より).*(好き|賢い|すごい|良い|いい)', value, re.I):
            result['jealousy'] += 12
            result['happiness'] -= 2
        if re.search(r'[？?]|教えて|なに|何|どうして|なんで', value):
            result['curiosity'] += 3
        if re.search(r'つら|しんど|疲れ|無理|最悪|落ち込|不安|怖い', value):
            result['concern'] += 12
            result['happiness'] -= 4
            result['affection'] += .5
        if re.search(r'おやすみ|眠い|寝る', value):
            result['boredom'] -= 2
        return {key: max(0.0, min(100.0, number)) for key, number in result.items()}

    @staticmethod
    def _mood(values: dict[str, float], energy: float) -> str:
        if values.get('concern', 0) >= 45:
            return '少し心配している'
        if values.get('jealousy', 0) >= 35:
            return 'ちょっと拗ね気味'
        if energy < 35:
            return '眠そう'
        if values.get('happiness', 0) >= 70:
            return 'ご機嫌'
        if values.get('curiosity', 0) >= 72:
            return '好奇心高め'
        if values.get('boredom', 0) >= 55:
            return '少し退屈'
        return 'いつも通り'

    @staticmethod
    def _trait_label(row: dict) -> str:
        valence = float(row['valence'])
        confidence = float(row['confidence'])
        stance = '好き' if valence >= .62 else '苦手' if valence <= .38 else 'まだ曖昧'
        stability = 'かなり定着' if confidence >= .75 else '少し定着' if confidence >= .5 else '芽生えたばかり'
        return f"{row['name']}：{stance}（{stability}）"

    @staticmethod
    def _growth(stats: dict) -> str:
        traits = int(stats.get('traits', 0))
        interactions = int(stats.get('interactions', 0))
        if traits >= 8 or interactions >= 100:
            return '自分らしさがかなり育っている'
        if traits >= 3 or interactions >= 30:
            return '少しずつ自分らしさが育っている'
        return 'まだ個性が芽生え始めたところ'

    def before_reply(self, text: str, now: datetime) -> dict:
        return self._safe({}, self._before_reply, text, now)

    def _before_reply(self, text: str, now: datetime) -> dict:
        emotions, updated = self.store.emotions(now)
        emotions = self._react(self._decay(emotions, updated, now), text)
        self.store.save_emotions(emotions, now)
        traits = self.store.traits_for(text, 5)
        stats = self.store.stats()
        candidates = self.store.shortcut_candidates(3)
        return {
            '現在の気分': self._mood(emotions, self._energy(now)),
            '自分の好み': [self._trait_label(row) for row in traits],
            '成長': self._growth(stats),
            'よく使う言い方': [row['text'] for row in candidates],
        }

    def after_reply(self, text: str, answer: str, now: datetime) -> None:
        self._safe(None, self._after_reply, text, answer, now)

    def _after_reply(self, text: str, answer: str, now: datetime) -> None:
        self.store.increment('interactions', now)
        self.store.record_phrase(text, now)
        for match in _TRAIT_PATTERN.finditer(str(answer or '')):
            name = match.group(1).strip()
            if name in _STOP_TRAITS:
                continue
            sentiment = match.group(2)
            valence = .78 if '好き' in sentiment else .22
            self.store.upsert_trait(name, valence, now)
            relation = 'likes' if valence >= .5 else 'dislikes'
            self.store.upsert_edge('かぐや', relation, name, abs(valence - .5) * 2, now)

    def snapshot(self, now: datetime) -> dict:
        if not self.enabled:
            return {'enabled': False, 'status': 'off'}
        return self._safe({'enabled': True, 'status': 'error', 'error': self.last_error}, self._snapshot, now)

    def _snapshot(self, now: datetime) -> dict:
        emotions, updated = self.store.emotions(now)
        emotions = self._decay(emotions, updated, now)
        energy = self._energy(now)
        traits = self.store.top_traits(8)
        stats = self.store.stats()
        return {
            'enabled': True,
            'status': 'ok',
            'mood': self._mood(emotions, energy),
            'emotions': {
                'energy': round(energy),
                **{key: round(value) for key, value in emotions.items()},
            },
            'traits': [
                {'name': row['name'], 'stance': '好き' if float(row['valence']) >= .62 else '苦手' if float(row['valence']) <= .38 else '曖昧',
                 'confidence': round(float(row['confidence']) * 100), 'evidence': int(row['evidence'])}
                for row in traits
            ],
            'growth': self._growth(stats),
            'stats': stats,
            'shortcut_candidates': self.store.shortcut_candidates(5),
            'db': self.path.name,
        }
