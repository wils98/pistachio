"""Static file server for pistachio's generated HTML pages.

Deployed via deploy/pistachio-serve.service. Reachable over Tailscale —
never public. PISTACHIO_SERVE_HOST should never be 0.0.0.0 on a box that
also has a plain LAN interface, same reasoning as psd-ootp's own webapp
(see src/webapp/main.py).

Also handles POST /refresh — an on-demand rerun of main.py, triggered by the
"Refresh" button on the draft pages (only those pages for now; see
exporter.py's "refreshable" flag). A stand-in for a scheduled
pistachio-run.timer, which stays disabled — see NOTES.md. HTTPServer here is
single-threaded by design: a /refresh request blocks all other requests
(including normal page loads) until main.py finishes, which also means two
overlapping refreshes can't race each other.
"""

import functools
import os
import subprocess
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

from config import export_filepath, pistachio_filepath

REFRESH_PATH = "/refresh"


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path != REFRESH_PATH:
            self.send_error(404)
            return

        result = subprocess.run(
            [sys.executable, str(pistachio_filepath / "main.py")],
            cwd=str(pistachio_filepath),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            body = (result.stderr or "main.py failed").encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(204)
        self.end_headers()


def run() -> None:
    host = os.environ.get("PISTACHIO_SERVE_HOST", "127.0.0.1")
    port = int(os.environ.get("PISTACHIO_SERVE_PORT", "8100"))
    handler = functools.partial(Handler, directory=str(export_filepath))
    HTTPServer((host, port), handler).serve_forever()


if __name__ == "__main__":
    run()
