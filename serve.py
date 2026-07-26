"""Static file server for pistachio's generated HTML pages.

Deployed via deploy/pistachio-serve.service. Reachable over Tailscale —
never public. PISTACHIO_SERVE_HOST should never be 0.0.0.0 on a box that
also has a plain LAN interface, same reasoning as psd-ootp's own webapp
(see src/webapp/main.py).
"""

import functools
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler

from config import export_filepath


def run() -> None:
    host = os.environ.get("PISTACHIO_SERVE_HOST", "127.0.0.1")
    port = int(os.environ.get("PISTACHIO_SERVE_PORT", "8100"))
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(export_filepath))
    HTTPServer((host, port), handler).serve_forever()


if __name__ == "__main__":
    run()
