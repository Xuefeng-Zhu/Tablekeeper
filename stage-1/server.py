"""Single-process HTTP boundary. No runtime network clients or external assets."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs, unquote
import os
import codec
from domain import Service
from rules import Error

service=Service()

class Server(ThreadingHTTPServer):
    request_queue_size=128
    daemon_threads=True

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'

    def log_message(self,*args):
        pass

    def handle_request(self):
        try:
            url=urlsplit(self.path)
            body={}
            if self.command in ('POST','PATCH','PUT'):
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if size<0: raise ValueError()
                    raw=self.rfile.read(size)
                    # Empty cancel is a supported bodyless operation.
                    body=codec.loads(raw) if raw else ({} if url.path.endswith('/cancel') else None)
                    if not isinstance(body,dict): raise ValueError()
                except (ValueError,UnicodeError,RecursionError):
                    raise Error(400,'malformed_request')
            query={k:v[0] for k,v in parse_qs(url.query,keep_blank_values=True).items()}
            status,text=service.execute(self.command,unquote(url.path),query,body,self.headers)
        except Error as error:
            status=error.status; text=codec.dumps({'error':{'code':error.code,'message':error.code.replace('_',' ')}})
        except (ValueError,TypeError,KeyError,OverflowError):
            status=422; text=codec.dumps({'error':{'code':'validation_failed','message':'Invalid value'}})
        raw=text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        try: self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError): pass

    do_GET=do_POST=do_PATCH=do_PUT=do_DELETE=handle_request

if __name__=='__main__':
    Server(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler).serve_forever()
