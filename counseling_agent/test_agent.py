import sqlite3
import unittest
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import MagicMock

import json

from agent import Agent, LocalLLM, read_only, terms_for
from server import Server, configure_shared_graph


class FakeLLM:
    def models(self):
        return ['test-local']

    def answer(self, model, question, evidence, scope):
        return {'points': [{'text': '검토 전 원문을 확인해야 합니다.', 'quote': evidence[0]['text'][:80], 'sources': [evidence[0]['id']]}, {'text': '잘못된 인용', 'quote': evidence[0]['text'][:80], 'sources': ['S9999']}, {'text': '잘못된 원문 인용', 'quote': '이 문장은 실제 근거에 존재하지 않습니다.', 'sources': [evidence[0]['id']]}], 'unsupported': ['지원 가능 여부는 확정하지 않습니다.']}


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = Agent(llm=FakeLLM())

    def test_korean_search_is_scoped_and_citable(self):
        pages = self.agent.search_documents('컴퓨터 수학 권장과목', 2028, 'YSU')
        self.assertTrue(pages)
        self.assertTrue(all(page['university_id'] == 'YSU' for page in pages))
        self.assertTrue(all(not page['year_hints'] or 2028 in page['year_hints'] for page in pages))

    def test_old_guide_does_not_become_2027_source(self):
        pages = self.agent.search_documents('학생부종합', 2027, 'KU')
        self.assertFalse(any(2026 in page['year_hints'] for page in pages))

    def test_snapshot_is_read_only(self):
        connection = read_only(self.agent.snapshot)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute('DELETE FROM source_fragment')
        finally:
            connection.close()

    def test_answer_drops_invented_citation(self):
        result = self.agent.ask({'question': '연세대 권장과목 수학', 'model': 'test-local', 'year': 2028})
        self.assertTrue(result['evidence'])
        self.assertEqual(len(result['points']), 1)
        self.assertIn('미연결', result['trace'][1]['status'])

    def test_grade_conversion_is_blocked(self):
        result = self.agent.ask({'question': '연세대 교과 5등급을 9등급으로 환산 비교', 'model': 'test-local'})
        self.assertFalse(result['points'])
        self.assertTrue(any('환산' in message for message in result['unsupported']))

    def test_graph_query_receives_only_public_identifiers(self):
        documents = self.agent.search_documents('연세대 권장과목', 2028, 'YSU')
        client = MagicMock()
        client.query.return_value = {'results': [{'columns': ['university', 'document', 'fragment_id', 'page', 'quote'], 'data': [{'row': ['연세대학교', page['title'], page['fragment_id'], page['pdf_page'], page['quote']]} for page in documents]}]}
        self.agent.graph = client
        try:
            paths, status = self.agent.graph_paths(documents)
            self.assertEqual(len(paths), len(documents))
            self.assertIn('확인', status)
            parameters = client.query.call_args.args[1]
            self.assertEqual(set(parameters), {'owner', 'release', 'ids'})
            self.assertTrue(all(identifier.startswith('fragment:') for identifier in parameters['ids']))
        finally:
            self.agent.disconnect_graph()

    def test_historical_queries_preserve_metric_context(self):
        results = self.agent.historical_results('첨단컴퓨팅 입결', 'YSU')
        self.assertTrue(results)
        self.assertTrue(all(row['year'] == 2026 and row['admission_type'] in ('학생부교과', '학생부종합') for row in results))
        self.assertIn('population', results[0])
        self.assertIn('source_file', results[0])

    def test_unknown_pdf_is_blocked(self):
        with self.assertRaises(ValueError):
            self.agent.source_path('../secret')

    def test_inventory_includes_documents_without_text(self):
        self.assertEqual(len(self.agent.sources), 17)
        self.assertEqual(len(self.agent.pages), 505)

    def test_llm_selects_quote_id_without_rewriting_source(self):
        model = LocalLLM()
        model.call = MagicMock(return_value={'message': {'content': json.dumps({'points': [{'text': '설명', 'quote': 'S1.Q1', 'sources': ['S1']}], 'unsupported': []})}})
        text = '수학 교과와 과학 교과의 조건은 각각 원문 기준으로 확인한다.'
        result = model.answer('test-local', '질문', [{'id': 'S1', 'kind': 'document', 'title': '합성 자료', 'text': text}], {})
        self.assertEqual(result['points'][0]['quote'], text)
        self.assertEqual(model.call.call_args.args[1]['format']['properties']['points']['items']['properties']['quote']['enum'], ['S1.Q1'])

    def test_search_terms_are_plain_data(self):
        self.assertIn('수학', terms_for('연세대 수학 권장과목 알려줘'))
        self.assertEqual(self.agent.search_documents('zzzxxyyqqq', 2028), [])


class SharedGraphTests(unittest.TestCase):
    def test_credentials_are_configured_once_on_server(self):
        agent = MagicMock()
        self.assertTrue(configure_shared_graph(agent, {'NEO4J_USERNAME': 'reader', 'NEO4J_PASSWORD': 'synthetic-secret'}))
        agent.connect_graph.assert_called_once_with('reader', 'synthetic-secret')

    def test_secret_file_is_supported(self):
        agent = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / 'password'
            secret.write_text('synthetic-secret\n', encoding='utf-8')
            self.assertTrue(configure_shared_graph(agent, {'NEO4J_PASSWORD_FILE': str(secret)}))
        agent.connect_graph.assert_called_once_with('neo4j', 'synthetic-secret')

    def test_missing_configuration_does_not_attempt_authentication(self):
        agent = MagicMock()
        self.assertFalse(configure_shared_graph(agent, {}))
        agent.connect_graph.assert_not_called()

    def test_failed_authentication_does_not_leak_secret_or_continue(self):
        agent = MagicMock()
        agent.connect_graph.side_effect = RuntimeError('synthetic-secret')
        with self.assertRaises(RuntimeError) as raised:
            configure_shared_graph(agent, {'NEO4J_PASSWORD': 'synthetic-secret'})
        self.assertNotIn('synthetic-secret', str(raised.exception))
        with self.assertRaises(RuntimeError):
            configure_shared_graph(agent, {'NEO4J_PASSWORD': 'one', 'NEO4J_PASSWORD_FILE': 'two'})

    def test_browsers_share_status_but_cannot_change_connection(self):
        agent = MagicMock()
        agent.graph = object()
        agent.llm.models.return_value = ['test-local']
        agent.release = 'synthetic-release'
        agent.sources = {'document': 'source'}
        agent.pages = {'fragment': 'text'}
        agent.approved_candidates = 0
        server = Server(('127.0.0.1', 0), agent)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/api/status') as response:
                first = json.load(response)
            with urlopen(base + '/api/status') as response:
                second = json.load(response)
            self.assertTrue(first['graph_connected'] and second['graph_connected'])
            self.assertEqual(first['graph_auth_mode'], 'server_shared')
            self.assertNotIn('password', first)
            for endpoint in ['connect', 'disconnect']:
                request = Request(base + '/api/graph/' + endpoint, data=b'{}', headers={'Content-Type': 'application/json', 'X-Session-Key': first['csrf']})
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request)
                self.assertEqual(raised.exception.code, 404)
                raised.exception.close()
            agent.connect_graph.assert_not_called()
            agent.disconnect_graph.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main(verbosity=2)