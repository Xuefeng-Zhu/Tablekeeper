import os
from . import codec as json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs,unquote
from .core import Error,parse,fail,password
from .service import Service
from zoneinfo import ZoneInfo

class Server(ThreadingHTTPServer):
    request_queue_size=256
    daemon_threads=True

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass # Requests and private test snapshots never enter logs.
    def handle_api(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<0: fail(400,'malformed_request')
            raw=self.rfile.read(size).decode('utf-8')
            url=urlsplit(self.path); path=unquote(url.path)
            body={}
            if self.command in ['POST','PATCH','PUT']:
                if not raw and path.endswith('/cancel'): raw='{}'
                body=parse(raw)
            query={k:v[0] for k,v in parse_qs(url.query,keep_blank_values=True).items()}
            status,result=self.server.service.route(self.command,path,query,body,raw,self.headers.get('Authorization'),self.headers.get('Idempotency-Key'))
        except Error as e: status,result=e.status,{'error':{'code':e.code,'message':e.code.replace('_',' ')}}
        except (ValueError,UnicodeError): status,result=400,{'error':{'code':'malformed_request','message':'Malformed request'}}
        except Exception:
            import traceback
            traceback.print_exc()
            status,result=500,{'error':{'code':'internal_error','message':'Internal error'}}
        payload=b'' if status==204 else json.dumps(result,separators=(',',':'),ensure_ascii=True).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(payload))); self.end_headers()
        try: self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError): pass
    do_GET=do_POST=do_PATCH=do_PUT=do_DELETE=do_OPTIONS=handle_api

def main():
    ZoneInfo('Europe/Berlin'); ZoneInfo('America/New_York'); password('runtime-capability-check')
    server=Server(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler)
    server.service=Service(); server.serve_forever()
if __name__=='__main__': main()
