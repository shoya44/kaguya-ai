import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app import main, memory_store
import pgtemp


class MindBrowseApiTests(unittest.TestCase):
    def test_mind_uses_existing_authenticated_memory_route(self):
        client = TestClient(main.app)
        self.assertEqual(client.get('/memories/mind').status_code, 401)
        main.app.dependency_overrides[main.require_session] = lambda: uuid4()
        self.addCleanup(main.app.dependency_overrides.pop, main.require_session)
        with patch.object(main, 'memory_request', new_callable=AsyncMock) as request:
            request.return_value = {'items': [], 'next_offset': None}
            self.assertEqual(client.get('/memories/mind?q=test&offset=30').status_code, 200)
            self.assertEqual(request.call_args.args[1:], ('GET', '/browse/mind'))
            self.assertEqual(request.call_args.kwargs['params'], {'q': 'test', 'offset': 30})
            self.assertEqual(client.get('/memories/unknown').status_code, 422)
            self.assertEqual(client.get('/memories/mind?offset=-1').status_code, 422)
            self.assertEqual(client.patch('/memories/mind/test', json={}).status_code, 422)


@unittest.skipUnless(pgtemp.available(), pgtemp.reason())
class MindBrowseDatabaseTests(unittest.TestCase):
    def test_all_tables_search_paging_and_read_only(self):
        db = pgtemp.database()
        self.addCleanup(db.close)
        with db.session() as conn:
            self.assertEqual(memory_store.list_memories(conn, 'mind')['items'], [])
            conn.execute("INSERT INTO mind_emotions VALUES ('joy',50,now())")
            conn.execute("INSERT INTO mind_traits VALUES ('coffee',0.8,0.7,3,now())")
            conn.execute("INSERT INTO mind_phrases VALUES ('ＧｉｔＨｕｂ',2,now())")
            conn.execute("INSERT INTO mind_graph_edges VALUES ('user','likes','coffee',0.8,now())")
            conn.execute("INSERT INTO mind_open_loops VALUES ('exam','event','tomorrow',now(),now(),NULL,0,NULL)")
            conn.execute("INSERT INTO mind_meta VALUES ('interactions','4',now())")
            result = memory_store.list_memories(conn, 'mind')
            self.assertEqual({row['category'] for row in result['items']},
                             {'emotions', 'traits', 'phrases', 'graph_edges', 'open_loops', 'meta'})
            self.assertIsNone(result['next_offset'])
            self.assertEqual(len(memory_store.list_memories(conn, 'mind', 'github')['items']), 1)
            conn.execute("INSERT INTO mind_meta SELECT 'item-' || n, n::text, now() FROM generate_series(1,35) n")
            first = memory_store.list_memories(conn, 'mind')
            second = memory_store.list_memories(conn, 'mind', offset=first['next_offset'])
            self.assertEqual(len(first['items']), 30)
            self.assertEqual(len(second['items']), 11)
            self.assertIsNone(second['next_offset'])
            self.assertEqual(conn.execute('SELECT asked,last_asked_at FROM mind_open_loops').fetchone(),
                             {'asked': 0, 'last_asked_at': None})
