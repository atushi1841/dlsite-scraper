"""
DLsite Scraper — Core scraping logic.

Supports three modes:
- keyword search (via Playwright for JS rendering)
- ranking scrape (via Playwright for JS rendering)
- product ID scrape (direct HTTP — no JS needed for detail pages)
"""
from __future__ import annotations

import asyncio
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
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
}
CRAWL_DELAY = 3  # seconds between requests (robots.txt: no explicit delay)

# Work type path mappings
WORK_TYPE_PATHS = {
    "all": "home",
    "voice": "home",
    "game": "home",
    "manga": "home",
    "music": "home",
    "soft": "home",
}
MANIAX_PATHS = {
    "voice": "maniax",
    "game": "maniax",
    "manga": "maniax",
    "music": "maniax",
    "soft": "maniax",
}
# RJ prefix → genre page path
RJ_PREFIX_MAP = {
    "RJ": "home",  # 同人一般
    "VJ": "maniax",  # 成人
    "BJ": "maniax",  # 成人(漫画)
    "RE": "home",  # 法人
    "VE": "maniax",  # 法人(成人)
}


def _price(text: str) -> int | None:
    m = re.search(r"[¥￥]\s*([\d,]+)", text)
    return int(m.group(1).replace(",", "")) if m else None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class DLsiteItem:
    """Normalized DLsite product data."""
    work_id: str = ""  # RJ/VJ/BJ number
    title: str = ""
    circle: str = ""  # サークル名
    price_jpy: int | None = None
    original_price_jpy: int | None = None  # 割引前価格
    sale_badge: str = ""  # セール表示
    release_date: str = ""  # 販売日
    voice_actor: str = ""  # 声優
    age_rating: str = ""  # 年齢指定
    work_type: str = ""  # 作品形式
    file_format: str = ""  # ファイル形式
    file_size: str = ""  # ファイル容量
    genres: list[str] = None  # ジャンル一覧
    dl_count: int | None = None  # DL数
    rating: float | None = None  # 評価
    rating_count: int | None = None  # 評価数
    wishlist_count: int | None = None  # いいね数
    review_count: int | None = None  # レビュー数
    description: str = ""  # 作品説明
    image_url: str = ""  # サムネイル画像
    sample_images: list[str] = None  # サンプル画像
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


# ── HTTP Session Setup ──

def create_session() -> httpx.Client:
    """Create HTTPX client with DLsite cookies for adult content access.
    Bypasses Apify proxy env vars (DLsite doesn't need proxy and proxy causes issues)."""
    client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30, trust_env=False)
    # Try to visit home page to get session; continue even if it fails
    try:
        client.get(f"{BASE_URL}/home")
    except Exception:
        pass
    # Set adult verification cookies regardless
    client.cookies.set("adult", "on", domain=".dlsite.com")
    client.cookies.set("ovad", "on", domain=".dlsite.com")
    return client


# ── Product Detail Scraping (Direct HTTP) ──

def scrape_product_detail(session: httpx.Client, work_id: str) -> DLsiteItem | None:
    """Scrape a single product detail page. Works without JS rendering."""
    # Determine correct URL path based on RJ prefix
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

    # Circle/Maker name (server-rendered)
    m = re.search(
        r"(?:サークル名|ブランド|メーカー)[：:]?\s*</th>\s*<td[^>]*>(.*?)</td>",
        html, re.DOTALL,
    )
    if m:
        item.circle = _clean(re.sub(r"<[^>]+>", " ", m.group(1)))

    # Price (multiple sources: meta tag, price attribute, embedded JSON)
    price_m = re.search(r'<meta[^>]*itemprop="price"[^>]*content="(\d+)"', html)
    if price_m:
        item.price_jpy = int(price_m.group(1))
    
    # Original/list price (official_price attribute)
    m = re.search(r'data-official_price="(\d+)"', html)
    if m:
        item.original_price_jpy = int(m.group(1))
    
    # Price from JSON data (embedded in HTML)
    price_json = re.search(r'"(?:price|official_price)["\']:\s*(\d+)', html)
    if price_json and item.price_jpy is None:
        item.price_jpy = int(price_json.group(1))

    # Sale badge / original price
    m = re.search(r"class=\"[^\"]*sale[^\"]*\"[^>]*>(.*?)<", html, re.DOTALL)
    if m:
        item.sale_badge = _clean(m.group(1))

    # Detail table fields (server-rendered)
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
            # Split by /\n/space or linebreak
            raw_genres = re.split(r"[\n/]", val)
            item.genres = [g.strip() for g in raw_genres if g.strip()]

    # Description (work description area)
    m = re.search(
        r'class="work_description[^"]*"[^>]*>(.*?)</div>',
        html, re.DOTALL,
    )
    if m:
        desc = re.sub(r"<[^>]+>", "\n", m.group(1))
        item.description = _clean(desc)[:2000]

    # Image (og:image or product image)
    m = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html)
    if m:
        item.image_url = m.group(1)

    # Sample images
    sample_imgs = re.findall(
        r'<img[^>]*src="(https://[^"]*(?:sample|img)[^"]*)"', html,
    )
    if sample_imgs:
        item.sample_images = sample_imgs[:10]

    # Work name fallback
    if not item.title:
        m = re.search(r'class="work_name"[^>]*>(.*?)</div>', html, re.DOTALL)
        if m:
            item.title = _clean(re.sub(r"<[^>]+>", " ", m.group(1)))

    return item


# ── Playwright-based Scraping (Search, Ranking) ──

async def _scrape_with_playwright(
    url: str,
    max_results: int,
    item_selector: str = 'a[href*="/work/=/product_id/"]',
) -> list[DLsiteItem]:
    """Use Playwright to render Vue.js pages and extract product links,
    then scrape details via direct HTTP."""
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
        # Set adult cookie
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

        # Wait for Vue to render product items
        await asyncio.sleep(3)
        try:
            await page.wait_for_selector(item_selector, timeout=30000)
        except Exception:
            pass  # No items found

        # Extract product IDs from the page
        html = await page.content()
        links = re.findall(r'/work/=/product_id/((?:RJ|VJ|BJ|RE|VE)\d+)', html)
        links = list(dict.fromkeys(links))  # Dedupe preserving order

        await browser.close()

    if not links:
        return []

    # Scrape each product detail via HTTP (faster than Playwright per-item)
    session = create_session()
    results = []
    for wid in links[:max_results]:
        item = scrape_product_detail(session, wid)
        if item:
            results.append(item)
        time.sleep(CRAWL_DELAY)

    return results


def scrape_search(
    keyword: str,
    work_type: str = "all",
    max_results: int = 50,
) -> list[DLsiteItem]:
    """Search DLsite by keyword. Uses Playwright for JS rendering."""
    base = WORK_TYPE_PATHS.get(work_type, "home")
    url = f"{BASE_URL}/{base}/fs?keyword={quote(keyword)}"
    return asyncio.run(_scrape_with_playwright(url, max_results))


def scrape_ranking(
    ranking_type: str = "daily",
    work_type: str = "all",
    max_results: int = 50,
) -> list[DLsiteItem]:
    """Scrape DLsite rankings via direct HTTP (server-rendered)."""
    base = WORK_TYPE_PATHS.get(work_type, "home")
    
    # Map short type to URL path
    period_map = {"daily": "day", "weekly": "week", "monthly": "month", "total": ""}
    period = period_map.get(ranking_type, "day")
    
    if period:
        url = f"{BASE_URL}/{base}/ranking/{period}"
    else:
        url = f"{BASE_URL}/{base}/ranking?date=all"
    
    session = create_session()
    resp = session.get(url)
    if resp.status_code != 200:
        print(f"  [ERR] Ranking page returned {resp.status_code}")
        return []
    
    html = resp.text
    # Check for Cloudflare
    if "Just a moment" in html[:500] or "しばらくお待ち" in html[:500]:
        print("  [ERR] Cloudflare block on ranking page")
        return []
    
    html = resp.text
    # Extract all work IDs from ranking page
    work_ids = re.findall(r'/(?:RJ|VJ|BJ|RE|VE|SJ)\d{8}', html)
    work_ids = list(dict.fromkeys(work_ids))  # Dedupe preserve order
    # Clean up the leading slash
    work_ids = [w.lstrip("/") for w in work_ids]
    
    if not work_ids:
        return []
    
    # Scrape each product detail
    results = []
    for wid in work_ids[:max_results]:
        item = scrape_product_detail(session, wid)
        if item:
            results.append(item)
        time.sleep(CRAWL_DELAY)
    
    return results


def scrape_product_ids(
    product_ids: list[str],
) -> list[DLsiteItem]:
    """Scrape specific product IDs via direct HTTP (fastest)."""
    session = create_session()
    results = []
    for pid in product_ids:
        item = scrape_product_detail(session, pid.strip())
        if item:
            results.append(item)
        time.sleep(CRAWL_DELAY)
    return results
