"""Slow handlers hand back a request id instead of holding the connection
open past Cloudflare's 100s (524). Run: python test_async_bus.py
"""
import asyncio

import httpx
from fastapi import FastAPI, HTTPException

import async_bus
from async_bus import AsyncBusMiddleware

async_bus.INLINE_WAIT = 0.2

app = FastAPI()
app.add_middleware(AsyncBusMiddleware)


@app.post("/api/echo")
async def echo(payload: dict, delay: float = 0):
    await asyncio.sleep(delay)
    return {"got": payload}


@app.post("/api/boom")
async def boom():
    await asyncio.sleep(0.4)
    raise HTTPException(status_code=402, detail="out of credits")


async def main():
    A, B = {"Authorization": "Bearer a", "X-Async": "1"}, {"Authorization": "Bearer b"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        # fast: normal response inline, no async id
        r = await c.post("/api/echo", json={"x": 1}, headers=A)
        assert r.status_code == 200 and r.json() == {"got": {"x": 1}} and "x-async-id" not in r.headers

        # no header: untouched
        r = await c.post("/api/echo?delay=0.3", json={"x": 2})
        assert r.status_code == 200

        # slow: 202 + id, then poll to the original response
        r = await c.post("/api/echo?delay=0.5", json={"x": 3}, headers=A)
        assert r.status_code == 202, r.text
        rid = r.headers["x-async-id"]
        assert (await c.get(f"/api/async/{rid}", headers=B)).status_code == 404  # other user
        r = await c.get(f"/api/async/{rid}", headers=A)
        assert r.status_code == 202 and r.headers["x-async-id"] == rid
        await asyncio.sleep(0.5)
        r = await c.get(f"/api/async/{rid}", headers=A)
        assert r.status_code == 200 and r.json() == {"got": {"x": 3}} and "x-async-id" not in r.headers

        # slow error keeps its status and detail
        r = await c.post("/api/boom", headers=A)
        rid = r.headers["x-async-id"]
        await asyncio.sleep(0.5)
        r = await c.get(f"/api/async/{rid}", headers=A)
        assert r.status_code == 402 and r.json()["detail"] == "out of credits"

        assert (await c.get("/api/async/nope", headers=A)).status_code == 404


if __name__ == "__main__":
    asyncio.run(main())
    print("ok")
