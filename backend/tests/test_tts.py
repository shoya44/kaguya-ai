"""PC側の読み上げエンジンとのやりとり。実際のエンジンには接続しない。"""
import asyncio
import io
import json
import unittest
import wave

import httpx

from app import tts

SPEAKERS = [
    {'name': 'アナウンサー', 'styles': [{'name': 'ノーマル', 'id': 3}]},
    {'name': 'コハク', 'styles': [{'name': 'ノーマル', 'id': 1512984320}, {'name': 'あまあま', 'id': 7}]},
]


def wav(frames: int = 240, rate: int = tts.SAMPLE_RATE, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(rate)
        stream.writeframes(b'\x01\x00' * frames * channels)
    return buffer.getvalue()


class Engine:
    """VOICEVOX互換エンジンの最低限の真似。受けた要求を記録する。"""

    def __init__(self, speakers=SPEAKERS, fail=''):
        self.speakers = speakers
        self.fail = fail
        self.queries = []
        self.synthesis = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError(self.fail)
        if request.url.path == '/speakers':
            return httpx.Response(200, json=self.speakers)
        if request.url.path == '/audio_query':
            self.queries.append(dict(request.url.params))
            return httpx.Response(200, json={'accent_phrases': [], 'outputSamplingRate': 44100,
                                             'outputStereo': True, 'speedScale': 1.0})
        if request.url.path == '/synthesis':
            self.synthesis.append(json.loads(request.content))
            return httpx.Response(200, content=wav())
        return httpx.Response(404)

    def speech(self, speaker='コハク', style='ノーマル', url='http://127.0.0.1:10101'):
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        return client, tts.Speech(client, url, speaker, style)


class SpeakerLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_id_comes_from_the_engine_and_is_looked_up_once(self):
        engine = Engine()
        client, speech = engine.speech()
        self.addAsyncCleanup(client.aclose)
        self.assertEqual(await speech.speaker_id(), 1512984320)
        engine.fail = '2回目は引かない'
        self.assertEqual(await speech.speaker_id(), 1512984320)

    async def test_a_missing_speaker_names_what_the_engine_has(self):
        client, speech = Engine().speech(speaker='いない人')
        self.addAsyncCleanup(client.aclose)
        with self.assertRaises(tts.SpeechError) as caught:
            await speech.speaker_id()
        self.assertIn('いない人', str(caught.exception))
        self.assertIn('コハク', str(caught.exception))

    async def test_an_unreachable_engine_says_so(self):
        client, speech = Engine(fail='refused').speech()
        self.addAsyncCleanup(client.aclose)
        with self.assertRaises(tts.SpeechError) as caught:
            await speech.speaker_id()
        self.assertIn('127.0.0.1:10101', str(caught.exception))


class SynthesisTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_output_format_is_forced_to_what_the_screen_plays(self):
        engine = Engine()
        client, speech = engine.speech()
        self.addAsyncCleanup(client.aclose)
        audio = await speech.say('おかえり。')
        # エンジン既定は44.1kHz・ステレオでも、画面が再生できる形に揃える。
        self.assertEqual(engine.synthesis[0]['outputSamplingRate'], tts.SAMPLE_RATE)
        self.assertFalse(engine.synthesis[0]['outputStereo'])
        # WAVヘッダを外した生のPCMが返る。
        self.assertEqual(len(audio), 240 * 2)
        self.assertEqual(engine.queries[0]['text'], 'おかえり。')

    async def test_a_wrong_format_is_reported_instead_of_played(self):
        with self.assertRaises(tts.SpeechError):
            tts.pcm(wav(rate=44100))
        with self.assertRaises(tts.SpeechError):
            tts.pcm(wav(channels=2))

    async def test_long_text_is_split_instead_of_being_cut_off(self):
        # 句読点が来ないまま伸びても、読み上げから欠落させない。
        ready, rest = tts.sentences('あ' * 450)
        self.assertEqual([len(text) for text in ready], [tts.MAX_TEXT, tts.MAX_TEXT])
        self.assertEqual(sum(len(text) for text in ready) + len(rest), 450)

    async def test_empty_text_does_not_call_the_engine(self):
        engine = Engine()
        client, speech = engine.speech()
        self.addAsyncCleanup(client.aclose)
        self.assertEqual(await speech.say('   '), b'')
        self.assertEqual(engine.queries, [])


class NarratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = Engine()
        self.client, self.speech = self.engine.speech()
        self.addAsyncCleanup(self.client.aclose)
        self.sent = []
        self.narrator = tts.Narrator(self.speech, self.send)
        self.narrator.start()
        self.addAsyncCleanup(self.narrator.close)

    async def send(self, audio):
        self.sent.append(audio)

    async def settle(self):
        await self.narrator.queue.join()
        await asyncio.sleep(0)

    async def test_each_finished_sentence_is_read_without_waiting_for_the_rest(self):
        self.narrator.feed('おかえり。')
        await self.settle()
        self.assertEqual([q['text'] for q in self.engine.queries], ['おかえり。'])
        # 句点が来ていない分は、まだ読まない。
        self.narrator.feed('今日は')
        await self.settle()
        self.assertEqual(len(self.engine.queries), 1)
        self.narrator.feed('どうだった？')
        await self.settle()
        self.assertEqual([q['text'] for q in self.engine.queries], ['おかえり。', '今日はどうだった？'])
        self.assertEqual(len(self.sent), 2)

    async def test_the_tail_is_read_when_the_answer_ends(self):
        self.narrator.feed('うん、わかった')
        self.narrator.flush()
        await self.settle()
        self.assertEqual([q['text'] for q in self.engine.queries], ['うん、わかった'])

    async def test_interrupting_drops_what_has_not_been_read_yet(self):
        self.narrator.feed('ひとつめ。ふたつめ。みっつめ。')
        self.narrator.stop()
        await self.settle()
        self.assertEqual(self.sent, [])
        # 割り込み後の発話は、また読み上げる。
        self.narrator.feed('やりなおし。')
        await self.settle()
        self.assertEqual([q['text'] for q in self.engine.queries], ['やりなおし。'])

    async def test_a_failing_engine_records_the_reason_and_keeps_going(self):
        self.engine.fail = 'engine down'
        self.narrator.feed('よろしく。')
        await self.settle()
        self.assertIn('読み上げ', self.narrator.error)
        self.assertEqual(self.sent, [])
        self.engine.fail = ''
        self.narrator.error = ''
        self.narrator.feed('もう一度。')
        await self.settle()
        self.assertEqual(len(self.sent), 1)


class LiveConfigTests(unittest.TestCase):
    """PC側で読み上げる場合も、Liveへ渡す設定は音声出力のまま。"""

    def test_text_only_output_is_never_requested(self):
        from google.genai import types
        from app.voice import live_config
        config = live_config('指示', 'Leda')
        # ネイティブ音声モデルはTEXTのみの出力を 1007 で拒否する。
        self.assertEqual(config['response_modalities'], ['AUDIO'])
        # 読み上げの元になる書き起こしを必ず受け取る。
        self.assertIn('output_audio_transcription', config)
        self.assertIn('input_audio_transcription', config)
        # SDKが受理する形であること。
        types.LiveConnectConfig.model_validate(config)


class FailureMessageTests(unittest.TestCase):
    """通話が続けられなくなったとき、画面に原因が出る。ただし鍵は出さない。"""

    def test_the_kind_of_failure_is_shown(self):
        from app.voice import _reason
        self.assertEqual(_reason(ValueError('返答が長すぎます。')), 'ValueError: 返答が長すぎます。')
        # 文面が無い例外でも、種類だけで切り分けの役に立つ。
        self.assertEqual(_reason(httpx.ConnectError('')), 'ConnectError')

    def test_an_api_key_in_the_message_is_hidden(self):
        from app.voice import _reason
        # Live APIのURLにはキーが載る。例外文ごと画面へ出すので必ず消す。
        for text in ('wss://host/ws?key=AIzaSySECRET&alt=1',
                     'Authorization=Bearer-SECRET failed',
                     'token=SECRET'):
            with self.subTest(text=text):
                shown = _reason(RuntimeError(text))
                self.assertNotIn('SECRET', shown)
                self.assertIn('=***', shown)

    def test_the_message_stays_short_enough_to_read(self):
        from app.voice import _reason
        self.assertLessEqual(len(_reason(RuntimeError('あ' * 1000))), 320)


if __name__ == '__main__':
    unittest.main()
