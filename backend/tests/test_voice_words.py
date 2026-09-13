import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app import voice_words
from app.voice import Transcript, live_config


class VoiceWordsTests(unittest.IsolatedAsyncioTestCase):
    def test_only_clear_addresses_are_corrected(self):
        for raw, expected in [('家具屋', 'かぐや'), ('家具屋！', 'かぐや！'),
                              ('家具屋、おはよう', 'かぐや、おはよう'),
                              ('ねえ、家具屋、聞いて', 'ねえ、かぐや、聞いて')]:
            self.assertEqual(voice_words.transcript_text(raw), expected)
        for raw in ('家具屋で椅子を買った', '家具屋の場所を教えて', '家具屋さんに行く',
                    'あの家具屋、よかったね', '家具屋、ソファを探しているんだけど'):
            self.assertEqual(voice_words.transcript_text(raw), raw)

    def test_accumulated_chunks_are_reconsidered_without_corrupting_raw_input(self):
        raw = '家具'
        self.assertEqual(voice_words.transcript_text(raw), '家具')
        raw += '屋'
        self.assertEqual(voice_words.transcript_text(raw), 'かぐや')
        raw += 'で買った'
        self.assertEqual(voice_words.transcript_text(raw), '家具屋で買った')

    def test_live_config_contains_reading_and_name_guidance(self):
        config = live_config('会話の方針', 'Leda')
        self.assertIn('「朝会」は「あさかい」', config['system_instruction'])
        self.assertIn('名前は「かぐや」', config['system_instruction'])
        self.assertEqual(config['response_modalities'], ['AUDIO'])

    async def test_corrected_input_is_saved_and_broadcast_but_answer_keeps_kanji(self):
        controller = SimpleNamespace(memory=SimpleNamespace(begin=AsyncMock(), complete=AsyncMock()),
                                     broadcast=AsyncMock(), living=None, mind=None, runtime=None)
        transcript = Transcript(controller, 'test-client')
        transcript.text, transcript.answer = '家具屋、おはよう', '朝会があるんだね。'
        await transcript.save()
        self.assertEqual(controller.memory.begin.call_args.args[0]['text'], 'かぐや、おはよう')
        self.assertEqual(controller.memory.complete.call_args.args[1], '朝会があるんだね。')
        self.assertEqual(controller.broadcast.call_args.args[0]['text'], 'かぐや、おはよう')
