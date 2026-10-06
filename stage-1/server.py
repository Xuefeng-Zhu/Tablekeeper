"""JSON HTTP adapter; no network services or runtime package downloads."""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote
from values import APIError, parse
from service import Service, encoded
SERVICE = Service()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass  # Never log bodies, credentials, tokens or private exports.

    def handle_request(self):
        try:
            body = {}
            length = int(self.headers.get('Content-Length', '0'))
            if length:
                body = parse(self.rfile.read(length))
            elif self.command in ('POST', 'PATCH') and not self.path.endswith('/cancel'):
                raise APIError(400, 'malformed_request')
            url = urlsplit(self.path)
            status, payload = SERVICE.request(self.command, unquote(url.path), parse_qs(url.query, keep_blank_values=True), body, self.headers)
        except APIError as error:
            status, payload = error.status, encoded({'error': {'code': error.code, 'message': error.code.replace('_', ' ')}})
        except (ValueError, OverflowError, RecursionError):
            status, payload = 400, encoded({'error': {'code': 'malformed_request', 'message': 'Invalid request'}})
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = handle_request


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128


if __name__ == '__main__':
    Server(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()
