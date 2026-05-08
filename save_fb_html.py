
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.facebook.com/jovempannews/videos/jair-bolsonaro-permanece-internado-e-segue-em-observação-na-uti/1462994848827818"
OUT = Path("session_data/video.html")

async def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(8000)
        html = await page.content()
        OUT.write_text(html, encoding="utf-8")
        print(f"saved: {OUT.resolve()}")
        await browser.close()

asyncio.run(main())