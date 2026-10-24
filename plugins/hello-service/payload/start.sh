#!/bin/sh
GREETING="${GREETING:-hello from jianyun}"
exec python3 -c "
import http.server, os
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b=os.environ.get('GREETING','hello').encode()
        self.send_response(200); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self,*a): pass
http.server.HTTPServer(('',int(os.environ.get('SERVICE_PORT','18081'))),H).serve_forever()
"
