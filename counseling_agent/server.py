import argparse
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from agent import Agent, UNIVERSITIES


def configure_shared_graph(agent, environ=None):
    settings = os.environ if environ is None else environ
    password = settings.get('NEO4J_PASSWORD', '')
    password_file = settings.get('NEO4J_PASSWORD_FILE', '')
    if password and password_file:
        raise RuntimeError('NEO4J_PASSWORD와 NEO4J_PASSWORD_FILE 중 하나만 설정하세요.')
    try:
        if password_file:
            with open(password_file, encoding='utf-8') as source:
                password = source.read(1025).rstrip('\r\n')
        if not password and not password_file:
            return False
        if not password or len(password) > 1024:
            raise ValueError('Invalid password configuration')
        agent.connect_graph(settings.get('NEO4J_USERNAME', 'neo4j'), password)
        return True
    except Exception:
        raise RuntimeError('서버 공용 Neo4j 연결에 실패했습니다. 운영자 인증 설정과 대상 DB를 확인하세요.') from None
    finally:
        password = ''


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, agent):
        super().__init__(address, Handler)
        self.agent = agent
        self.csrf = secrets.token_urlsafe(32)
        self.busy = threading.BoundedSemaphore(1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *arguments):
        pass

    def valid_host(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        return self.headers.get('Host') in allowed

    def reply(self, status, data, content_type='application/json; charset=utf-8'):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {'error': '로컬 주소로만 접속할 수 있습니다.'})
        route = urlsplit(self.path).path
        if route == '/':
            return self.reply(200, Path(__file__).with_name('index.html').read_bytes(), 'text/html; charset=utf-8')
        if route == '/api/status':
            try:
                models = self.server.agent.llm.models()
            except Exception:
                models = []
            return self.reply(200, {'csrf': self.server.csrf, 'models': models, 'graph_connected': self.server.agent.graph is not None, 'graph_auth_mode': 'server_shared', 'universities': UNIVERSITIES, 'release_id': self.server.agent.release, 'counts': {'universities': 6, 'documents': len(self.server.agent.sources), 'fragments': len(self.server.agent.pages)}, 'approved_candidates': self.server.agent.approved_candidates})
        if route.startswith('/source/'):
            try:
                source = self.server.agent.source_path(route.removeprefix('/source/'))
                return self.reply(200, source.read_bytes(), 'application/pdf')
            except (ValueError, OSError):
                return self.reply(404, {'error': '원문을 찾을 수 없거나 변경된 파일입니다.'})
        return self.reply(404, {'error': '경로를 찾을 수 없습니다.'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        allowed_origins = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if not self.valid_host() or (origin and origin not in allowed_origins) or self.headers.get('X-Session-Key') != self.server.csrf:
            return self.reply(403, {'error': '허용되지 않은 요청입니다. 페이지를 새로고침하세요.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32768 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.reply(400, {'error': '올바른 JSON 요청이 필요합니다.'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('JSON 객체가 필요합니다.')
        except (ValueError, UnicodeError):
            return self.reply(400, {'error': '요청 형식이 올바르지 않습니다.'})
        route = urlsplit(self.path).path
        if route == '/api/ask':
            if not self.server.busy.acquire(blocking=False):
                return self.reply(429, {'error': '현재 답변을 생성 중입니다. 완료 후 다시 질문하세요.'})
            try:
                return self.reply(200, self.server.agent.ask(data))
            except (ValueError, TypeError):
                return self.reply(400, {'error': '질문과 검색 범위를 확인하세요.'})
            except Exception:
                return self.reply(500, {'error': '검색 처리 중 오류가 발생했습니다. 원본 DB와 로컬 서비스를 확인하세요.'})
            finally:
                self.server.busy.release()
        return self.reply(404, {'error': '경로를 찾을 수 없습니다.'})


def main():
    parser = argparse.ArgumentParser(description='Local admissions evidence agent')
    parser.add_argument('--port', type=int, default=8770)
    arguments = parser.parse_args()
    agent = Agent()
    try:
        connected = configure_shared_graph(agent)
    except RuntimeError as error:
        parser.exit(1, str(error) + '\n')
    server = Server(('127.0.0.1', arguments.port), agent)
    print(f'Admissions agent: http://127.0.0.1:{server.server_port}', flush=True)
    print('Shared Neo4j: ' + ('connected' if connected else 'not configured; SQL-only mode'), flush=True)
    print('Neo4j credentials are configured by the server operator, never through the browser.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        agent.disconnect_graph()
        server.server_close()


if __name__ == '__main__':
    main()