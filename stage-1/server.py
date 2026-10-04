"""HTTP transport; request reads and response writes never hold the domain lock."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote
from domain import Service, Error, parse
from portability import fixture, imported

service = Service()
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def handle_request(self):
        try:
            url = urlsplit(self.path)
            path = unquote(url.path)
            raw = '{}'
            if self.command in ['POST','PATCH','PUT']:
                try: length = int(self.headers.get('Content-Length','0'))
                except ValueError: raise Error(400,'malformed_request')
                if length < 0: raise Error(400,'malformed_request')
                raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            body = parse(raw)
            if self.command=='POST' and path in ['/auth/signup','/auth/login']:
                status,response=service.auth(path,body)
            elif self.command=='POST' and path in ['/_test/reset','/_test/import']:
                state=fixture(service,body) if path=='/_test/reset' else imported(service,body)
                with service.lock: service.state=state
                status,response=204,None
            else:
                query={k:v[-1] for k,v in parse_qs(url.query,keep_blank_values=True).items()}
                status,response=service.transact(self.command,path,body,raw,self.headers,query)
        except Error as e: status,response=e.status,{'error':{'code':e.code,'message':e.code.replace('_',' ')}}
        except (ValueError,UnicodeError,OverflowError,RecursionError): status,response=400,{'error':{'code':'malformed_request','message':'Invalid request'}}
        except Exception as e:
            print('Unexpected request failure: '+type(e).__name__,file=sys.stderr)
            status,response=500,{'error':{'code':'internal_error','message':'Internal error'}}
        payload=b'' if status==204 else json.dumps(response,separators=(',',':'),ensure_ascii=False).encode()
        try:
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError): pass
    do_GET=do_POST=do_PATCH=do_PUT=do_DELETE=handle_request

class Server(ThreadingHTTPServer):
    request_queue_size=128
    daemon_threads=True
if __name__=='__main__':
    Server(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler).serve_forever()
