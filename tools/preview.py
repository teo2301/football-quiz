#!/usr/bin/env python3
"""Preview server for v2: serves docs/ on 0.0.0.0:8197 and proxies /api/* to a
test leaderboard (default 127.0.0.1:8193). The app uses API = "/api" when it's
on port 8197, so the preview never touches the live board.

    QUIZ_DB=/tmp/fq-v2-test.db QUESTIONS_PATH=docs/questions.json QUIZ_HOST=127.0.0.1 QUIZ_PORT=8193 python3 server/leaderboard.py
    python3 tools/preview.py
"""
import os
import sys
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
PORT = int(os.environ.get("PREVIEW_PORT", "8197"))
UPSTREAM = os.environ.get("PREVIEW_API", "http://127.0.0.1:8193")


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _proxy(self):
        body = None
        if self.command == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(n, 65536))
        req = urllib.request.Request(UPSTREAM + self.path, data=body, method=self.command,
                                     headers={"X-Forwarded-For": self.headers.get("X-Preview-Client") or self.client_address[0],
                                              "Content-Type": self.headers.get("Content-Type", "text/plain")})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                code, data, ctype = r.status, r.read(), r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            code, data, ctype = e.code, e.read(), e.headers.get("Content-Type", "application/json")
        except OSError:
            code, data, ctype = 502, b'{"error": "test server down"}', "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(405)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.client_address[0], fmt % args))


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), partial(Handler, directory=ROOT)).serve_forever()
