"""接し方の行を、画面から足したり消したりできること。

足せるのは自分で作った行だけで、最初から入っている行とシステムが書く行は
それぞれ「消せない」「触れない」。想起に載る行数の上限もここで守る。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import main, memory_store
from app.errors import ChatError

import pgtemp


class PersonaRowApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.app.dependency_overrides[main.require_session] = lambda: uuid4()
        self.addCleanup(main.app.dependency_overrides.pop, main.require_session)
        # lifespanを起動せずに経路だけを見る。会話中の待避と editing の解除を
        # 確かめたいので、状態を持つところだけ用意する。
        self.controller = SimpleNamespace(active=None, unsaved=None, editing=False,
                                          broadcast=AsyncMock(),
                                          memory=SimpleNamespace(call=AsyncMock(return_value={'ok': True})))
        main.app.state.controller = self.controller
        self.addCleanup(main.app.state._state.pop, 'controller', None)

    def test_a_new_row_needs_a_confirmed_valid_name_before_it_reaches_the_database(self):
        with patch.object(main, 'memory_request', new_callable=AsyncMock) as request:
            request.return_value = {'ok': True, 'key': 'work_context'}
            for body in ({'key': 'work_context', 'value': '仕事の前提'},          # 確認なし
                         {'key': 'Work Context', 'value': '仕事の前提', 'confirmed': True},
                         {'key': 'work-context', 'value': '仕事の前提', 'confirmed': True},
                         {'key': 'work_context', 'value': '', 'confirmed': True},
                         {'key': 'work_context', 'value': 'あ' * 401, 'confirmed': True}):
                with self.subTest(body=body):
                    self.assertEqual(self.client.post('/memories/persona', json=body).status_code, 422)
            request.assert_not_awaited()
            ok = self.client.post('/memories/persona',
                                  json={'key': 'work_context', 'value': '仕事の前提', 'confirmed': True})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(request.call_args.args[1:], ('POST', '/browse/persona/create'))
            self.assertEqual(request.call_args.kwargs['json'], {'key': 'work_context', 'value': '仕事の前提'})

    def test_adding_waits_for_the_conversation_to_finish(self):
        self.controller.active = {'turn_id': 'busy'}
        with patch.object(main, 'memory_request', new_callable=AsyncMock) as request:
            response = self.client.post('/memories/persona',
                                        json={'key': 'work_context', 'value': '仕事の前提', 'confirmed': True})
            self.assertEqual(response.status_code, 409)
            request.assert_not_awaited()
        self.controller.active = None
        # 足し終えたら編集中の札を必ず降ろす。降ろさないと会話が始められなくなる。
        with patch.object(main, 'memory_request', new_callable=AsyncMock) as request:
            request.return_value = {'ok': True, 'key': 'work_context'}
            self.client.post('/memories/persona',
                             json={'key': 'work_context', 'value': '仕事の前提', 'confirmed': True})
        self.assertFalse(self.controller.editing)

    def test_a_refused_name_reads_as_a_bad_input_not_a_broken_pc(self):
        """重複や上限は入力の問題。502を返すと接続の不調として案内してしまう。"""
        # 断り方の置き換えそのものを見るので、内部APIの手前までは本物を通す。
        self.controller.memory.call.side_effect = ChatError('invalid_request', 'その項目名はすでにあります。')
        response = self.client.post('/memories/persona',
                                    json={'key': 'work_context', 'value': '仕事の前提', 'confirmed': True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'その項目名はすでにあります。')

    def test_the_rows_the_system_writes_are_not_editable_from_the_screen(self):
        with patch.object(main, 'memory_request', new_callable=AsyncMock) as request:
            for key in memory_store.PERSONA_SYSTEM_KEYS:
                with self.subTest(key=key):
                    self.assertEqual(self.client.get(f'/memories/persona/{key}/impact').status_code, 422)
            request.assert_not_awaited()


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class PersonaRowDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = pgtemp.database()
        self.addCleanup(self.db.close)
        # persona_character は共用DBに残る表で、空にして使うテストもある。
        # 元の中身を控えてから初期行を入れ直し、終わったら元へ戻す。
        with self.db.session() as conn:
            self.saved = conn.execute('SELECT key,value,locked FROM persona_character').fetchall()
        self.addCleanup(self.restore)
        self.write(({'key': key, 'value': '内容', 'locked': True}
                    for key in memory_store.PERSONA_KEYS))

    def restore(self):
        self.write(self.saved)

    def write(self, rows):
        with self.db.session() as conn:
            conn.execute('DELETE FROM persona_character')
            for row in rows:
                conn.execute('INSERT INTO persona_character(key,value,locked) VALUES (%s,%s,%s)',
                             (row['key'], Jsonb(row['value']), row['locked']))

    def test_a_new_row_is_locked_and_reaches_the_next_prompt(self):
        with self.db.session() as conn:
            memory_store.create_persona(conn, 'work_context', '平日の日中は仕事中で、返事が遅くなる。')
            row = conn.execute("SELECT * FROM persona_character WHERE key='work_context'").fetchone()
            self.assertTrue(row['locked'])          # 自動整理に書き換えさせない
            self.assertEqual(row['revision'], 1)
            self.assertIsNone(row['previous_value'])
            # 想起に載らなければ、足しても返答は何も変わらない。
            keys = {item['key'] for item in memory_store.recall(conn, '仕事の話')['persona']}
            self.assertIn('work_context', keys)

    def test_the_same_name_twice_and_a_reserved_name_are_refused(self):
        with self.db.session() as conn:
            memory_store.create_persona(conn, 'work_context', '仕事の前提')
            for key in ('work_context', 'base_personality', 'style_feedback', 'Work', 'work-context', 'w'):
                with self.subTest(key=key), self.assertRaises(HTTPException):
                    memory_store.create_persona(conn, key, '内容')

    def test_rows_stop_at_the_number_the_prompt_can_carry(self):
        limit = memory_store.PERSONA_MAX_ROWS
        with self.db.session() as conn:
            start = conn.execute('SELECT count(*) AS n FROM persona_character').fetchone()['n']
            for number in range(limit - start):
                memory_store.create_persona(conn, f'extra_{number}', '内容')
            with self.assertRaises(HTTPException) as refused:
                memory_store.create_persona(conn, 'one_too_many', '内容')
            self.assertEqual(refused.exception.status_code, 400)
            # 想起が渡せる行数と同じ。ここがずれると、足した行が名前順で黙って落ちる。
            self.assertEqual(len(memory_store.recall(conn, '話')['persona']), limit)

    def test_only_rows_you_added_can_be_deleted(self):
        with self.db.session() as conn:
            memory_store.create_persona(conn, 'work_context', '仕事の前提')
            first = conn.execute("SELECT revision FROM persona_character WHERE key='work_context'").fetchone()
            memory_store.mutate(conn, 'persona', 'work_context', {'revision': first['revision']}, delete=True)
            self.assertIsNone(conn.execute("SELECT 1 FROM persona_character WHERE key='work_context'").fetchone())
            core = conn.execute("SELECT revision FROM persona_character WHERE key='base_personality'").fetchone()
            with self.assertRaises(HTTPException) as refused:
                memory_store.mutate(conn, 'persona', 'base_personality',
                                    {'revision': core['revision']}, delete=True)
            self.assertEqual(refused.exception.status_code, 400)
            self.assertIsNotNone(conn.execute("SELECT 1 FROM persona_character WHERE key='base_personality'").fetchone())

    def test_the_screen_is_told_which_rows_it_may_change_or_remove(self):
        with self.db.session() as conn:
            memory_store.create_persona(conn, 'work_context', '仕事の前提')
            memory_store.set_style(conn, 'もっと短く返す。')
            rows = {row['key']: row for row in memory_store.list_memories(conn, 'persona')['items']}
        self.assertEqual((rows['work_context']['editable'], rows['work_context']['removable']), (True, True))
        self.assertEqual((rows['base_personality']['editable'], rows['base_personality']['removable']), (True, False))
        self.assertEqual((rows['style_feedback']['editable'], rows['style_feedback']['removable']), (False, False))


if __name__ == '__main__':
    unittest.main()
