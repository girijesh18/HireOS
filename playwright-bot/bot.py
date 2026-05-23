"""Playwright Application Bot — receives job application tasks from backend and executes them."""
import asyncio
from loguru import logger

async def main():
    logger.info("Playwright bot ready and listening...")
    # Will be wired to backend task queue in Phase 3
    while True:
        await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
