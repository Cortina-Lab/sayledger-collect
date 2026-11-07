"""Passenger entry for cPanel "Setup Python App" hosting of the SayLedger Collect backend.

cPanel mounts the app under a URL prefix (default /sayledger-collect, set SAYLEDGER_PREFIX to
change it). The FastAPI app has no prefix, so it is stripped here before routing.
PayPal's webhook then goes to https://<domain><prefix>/paypal/webhook.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(HERE, ".env"))
os.environ.setdefault("SAYLEDGER_DATA", os.path.join(HERE, "data"))

# Ledger days are local days. Use the demo shop's time zone, not the server's.
os.environ["TZ"] = os.getenv("SAYLEDGER_TZ", "America/New_York")
time.tzset()

from a2wsgi import ASGIMiddleware  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

PREFIX = os.getenv("SAYLEDGER_PREFIX", "/sayledger-collect").rstrip("/")


async def _strip_prefix(scope, receive, send):
    if PREFIX and scope.get("type") in ("http", "websocket"):
        path = scope.get("path", "") or "/"
        if path == PREFIX or path.startswith(PREFIX + "/"):
            path = path[len(PREFIX):] or "/"
        scope = dict(scope, path=path, raw_path=path.encode(), root_path="")
    await fastapi_app(scope, receive, send)


application = ASGIMiddleware(_strip_prefix)
