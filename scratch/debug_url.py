
import asyncio
import httpx
import re
import json

async def test_fetch():
    url = "https://www.gojobs.gov.on.ca/Preview.aspx?Language=English&JobID=242249"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            text = resp.text
            print(f"Status: {resp.status_code}")
            print(f"Final URL: {resp.url}")
            print(f"Original Length: {len(text)}")
            
            # Basic HTML stripping as in agents.py
            text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            print(f"Stripped Length: {len(text)}")
            print(f"Stripped content: {text}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_fetch())
