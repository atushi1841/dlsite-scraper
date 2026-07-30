"""
DLsite Scraper — Core scraping logic.

Three modes:
- keyword search (via Playwright for JS rendering)
- ranking scrape (direct HTTP — server-rendered)
- product ID scrape (direct HTTP — detail pages)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import time
from dataclasses import dataclass, asdict
from typing import Any
from urllib.parse import quote

import httpx

BASE_URL = "https://www.dlsite.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
    "Gecko/20100101 Firefox/133.0"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US,en;q=0.5",
    "DNT": "1",
}
CRAWL_DELAY = 2  # seconds between requests
MAX_WORKERS = 5  # parallel product detail scraping

# RJ prefix → genre page path
RJ_PREFIX_MAP = {
    "RJ": "home", "VJ": "maniax", "BJ": "maniax",
    "RE": "home", "VE": "maniax", "SJ": "maniax",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class DLsiteItem:
    """Normalized DLsite product data."""
    work_id: str = ""
    title: str = ""
    circle: str = ""
    price_jpy: int | None = None
    original_price_jpy: int | None = None
    sale_badge: str = ""
    release_date: str = ""
    voice_actor: str = ""
    age_rating: str = ""
    work_type: str = ""
    file_format: str = ""
    file_size: str = ""
    genres: list[str] = None
    dl_count: int | None = None
    rating: float | None = None
    rating_count: int | None = None
    wishlist_count: int | None = None
    review_count: int | None = None
    description: str = ""
    image_url: str = ""
    sample_images: list[str] = None
    url: str = ""
    scraped_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.genres is None:
            d["genres"] = []
        if self.sample_images is None:
            d["sample_images"] = []
        d["scraped_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return d


# ── HTTP Session ──

def create_session() -> httpx.Client:
    """HTTPX client with adult cookies for DLsite. Bypasses Apify proxy."""
    client = httpx.Client(
        headers=HEADERS, follow_redirects=True, timeout=30, trust_env=False,
    )
    try:
        client.get(f"{BASE_URL}/home")
    except Exception:
        pass
    client.cookies.set("adult", "on", domain=".dlsite.com")
    client.cookies.set("ovad", "on", domain=".dlsite.com")
    return client


# ── Product Detail ──

def scrape_product_detail(session: httpx.Client, work_id: str) -> DLsiteItem | None:
    """Scrape a single product detail page (server-side HTML, no JS needed)."""
    prefix = re.match(r"([A-Z]+)", work_id)
    prefix_str = prefix.group(1) if prefix else "RJ"
    base_path = RJ_PREFIX_MAP.get(prefix_str, "home")
    url = f"{BASE_URL}/{base_path}/work/=/product_id/{work_id}.html"

    try:
        resp = session.get(url)
        if resp.status_code != 200 or "adultcheck" in resp.url.path:
            return None
    except Exception:
        return None

    html = resp.text
    item = DLsiteItem(work_id=work_id, url=url)

    # Title (<title>)
    m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    if m:
        item.title = m.group(1).split(" | ")[0].strip()

    # Circle (server-rendered)
    m = re.search(
        r"(?:サークル名|ブランド|メーカー)[：:]?\s*</th>\s*<td[^>]*>(.*?)</td>",
        html, re.DOTALL,
    )
    if m:
        item.circle = _clean(re.sub(r"<[^>]+>", " ", m.group(1)))

    # Price
    pm = re.search(r'<meta[^>]*itemprop="price"[^>]*content="(\d+)"', html)
    if pm:
        item.price_jpy = int(pm.group(1))
    om = re.search(r'data-official_price="(\d+)"', html)
    if om:
        item.original_price_jpy = int(om.group(1))
    if item.price_jpy is None:
        pj = re.search(r'"(?:price|official_price)["\']:\s*(\d+)', html)
        if pj:
            item.price_jpy = int(pj.group(1))

    # Sale badge
    sm = re.search(r"通知バッジ[^>]*>(.*?)<", html)
    if sm:
        item.sale_badge = _clean(sm.group(1))

    # Detail table
    detail_rows = re.findall(
        r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", html, re.DOTALL,
    )
    for th, td in detail_rows:
        key = _clean(re.sub(r"<[^>]+>", " ", th))
        val = _clean(re.sub(r"<[^>]+>", " ", td))
        if "販売日" in key:
            item.release_date = val
        elif "声優" in key:
            item.voice_actor = val
        elif "年齢指定" in key:
            item.age_rating = val
        elif "作品形式" in key:
            item.work_type = val
        elif "ファイル形式" in key:
            item.file_format = val
        elif "ファイル容量" in key:
            item.file_size = val
        elif "ジャンル" in key:
            item.genres = [g.strip() for g in re.split(r"[\n/]", val) if g.strip()]

    # Description
    dm = re.search(
        r'class="work_description[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL,
    )
    if dm:
        desc = re.sub(r"<[^>]+>", "\n", dm.group(1))
        item.description = _clean(desc.replace("&nbsp;", " "))[:3000]

    # Image
    im = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html)
    if im:
        item.image_url = im.group(1)
    if not item.image_url:
        im = re.search(r'class="work_slider"[^>]*>.*?src="([^"]*)"', html, re.DOTALL)
        if im:
            item.image_url = im.group(1)

    # Sample images
    samples = re.findall(
        r'<img[^>]*src="(https://[^"]*(?:sample|img)[^"]*)"', html,
    )
    if samples:
        item.sample_images = samples[:10]

    return item


def _scrape_one(wid: str) -> DLsiteItem | None:
    """Wrapper for concurrent scraping (each gets its own session)."""
    session = create_session()
    item = scrape_product_detail(session, wid)
    return item


def _scrape_many(work_ids: list[str], max_results: int) -> list[DLsiteItem]:
    """Scrape multiple products in parallel with ThreadPoolExecutor."""
    if not work_ids:
        return []
    ids = work_ids[:max_results]
    results: list[DLsiteItem] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        fut_map = {pool.submit(_scrape_one, wid): wid for wid in ids}
        for future in concurrent.futures.as_completed(fut_map):
            item = future.result()
            if item:
                results.append(item)
            time.sleep(CRAWL_DELAY / MAX_WORKERS)  # Rate limit across workers
    # Preserve original order
    by_id = {r.work_id: r for r in results}
    return [by_id[wid] for wid in ids if wid in by_id]


# ── Playwright Search ──

async def _scrape_with_playwright(
    url: str,
    max_results: int,
) -> list[DLsiteItem]:
    """Render Vue.js search page with Playwright, extract product IDs, scrape details."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
                "--disable-component-update",
            ],
        )
        context = await browser.new_context(
            user_agent=UA,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1920, "height": 1080},
        )
        await context.add_cookies([
            {"name": "adult", "value": "on", "domain": ".dlsite.com", "path": "/"},
            {"name": "ovad", "value": "on", "domain": ".dlsite.com", "path": "/"},
        ])
        page = await context.new_page()
        page.set_default_timeout(120000)

        try:
            await page.goto(url, wait_until="networkidle", timeout=120000)
        except Exception:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                await browser.close()
                return []

        # Wait for Vue to render
        await asyncio.sleep(3)
        try:
            await page.wait_for_selector('a[href*="/work/=/product_id/"]', timeout=30000)
        except Exception:
            pass

        html = await page.content()
        await browser.close()

    links = re.findall(r'/work/=/product_id/((?:RJ|VJ|BJ|RE|VE)\d+)', html)
    links = list(dict.fromkeys(links))
    return _scrape_many(links, max_results)


# ── Public API ──

def scrape_search(
    keyword: str,
    work_type: str = "all",
    max_results: int = 50,
) -> list[DLsiteItem]:
    """Search DLsite by keyword. Uses Playwright for Vue.js rendering."""
    base = "home" if work_type in ("all", "voice", "game", "manga", "music", "soft") else "home"
    url = f"{BASE_URL}/{base}/fs?keyword={quote(keyword)}"
    return asyncio.run(_scrape_with_playwright(url, max_results))


def scrape_ranking(
    ranking_type: str = "daily",
    work_type: str = "all",
    max_results: int = 50,
) -> list[DLsiteItem]:
    """Scrape rankings via direct HTTP (server-rendered, no JS needed)."""
    base = "home"
    period_map = {"daily": "day", "weekly": "week", "monthly": "month", "total": ""}
    period = period_map.get(ranking_type, "day")
    url = f"{BASE_URL}/{base}/ranking/{period}" if period else f"{BASE_URL}/{base}/ranking?date=all"

    session = create_session()
    try:
        resp = session.get(url)
    except Exception:
        return []
    if resp.status_code != 200:
        return []

    html = resp.text
    if "Just a moment" in html[:500] or "しばらくお待ち" in html[:500]:
        return []

    work_ids = re.findall(r"/(?:RJ|VJ|BJ|RE|VE)\d{8}", html)
    work_ids = list(dict.fromkeys(wid.lstrip("/") for wid in work_ids))

    if not work_ids:
        return []

    return _scrape_many(work_ids, max_results)


def scrape_product_ids(product_ids: list[str]) -> list[DLsiteItem]:
    """Scrape specific product IDs (fastest — direct HTTP only)."""
    return _scrape_many([pid.strip() for pid in product_ids if pid.strip()], len(product_ids))
