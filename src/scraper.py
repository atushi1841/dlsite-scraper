"""
DLsite Scraper — Core scraping logic.

Three modes + two detail levels:
- keyword search (Playwright for JS rendering)
- ranking scrape (direct HTTP — server-rendered)
- product ID scrape (direct HTTP — detail pages)

Detail levels:
- basic: Direct HTTP (fast, 15+ fields)
- full: Playwright-enriched (includes ratings, DL count, reviews)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
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
CRAWL_DELAY = 1.5  # seconds between requests
MAX_WORKERS = 5   # parallel HTTP detail scraping
MAX_RETRIES = 2   # HTTP retries

RJ_PREFIX_MAP = {
    "RJ": "home", "VJ": "maniax", "BJ": "maniax",
    "RE": "home", "VE": "maniax", "SJ": "maniax",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _retry_get(session: httpx.Client, url: str, **kwargs) -> httpx.Response | None:
    """GET with retry and backoff."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, **kwargs)
            if resp.status_code == 200:
                return resp
            time.sleep(2 * attempt)
        except Exception:
            time.sleep(2 * attempt)
    return None


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
    detail_level: str = "basic"
    scraped_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.genres is None: d["genres"] = []
        if self.sample_images is None: d["sample_images"] = []
        d["scraped_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return d


# ── HTTP Session ──

def create_session() -> httpx.Client:
    """HTTPX client with adult cookies. Bypasses Apify proxy env vars."""
    client = httpx.Client(
        headers=HEADERS, follow_redirects=True, timeout=30, trust_env=False,
    )
    try:
        client.get(f"{BASE_URL}/home", timeout=10)
    except Exception:
        pass
    client.cookies.set("adult", "on", domain=".dlsite.com")
    client.cookies.set("ovad", "on", domain=".dlsite.com")
    return client


# ── Product Detail (Basic — Direct HTTP) ──

def _parse_detail_from_html(html: str, work_id: str, url: str) -> DLsiteItem:
    """Extract product fields from server-rendered HTML (no JS needed)."""
    item = DLsiteItem(work_id=work_id, url=url, detail_level="basic")

    m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    if m: item.title = m.group(1).split(" | ")[0].strip()

    m = re.search(r"(?:サークル名|ブランド|メーカー)[：:]?\s*</th>\s*<td[^>]*>(.*?)</td>", html, re.DOTALL)
    if m:
        raw = re.sub(r"<[^>]+>", " ", m.group(1))
        # Clean up follow button text and extra tags
        item.circle = _clean(re.sub(r"(?:フォロー|お気に入り|閉じる).*$", "", raw))

    pm = re.search(r'<meta[^>]*itemprop="price"[^>]*content="(\d+)"', html)
    if pm: item.price_jpy = int(pm.group(1))
    om = re.search(r'data-official_price="(\d+)"', html)
    if om: item.original_price_jpy = int(om.group(1))
    if item.price_jpy is None:
        pj = re.search(r'"(?:price|official_price)[\'"]:\s*(\d+)', html)
        if pj: item.price_jpy = int(pj.group(1))

    for th, td in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", html, re.DOTALL):
        key = _clean(re.sub(r"<[^>]+>", " ", th))
        val = _clean(re.sub(r"<[^>]+>", " ", td))
        if "販売日" in key: item.release_date = val
        elif "声優" in key: item.voice_actor = val
        elif "年齢指定" in key: item.age_rating = val
        elif "作品形式" in key: item.work_type = val
        elif "ファイル形式" in key: item.file_format = val
        elif "ファイル容量" in key: item.file_size = val
        elif "ジャンル" in key:
            item.genres = [g.strip() for g in re.split(r"[\n/]", val) if g.strip()]

    dm = re.search(r'class="work_description[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
    if dm:
        desc = re.sub(r"<[^>]+>", "\n", dm.group(1))
        item.description = _clean(desc.replace("&nbsp;", " "))[:3000]

    im = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html)
    if im: item.image_url = im.group(1)
    if not item.image_url:
        im = re.search(r'class="work_slider"[^>]*>.*?src="([^"]*)"', html, re.DOTALL)
        if im: item.image_url = im.group(1)

    samples = re.findall(r'<img[^>]*src="(https://[^"]*(?:sample|img)[^"]*)"', html)
    if samples: item.sample_images = samples[:10]

    return item


def scrape_detail_basic(session: httpx.Client, work_id: str) -> DLsiteItem | None:
    """Fast product detail scraping via direct HTTP (no JS)."""
    prefix = re.match(r"([A-Z]+)", work_id)
    base_path = RJ_PREFIX_MAP.get(prefix.group(1) if prefix else "RJ", "home")
    url = f"{BASE_URL}/{base_path}/work/=/product_id/{work_id}.html"
    resp = _retry_get(session, url)
    if not resp or "adultcheck" in resp.url.path:
        return None
    html = resp.text
    if "Just a moment" in html[:500] or "しばらくお待ち" in html[:500]:
        return None
    return _parse_detail_from_html(html, work_id, url)


def _scrape_one_basic(wid: str) -> DLsiteItem | None:
    """Thread-safe wrapper for basic scraping."""
    session = create_session()
    return scrape_detail_basic(session, wid)


# ── Product Detail (Full — Playwright rendered) ──

async def _scrape_detail_full_pw(
    browser: Any, work_id: str,
) -> DLsiteItem | None:
    """Scrape product detail with Playwright to get Vue-rendered fields (rating, DL count)."""
    prefix = re.match(r"([A-Z]+)", work_id)
    base_path = RJ_PREFIX_MAP.get(prefix.group(1) if prefix else "RJ", "home")
    url = f"{BASE_URL}/{base_path}/work/=/product_id/{work_id}.html"

    try:
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(2)

        html = await page.content()

        rating_text = ""
        dl_text = ""
        try:
            el = await page.query_selector(".point.average_count")
            if el: rating_text = await el.inner_text()
        except: pass
        try:
            el = await page.query_selector(".work_dl_details .point")
            if el: dl_text = await el.inner_text()
        except: pass

        await page.close()

        item = _parse_detail_from_html(html, work_id, url)
        if rating_text:
            try: item.rating = float(rating_text.strip())
            except: pass
        if dl_text:
            try: item.dl_count = int(dl_text.strip().replace(",", ""))
            except: pass
        item.detail_level = "full"
        return item
    except Exception:
        return None


async def _enrich_with_playwright(
    work_ids: list[str], max_results: int,
) -> list[DLsiteItem]:
    """Scrape products with Playwright for full Vue-rendered data."""
    from playwright.async_api import async_playwright

    ids = work_ids[:max_results]
    results: list[DLsiteItem] = []

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
            user_agent=UA, locale="ja-JP", timezone_id="Asia/Tokyo",
            viewport={"width": 1920, "height": 1080},
        )
        await context.add_cookies([
            {"name": "adult", "value": "on", "domain": ".dlsite.com", "path": "/"},
            {"name": "ovad", "value": "on", "domain": ".dlsite.com", "path": "/"},
        ])

        for wid in ids:
            item = await _scrape_detail_full_pw(browser, wid)
            if item:
                results.append(item)
            await asyncio.sleep(CRAWL_DELAY)

        await browser.close()

    return results


# ── Batch HTTP (parallel) ──

def _scrape_many_basic(work_ids: list[str], max_results: int) -> list[DLsiteItem]:
    """Batch scrape products in parallel via direct HTTP (fast)."""
    if not work_ids:
        return []
    ids = work_ids[:max_results]
    results: list[DLsiteItem] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        fut_map = {pool.submit(_scrape_one_basic, wid): wid for wid in ids}
        for future in concurrent.futures.as_completed(fut_map):
            item = future.result()
            if item:
                results.append(item)
            time.sleep(CRAWL_DELAY / MAX_WORKERS)
    by_id = {r.work_id: r for r in results}
    return [by_id[wid] for wid in ids if wid in by_id]


def _scrape_many_full(work_ids: list[str], max_results: int) -> list[DLsiteItem]:
    """Batch scrape products with Playwright for Vue-rendered data (slower).
    Uses ThreadPoolExecutor to avoid async context conflicts (Apify mode)."""
    if not work_ids:
        return []
    ids = work_ids[:max_results]
    try:
        loop = asyncio.get_running_loop()
        # Inside async context — run in separate thread
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(lambda: asyncio.run(_enrich_with_playwright(ids, len(ids))))
            return fut.result()
    except RuntimeError:
        return asyncio.run(_enrich_with_playwright(ids, len(ids)))


# ── Playwright Search ──

async def _scrape_search_pw(
    url: str, max_results: int,
) -> list[DLsiteItem]:
    """Search via Playwright, extract product IDs, scrape with Playwright enrichment."""
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
            user_agent=UA, locale="ja-JP", timezone_id="Asia/Tokyo",
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

        await asyncio.sleep(3)
        try:
            await page.wait_for_selector('a[href*="/work/=/product_id/"]', timeout=30000)
        except Exception:
            pass

        html = await page.content()
        links = re.findall(r'/work/=/product_id/((?:RJ|VJ|BJ|RE|VE)\d+)', html)
        links = list(dict.fromkeys(links))

        # Scrape each product with Playwright (same browser) for full data
        results: list[DLsiteItem] = []
        for wid in links[:max_results]:
            item = await _scrape_detail_full_pw(browser, wid)
            if item:
                results.append(item)
            await asyncio.sleep(CRAWL_DELAY)

        await browser.close()
    return results


# ── Public API ──

def scrape_search(
    keyword: str,
    max_results: int = 50,
    detail_level: str = "basic",
) -> list[DLsiteItem]:
    """Search DLsite. When detail='full', uses Playwright for all data."""
    url = f"{BASE_URL}/home/fs?keyword={quote(keyword)}"

    def _run_search():
        if detail_level == "full":
            return asyncio.run(_scrape_search_pw(url, max_results))
        ids = asyncio.run(_scrape_search_pw(url, max_results))
        if not ids:
            return []
        return _scrape_many_basic([i.work_id for i in ids], max_results)

    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: _run_search()).result()
    except RuntimeError:
        return _run_search()


def scrape_ranking(
    ranking_type: str = "daily",
    max_results: int = 50,
    detail_level: str = "basic",
) -> list[DLsiteItem]:
    """Scrape rankings. Direct HTTP for listing, optional Playwright for full detail."""
    period_map = {"daily": "day", "weekly": "week", "monthly": "month", "total": ""}
    period = period_map.get(ranking_type, "day")
    url = f"{BASE_URL}/home/ranking/{period}" if period else f"{BASE_URL}/home/ranking?date=all"

    session = create_session()
    resp = _retry_get(session, url)
    if not resp:
        return []
    html = resp.text
    if "Just a moment" in html[:500] or "しばらくお待ち" in html[:500]:
        return []

    work_ids = re.findall(r"/(?:RJ|VJ|BJ|RE|VE)\d{8}", html)
    work_ids = list(dict.fromkeys(wid.lstrip("/") for wid in work_ids))
    if not work_ids:
        return []

    if detail_level == "full":
        return _scrape_many_full(work_ids, max_results)
    return _scrape_many_basic(work_ids, max_results)


def scrape_product_ids(
    product_ids: list[str],
    detail_level: str = "basic",
) -> list[DLsiteItem]:
    """Scrape specific product IDs."""
    ids = [pid.strip() for pid in product_ids if pid.strip()]
    if not ids:
        return []
    if detail_level == "full":
        return _scrape_many_full(ids, len(ids))
    return _scrape_many_basic(ids, len(ids))
