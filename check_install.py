"""Comprueba las bibliotecas, SQLite y Chromium usando solo una página local."""

import asyncio
import os
import sqlite3
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version

from crawlee import ConcurrencySettings
from crawlee.crawlers import BeautifulSoupCrawler
from playwright.async_api import async_playwright


class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><head><title>Monitor local OK</title></head><body>Prueba local</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


async def check(url):
    for package in ("crawlee", "playwright", "ollama"):
        print(f"{package}: {version(package)}", flush=True)
    with sqlite3.connect(":memory:") as database:
        database.execute("CREATE TABLE check_install (id TEXT PRIMARY KEY)")
        database.execute("INSERT INTO check_install VALUES ('ok')")
        assert database.execute("SELECT id FROM check_install").fetchone() == ("ok",)
    print(f"SQLite: {sqlite3.sqlite_version} OK", flush=True)

    titles = []
    crawler = BeautifulSoupCrawler(
        max_requests_per_crawl=1,
        concurrency_settings=ConcurrencySettings(min_concurrency=1, max_concurrency=1),
    )

    @crawler.router.default_handler
    async def handle(context):
        titles.append(context.soup.title.get_text())

    await crawler.run([url])
    assert titles == ["Monitor local OK"], titles
    print("Crawlee HTTP: OK", flush=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.environ.get("MONITOR_CHROMIUM_PATH", "/usr/bin/chromium"),
            headless=True,
            chromium_sandbox=True,
        )
        try:
            page = await browser.new_page()
            await page.goto(url)
            assert await page.title() == "Monitor local OK"
            assert await page.evaluate("1 + 1") == 2
            print(f"Playwright + Chromium {browser.version}: OK", flush=True)
        finally:
            await browser.close()


if __name__ == "__main__":
    original_directory = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="local-monitor-check-") as directory:
        server = ThreadingHTTPServer(("127.0.0.1", 0), PageHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            os.chdir(directory)
            asyncio.run(check(f"http://127.0.0.1:{server.server_port}/"))
        finally:
            os.chdir(original_directory)
            server.shutdown()
            server.server_close()
            thread.join()
