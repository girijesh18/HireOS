"""Long requests without the 524.

Cloudflare drops any origin response slower than 100s with a 524, and LLM
calls regularly take longer. A client that sends `X-Async: 1` gets:

  * the normal response, if the handler finishes within INLINE_WAIT seconds;
  * otherwise `202` with `X-Async-Id: <id>`, while the handler keeps running.
    It then polls `GET /api/async/<id>` (same Authorization header); a reply
    without `X-Async-Id` is the handler's original response, byte for byte.

Works for every endpoint, unchanged: this is plain ASGI in front of the app.
"""
import asyncio
import hmac
import json
import secrets
import time

INLINE_WAIT = 20        # seconds; well under Cloudflare's 100s
RESULT_TTL = 3600       # finished results kept this long for the poller
POLL_PREFIX = "/api/async/"

# ponytail: in-process dict -- fine for one uvicorn worker on one machine (the
# Dockerfile's --workers 1). A restart loses in-flight jobs; the poller gets a
# 404 and says so. Move to Redis/DB if we ever scale out.
_jobs: dict = {}


def _json(status, payload, extra=()):
    body = json.dumps(payload).encode()
    return status, [(b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()), *extra], body


async def _send(send, status, headers, body):
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class AsyncBusMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        auth = headers.get(b"authorization", b"")

        if scope["path"].startswith(POLL_PREFIX) and scope["method"] == "GET":
            return await self._poll(scope["path"][len(POLL_PREFIX):], auth, send)
        if headers.get(b"x-async") != b"1":
            return await self.app(scope, receive, send)

        # Buffer the request body: the handler outlives this connection.
        body, more = b"", True
        while more:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            body += msg.get("body", b"")
            more = msg.get("more_body", False)

        now = time.time()
        for k in [k for k, j in _jobs.items() if j["done"].is_set() and now - j["t"] > RESULT_TTL]:
            del _jobs[k]

        job = {"auth": auth, "t": now, "done": asyncio.Event(),
               "status": 500, "headers": [], "body": b""}
        job["task"] = asyncio.create_task(self._run(scope, body, job))

        try:
            await asyncio.wait_for(asyncio.shield(job["done"].wait()), INLINE_WAIT)
            return await _send(send, job["status"], job["headers"], job["body"])
        except asyncio.TimeoutError:
            rid = secrets.token_urlsafe(18)
            _jobs[rid] = job
            await _send(send, *_json(202, {"status": "processing", "request_id": rid},
                                     [(b"x-async-id", rid.encode())]))

    async def _run(self, scope, body, job):
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()   # no disconnect: the client may be gone, the work isn't

        async def capture(msg):
            if msg["type"] == "http.response.start":
                job["status"], job["headers"] = msg["status"], list(msg.get("headers", []))
            elif msg["type"] == "http.response.body":
                job["body"] += msg.get("body", b"")
                if not msg.get("more_body"):
                    job["done"].set()

        try:
            await self.app(scope, replay, capture)
        except Exception as e:
            job["status"], job["headers"], job["body"] = _json(500, {"detail": f"Request failed: {e}"})
        finally:
            job["t"] = time.time()
            job["done"].set()

    async def _poll(self, rid, auth, send):
        job = _jobs.get(rid)
        if not job or not hmac.compare_digest(job["auth"], auth):
            return await _send(send, *_json(404, {"detail": "Request not found — the server may have restarted. Try again."}))
        if not job["done"].is_set():
            return await _send(send, *_json(202, {"status": "processing", "request_id": rid},
                                            [(b"x-async-id", rid.encode())]))
        await _send(send, job["status"], job["headers"], job["body"])
