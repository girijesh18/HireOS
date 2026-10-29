"""JobDiscoveryAgent._fetch_page: plain reading only. A login wall, bot check or near-empty page is
reported (paste the JD instead), never worked around. Fake HTTP, no network.

Run: cd backend && python test_fetch_page.py
"""
import asyncio

import httpx

import agents

JD = "<html><body><h1>Senior Data Scientist</h1><p>" + "Build models with Python and SQL. " * 30 + "</p></body></html>"


def fetch(handler, url="https://acme.com/jobs/1"):
    real = agents.httpx.AsyncClient
    agents.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        return asyncio.run(agents.JobDiscoveryAgent._fetch_page(None, url))
    finally:
        agents.httpx.AsyncClient = real


def blocked(handler, url="https://acme.com/jobs/1"):
    try:
        fetch(handler, url)
    except ValueError as e:
        return str(e)
    raise AssertionError("must be reported, not fetched another way")


def test_fetch_page():
    text = fetch(lambda r: httpx.Response(200, text=JD))
    assert text.startswith("Senior Data Scientist Build models") and "<" not in text
    bot = "<p>" + "x " * 400 + "Please solve this CAPTCHA to continue</p>"
    assert "Paste JD" in blocked(lambda r: httpx.Response(200, text=bot))
    assert "Paste JD" in blocked(lambda r: httpx.Response(200, text="<p>Loading…</p>"))       # near empty
    login = lambda r: (httpx.Response(302, headers={"location": "https://acme.com/login?next=1"})
                       if r.url.path == "/jobs/1" else httpx.Response(200, text=JD))
    assert "Paste JD" in blocked(login)

    def down(r):
        raise httpx.ConnectError("refused")
    assert "Could not access website" in blocked(down)


if __name__ == "__main__":
    test_fetch_page()
    print("ok test_fetch_page")
