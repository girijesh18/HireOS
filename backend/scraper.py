import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from loguru import logger

class StealthScraper:
    """
    A high-fidelity scraper that uses a headless browser to bypass bot protection.
    Uses playwright-stealth to mimic human behavior.
    """

    @staticmethod
    async def fetch(url: str, timeout_ms: int = 60000, captcha_key: str = None, proxy_url: str = None) -> str:
        """
        Launch a headless browser with advanced stealth, CAPTCHA solving, and proxy support.
        """
        logger.info(f"[StealthScraper] Spawning stealth browser for: {url}")
        
        async with async_playwright() as p:
            # Proxy configuration
            proxy = None
            if proxy_url:
                # Expecting format: http://user:pass@host:port
                proxy = {"server": proxy_url}
                logger.debug(f"[StealthScraper] Using proxy: {proxy_url}")

            browser = await p.chromium.launch(
                headless=True,
                proxy=proxy,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )
            
            ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            context = await browser.new_context(user_agent=ua, viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            
            # Apply stealth
            await Stealth().apply_stealth_async(page)
            
            try:
                # 1. Warm up
                domain = "/".join(url.split("/")[:3])
                await page.goto(domain, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)
                
                # 2. Navigate
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                
                # 3. Check for CAPTCHA
                if captcha_key:
                    await StealthScraper._solve_if_needed(page, captcha_key)

                # 4. Behavioral Jitter
                await page.mouse.move(100, 100)
                await page.mouse.move(400, 300, steps=10)
                await page.evaluate("window.scrollBy(0, 400)")
                await asyncio.sleep(4)
                
                content = await page.content()
                
                import re
                text = re.sub(r"<script[^>]*>.*?</script>", " ", content, flags=re.DOTALL)
                text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                
                await browser.close()
                return text
                
            except Exception as e:
                logger.error(f"[StealthScraper] Session failed: {e}")
                if browser:
                    await browser.close()
                raise e

    @staticmethod
    async def _solve_if_needed(page, api_key: str):
        """Detect and solve common CAPTCHAs using 2Captcha library."""
        try:
            from twocaptcha import TwoCaptcha
            solver = TwoCaptcha(api_key)
            
            # Check for hCaptcha
            hcaptcha = await page.query_selector("iframe[src*='hcaptcha']")
            if hcaptcha:
                logger.info("[StealthScraper] hCaptcha detected. Solving...")
                # Extract sitekey from page source if possible
                sitekey = await page.evaluate("document.querySelector('.h-captcha')?.dataset.sitekey || ''")
                if sitekey:
                    result = solver.hcaptcha(sitekey=sitekey, url=page.url)
                    code = result['code']
                    await page.evaluate(f'document.querySelector("[name=h-captcha-response]").innerHTML = "{code}"')
                    await page.evaluate(f'document.querySelector("[name=g-recaptcha-response]").innerHTML = "{code}"')
                    # Click submit if found
                    submit = await page.query_selector("#hcaptcha-demo-submit, .hcaptcha-submit")
                    if submit: await submit.click()
                    await asyncio.sleep(3)

            # Check for reCAPTCHA
            recaptcha = await page.query_selector("iframe[src*='recaptcha']")
            if recaptcha:
                logger.info("[StealthScraper] reCAPTCHA detected. Solving...")
                sitekey = await page.evaluate("document.querySelector('.g-recaptcha')?.dataset.sitekey || ''")
                if sitekey:
                    result = solver.recaptcha(sitekey=sitekey, url=page.url)
                    code = result['code']
                    await page.evaluate(f'document.getElementById("g-recaptcha-response").innerHTML = "{code}"')
                    # Try to trigger the callback if it exists
                    await page.evaluate("if(typeof(onSuccess) === 'function') { onSuccess(); }")
                    await asyncio.sleep(3)

        except Exception as e:
            logger.warning(f"[StealthScraper] Captcha solving error: {e}")

# Test runner
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(StealthScraper.fetch(test_url))
        print(result[:1000])
