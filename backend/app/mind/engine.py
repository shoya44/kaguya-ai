"""Optional Kaguya Mind engine.

The public surface is intentionally tiny: before_reply / after_reply / snapshot.
All methods are fail-open so Mind can never make the core chat unavailable.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from ..tuning import (BOREDOM_IDLE_FULL, BOREDOM_IDLE_MAX, BOREDOM_TALKING,
                      BOREDOM_TALKING_WITHIN, CONCERN_REACTION, CONCERN_REACTION_QUIET,
                      DISPOSITION_MAX, DISPOSITION_MIN_SAMPLES, DISPOSITION_RATIO, EMOTION_BASELINE, EMOTION_HALF_LIFE_HOURS, EMOTION_REACTION,
                      EMOTION_THRESHOLD, ENERGY_BY_HOUR, GROWTH_GROWING, GROWTH_GROWN,
                      LOOP_CONCERN_AFTER, LOOP_DUE_HOUR, LOOP_TODAY_AFTER, RECALL_IMPORTANT,
                      RECALL_IMPORTANT_REACTION, RECALL_REACTION, TRAIT_STABILITY, TRAIT_STANCE,
                      TRAIT_VALENCE)
from ..db import Database
from .store import MindStore


_log = logging.getLogger('uvicorn.error')


# 断定された自己申告だけを拾う。文末側の先読みで「好きじゃない」「好きって言ったら」
# のような否定・仮定・引用を除外する。ここを緩めると誤った好みが確信度付きで定着する。
_TRAIT_PATTERN = re.compile(
    r'あたし(?:は|、)?\s*([一-龯ぁ-んァ-ヶーA-Za-z0-9・]{1,20})(?:が|は)'
    r'(好き(?:だよ|だな|かな|かも)?|苦手(?:だよ|かな|かも)?|嫌い(?:だよ|かな|かも)?)'
    r'(?!じゃ|では|ない|なく|くない|そう|って|と言|という|かどうか|なら|たら|れば)'
    r'(?=[。．！？!?、,．\s]|$)'
)
_STOP_TRAITS = {'これ', 'それ', 'あれ', 'あなた', '君', 'しょうや', '将弥', 'そう', 'こういうの', 'それ系'}

# 未完の話題（open loop）の抽出。LLMを呼ばず、時間表現＋名詞＋予定を表す述語という
# 保守的な組み合わせだけを拾う。取りこぼしは許容し、誤検出を避ける側に倒している。
_PLAN_PATTERN = re.compile(
    r'(今日|今夜|今晩|明日|あした|あす|明後日|あさって|今週|週末|来週|再来週|来月'
    r'|[月火水木金土日]曜日?)'
    r'[^。．！？!?\n]{0,14}?'
    # 語中のひらがなは許すが、助詞を飲み込まないよう前後は漢字・カタカナ等に限る。
    r'([一-龯ァ-ヶーA-Za-z0-9々][一-龯ぁ-んァ-ヶーA-Za-z0-9々]{0,10}[一-龯ァ-ヶーA-Za-z0-9々])'
    r'(?:が|を|に|は|の)?\s*'
    r'(ある|行く|受ける|出す|提出|やら|やる|する|予定|控え|しなきゃ|しないと)'
)
_CONCERN_PATTERN = re.compile(
    r'(熱|頭痛|風邪|腹痛|体調|病院|検査|手術|寝不足)[^。．！？!?\n]{0,6}?'
    r'(悪い|痛い|出た|ひいた|ある|行く|受ける)'
)
_STOP_TOPICS = {'予定', '時間', '感じ', '自分', '本当', '一緒', '普通', '無理', '最近',
                '大丈夫', '状態', '状況', '内容', '場合', '自体', '以上', '以下'}
_OFFSET_DAYS = {'今日': 0, '今夜': 0, '今晩': 0, '明日': 1, 'あした': 1, 'あす': 1,
                '明後日': 2, 'あさって': 2, '今週': 3, '週末': 3, '来週': 7,
                '再来週': 14, '来月': 30}
_WEEKDAYS = {'月': 0, '火': 1, '水': 2, '木': 3, '金': 4, '土': 5, '日': 6}


def _quoted(text: str, index: int) -> bool:
    """引用符の内側なら、かぐや自身の発言ではないと判断する。"""
    head = text[:index]
    return any(head.count(open_mark) > head.count(close_mark)
               for open_mark, close_mark in (('「', '」'), ('『', '』')))


def _due_at(word: str, now: datetime) -> datetime:
    """予定が済んだ頃合い。過ぎてから初めて話題に出す。"""
    if word in _OFFSET_DAYS:
        days = _OFFSET_DAYS[word]
        if days == 0:
            return now + LOOP_TODAY_AFTER
    else:
        weekday = _WEEKDAYS.get(word[0], 0)
        days = (weekday - now.weekday()) % 7 or 7
    return (now + timedelta(days=days)).replace(hour=LOOP_DUE_HOUR, minute=0, second=0, microsecond=0)


class KaguyaMind:
    def __init__(self, db: Database, enabled: Callable[[], bool]):
        self.store = MindStore(db)
        self._enabled = enabled
        self.last_error = ''
        # 気がかりで心配になった最後の時刻。DBには置かない。心配の値そのものは
        # living_emotion にあり時間で減衰するので、再起動で忘れても差し障りがない。
        self.concerned_at = datetime.min.replace(tzinfo=timezone.utc)

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
            # 会話は続けるが、原因が追えないまま黙って無効化されないよう記録する。
            _log.warning('Kaguya Mind failed in %s', getattr(fn, '__name__', fn), exc_info=True)
            return fallback

    @staticmethod
    def _energy(now: datetime) -> float:
        return next(value for until, value in ENERGY_BY_HOUR if now.hour < until)

    @staticmethod
    def _decay(values: dict[str, float], updated: datetime, now: datetime) -> dict[str, float]:
        elapsed = max(0.0, (now - updated).total_seconds() / 3600.0)
        result = {}
        for name, baseline in EMOTION_BASELINE.items():
            value = float(values.get(name, baseline))
            factor = .5 ** (elapsed / EMOTION_HALF_LIFE_HOURS[name]) if elapsed else 1.0
            result[name] = baseline + (value - baseline) * factor
        return result

    def _recalled(self, values: dict[str, float], memories, concerns, now: datetime) -> dict[str, float]:
        """思い出したことで動く分。言葉づかいではなく「何を思い出したか」で決まる。

        memories は想起できた長期記憶の行。concerns は期限の来た気がかり。
        どちらも会話のたびに引いているものを受け取るだけで、DBは引き直さない。
        """
        result = dict(values)
        rows = [row for row in (memories or []) if isinstance(row, dict)]
        reactions = []
        if rows:
            # 覚えていることに触れられた。話題そのものへの関心が上がる。
            reactions.append(RECALL_REACTION)
        if any(int(row.get('importance') or 0) >= RECALL_IMPORTANT for row in rows):
            # その人の核心に近い話題。親しみと機嫌がいっしょに動く。
            reactions.append(RECALL_IMPORTANT_REACTION)
        # 気がかりは片付くまで毎ターン引き直される。そのたびに足していたため、
        # 1件残っているだけで心配が減衰を上回り、顔が固定されていた。
        # 思い出して心配になるのは一度きりにして、あとは話しながら落ち着かせる。
        if concerns and now - self.concerned_at >= CONCERN_REACTION_QUIET:
            self.concerned_at = now
            reactions.append(CONCERN_REACTION)
        for reaction in reactions:
            for key, delta in reaction.items():
                result[key] = result.get(key, 0.0) + delta
        return result

    @staticmethod
    def _idled(values: dict[str, float], idle: timedelta) -> dict[str, float]:
        """話していない時間で動く退屈。放っておかれると溜まり、話すと紛れる。

        言われた言葉では動かさない。「暇だ」と言われて退屈するのではなく、
        誰も話しかけてこない時間そのものが退屈だから。
        """
        result = dict(values)
        if idle < BOREDOM_TALKING_WITHIN:
            result['boredom'] = result.get('boredom', 0.0) + BOREDOM_TALKING
            return result
        share = min(1.0, idle / BOREDOM_IDLE_FULL)
        result['boredom'] = result.get('boredom', 0.0) + BOREDOM_IDLE_MAX * share
        return result

    @staticmethod
    def _react(values: dict[str, float], text: str) -> dict[str, float]:
        result = dict(values)
        value = str(text or '')
        # 増減の値はtuning.EMOTION_REACTION、拾う言葉はここ、と役割を分ける。
        fired = []
        if re.search(r'(かぐや.{0,8}(かわいい|好き|えらい|いい子)|ありがとう|助かった)', value):
            fired.append('praised')
        if re.search(r'(Claude|ChatGPT|チャットGPT).*(の方が|より).*(好き|賢い|すごい|良い|いい)', value, re.I):
            fired.append('compared')
        # 「何時」「何回」「何か」まで拾うと、ほぼ全ての発言が質問扱いになり、
        # 好奇心が上限に張り付いて「好奇心高め」から動かなくなる。問いかけの形を
        # はっきり持つ言い方だけにする。
        if re.search(r'[？?]|教えて|どうして|なんで|なぜ|どう思う|知ってる', value):
            fired.append('asked')
        if re.search(r'つら|しんど|疲れ|無理|最悪|落ち込|不安|怖い', value):
            fired.append('worried')
        for name in fired:
            for key, delta in EMOTION_REACTION[name].items():
                result[key] = result.get(key, 0.0) + delta
        return {key: max(0.0, min(100.0, number)) for key, number in result.items()}

    # よくある状態を、そのまま「その子らしさ」として言葉にする。
    DISPOSITIONS = {
        'happiness': 'ごきげんでいることが多い',
        'curiosity': '知りたがりで、いろいろ聞きたくなる',
        'boredom': '退屈しやすく、かまってほしくなる',
        'affection': '相手にすっかり懐いている',
        'jealousy': 'やきもちを焼きやすい',
        'concern': '心配性なところがある',
    }

    @staticmethod
    def _high(values: dict[str, float]) -> set:
        """いま「強い」と言える感情。気分ラベルと同じ境目を使う。"""
        return {name for name, value in values.items()
                if name in EMOTION_THRESHOLD and value >= EMOTION_THRESHOLD[name]}

    @classmethod
    def _disposition(cls, rows) -> list[str]:
        """回数の多い状態から、いくつかを性格として取り出す。

        まだ会話が少ないうちは何も言わない。少ない回数で性格を決めつけると、
        たまたまの機嫌がそのまま固定されてしまう。
        """
        ranked = []
        for row in rows or []:
            samples = int(row.get('samples') or 0)
            if samples < DISPOSITION_MIN_SAMPLES or row.get('name') not in cls.DISPOSITIONS:
                continue
            ratio = int(row.get('high_count') or 0) / samples
            if ratio >= DISPOSITION_RATIO:
                ranked.append((ratio, row['name']))
        ranked.sort(reverse=True)
        return [cls.DISPOSITIONS[name] for _, name in ranked[:DISPOSITION_MAX]]

    def disposition(self) -> str:
        """週次でまとめた「その子らしさ」。まだ決まらないうちは空文字。"""
        parts = self._safe([], lambda: self._disposition(self.store.counters()))
        return '。'.join(parts) + '。' if parts else ''

    @staticmethod
    def _mood(values: dict[str, float], energy: float) -> str:
        if values.get('concern', 0) >= EMOTION_THRESHOLD['concern']:
            return '少し心配している'
        if values.get('jealousy', 0) >= EMOTION_THRESHOLD['jealousy']:
            return 'ちょっと拗ね気味'
        if energy < EMOTION_THRESHOLD['energy_sleepy']:
            return '眠そう'
        if values.get('happiness', 0) >= EMOTION_THRESHOLD['happiness']:
            return 'ご機嫌'
        if values.get('curiosity', 0) >= EMOTION_THRESHOLD['curiosity']:
            return '好奇心高め'
        if values.get('boredom', 0) >= EMOTION_THRESHOLD['boredom']:
            return '少し退屈'
        return 'いつも通り'

    @staticmethod
    def _expression(values: dict[str, float], energy: float) -> str:
        """画面のかぐやの表情。_moodと同じ優先順で、同じ境目を使う。

        以前は心配・退屈がnormalへ落ちていたため、気分ラベルは「少し心配している」
        なのに顔は普段どおり、という食い違いが出ていた。判定を_moodと揃える。
        好奇心は表情として分かりにくいのでnormalのままにする。"""
        if values.get('concern', 0) >= EMOTION_THRESHOLD['concern']:
            return 'worried'
        if values.get('jealousy', 0) >= EMOTION_THRESHOLD['jealousy']:
            return 'sulky'
        if energy < EMOTION_THRESHOLD['energy_sleepy']:
            return 'sleepy'
        if values.get('happiness', 0) >= EMOTION_THRESHOLD['happiness']:
            return 'happy'
        if values.get('boredom', 0) >= EMOTION_THRESHOLD['boredom']:
            return 'bored'
        return 'normal'

    def face(self, now: datetime) -> str:
        """OFF時と失敗時は空文字。画面側は従来どおりローカル判定へ落ちる。"""
        return self._safe('', self._face, now)

    def _face(self, now: datetime) -> str:
        emotions, updated = self.store.emotions(now)
        return self._expression(self._decay(emotions, updated, now), self._energy(now))

    @staticmethod
    def _trait_label(row: dict) -> str:
        valence = float(row['valence'])
        confidence = float(row['confidence'])
        stance = ('好き' if valence >= TRAIT_STANCE['like']
                  else '苦手' if valence <= TRAIT_STANCE['dislike'] else 'まだ曖昧')
        stability = ('かなり定着' if confidence >= TRAIT_STABILITY['settled']
                     else '少し定着' if confidence >= TRAIT_STABILITY['forming'] else '芽生えたばかり')
        return f"{row['name']}：{stance}（{stability}）"

    @staticmethod
    def _growth(stats: dict) -> str:
        traits = int(stats.get('traits', 0))
        interactions = int(stats.get('interactions', 0))
        if traits >= GROWTH_GROWN['traits'] or interactions >= GROWTH_GROWN['interactions']:
            return '自分らしさがかなり育っている'
        if traits >= GROWTH_GROWING['traits'] or interactions >= GROWTH_GROWING['interactions']:
            return '少しずつ自分らしさが育っている'
        return 'まだ個性が芽生え始めたところ'

    @staticmethod
    def _loop_label(row: dict, now: datetime) -> str:
        opened = datetime.fromisoformat(row['opened_at'])
        elapsed = max(0, (now - opened).days)
        when = '今日' if elapsed == 0 else '昨日' if elapsed == 1 else f'{elapsed}日前'
        kind = '気にしていた体調' if row['kind'] == 'concern' else '聞いていた予定'
        return f"{row['topic']}（{when}に{kind}。その後どうなったかはまだ聞けていない）"

    def _track_loops(self, text: str, answer: str, now: datetime) -> None:
        value = str(text or '')
        from ..reply_hints import concern_status
        # 話題に戻ってきただけでは片付いていない。「終わった」と言われたときだけ閉じる。
        for topic in self.store.unresolved_topics():
            if concern_status(topic, value) == 'resolved':
                self.store.resolve_loop(topic, now)
        for match in _PLAN_PATTERN.finditer(value):
            topic = match.group(2).strip()
            if topic in _STOP_TOPICS or topic.isdigit():
                continue
            self.store.open_loop(topic, 'plan', match.group(0), now, _due_at(match.group(1), now))
        for match in _CONCERN_PATTERN.finditer(value):
            self.store.open_loop(match.group(1), 'concern', match.group(0), now,
                                 now + LOOP_CONCERN_AFTER)
        # かぐやが実際に触れた話題だけ、しつこさ防止のカウントを進める。
        reply = str(answer or '')
        for topic in self.store.unresolved_topics():
            if topic in reply:
                self.store.mark_asked(topic, now)
        self.store.prune_loops(now)
        self.store.prune_traits(now)

    def before_reply(self, text: str, now: datetime, recalled=None) -> dict:
        """recalled は /recall の結果。渡されたときだけ、思い出した分も感情へ足す。

        天気の即答やファイルタブへの引き継ぎでは記憶を引かないので、
        そのときは従来どおり言葉づかいだけで動く。
        """
        return self._safe({}, self._before_reply, text, now, recalled)

    def _before_reply(self, text: str, now: datetime, recalled=None) -> dict:
        emotions, updated = self.store.emotions(now)
        # 気がかりは感情の材料でもあるので、先に引いてから感情を決める。
        # 会話では recall がすでに同じ条件で引いているので、それを使い回す（追加クエリなし）。
        due = self.store.due_loops(now, 2) if recalled is None else (recalled.get('pending_topic') or [])
        memories = (recalled or {}).get('wisdom') or []
        idle = max(timedelta(0), now - updated)
        emotions = self._decay(emotions, updated, now)
        emotions = self._idled(self._react(emotions, text), idle)
        emotions = self._recalled(emotions, memories, due, now)
        emotions = {key: max(0.0, min(100.0, value)) for key, value in emotions.items()}
        self.store.save_emotions(emotions, now, self._high(emotions))
        traits = self.store.traits_for(text, 5)
        stats = self.store.stats()
        context = {
            '現在の気分': self._mood(emotions, self._energy(now)),
            '自分の好み': [self._trait_label(row) for row in traits],
            '成長': self._growth(stats),
        }
        # 会話ではプロンプトの「Memory：気にかけている話題」と重複するので出さない。
        loops = [self._loop_label(row, now) for row in due] if recalled is None else []
        if loops:
            context['気にかけていること'] = loops
        return context

    def due_topic(self, now: datetime) -> str:
        """自分から声をかけるための話題を1件だけ引き当てる。
        引き当てた時点で「聞いた」扱いにするので、同じ話題を会話側と二重に持ち出さない。"""
        return self._safe('', self._due_topic, now)

    def _due_topic(self, now: datetime) -> str:
        rows = self.store.due_loops(now, 1)
        if not rows:
            return ''
        topic = rows[0]['topic']
        self.store.mark_asked(topic, now)
        return topic

    def after_reply(self, text: str, answer: str, now: datetime) -> None:
        self._safe(None, self._after_reply, text, answer, now)

    def _after_reply(self, text: str, answer: str, now: datetime) -> None:
        # 会話回数は living_activity（かぐやの状態）が数える。
        self._track_loops(text, answer, now)
        value = str(answer or '')
        for match in _TRAIT_PATTERN.finditer(value):
            name = match.group(1).strip()
            if name in _STOP_TRAITS or _quoted(value, match.start()):
                continue
            sentiment = match.group(2)
            valence = TRAIT_VALENCE['like'] if '好き' in sentiment else TRAIT_VALENCE['dislike']
            self.store.upsert_trait(name, valence, now)

    def reset(self) -> None:
        """育ったものを消して最初からにする。OFFのときも削除できる。"""
        try:
            self.store.reset()
            self.last_error = ''
        except Exception as exc:
            self.last_error = type(exc).__name__
            _log.warning('Kaguya Mind reset failed', exc_info=True)
            raise

    def close(self) -> None:
        self.store.close()

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
                {'name': row['name'],
                 'stance': ('好き' if float(row['valence']) >= TRAIT_STANCE['like']
                            else '苦手' if float(row['valence']) <= TRAIT_STANCE['dislike'] else '曖昧'),
                 'confidence': round(float(row['confidence']) * 100), 'evidence': int(row['evidence'])}
                for row in traits
            ],
            'growth': self._growth(stats),
            'stats': {**stats, 'open_loops': self.store.loop_stats()['open']},
            'store': 'PostgreSQL (living_emotion / persona_favorite / memory_concern)',
        }
