"""
DLsite Scraper — Core scraping logic v2 (API-based).

Uses DLsite's internal JSON API for product details (86 fields, no JS needed).
Only Playwright for search results (Vue.js page).
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
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json,text/html,*/*",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US,en;q=0.5",
    "DNT": "1",
    "Referer": "https://www.dlsite.com/",
}
CRAWL_DELAY = 1.0
MAX_WORKERS = 8
MAX_RETRIES = 2

# API endpoint mapping by work ID prefix
API_PATH_MAP = {
    "RJ": "pro", "RE": "pro",
    "VJ": "maniax", "BJ": "maniax", "VE": "maniax", "SJ": "maniax",
}

# Work type code mapping
WORK_TYPE_MAP = {
    "SOU": "Voice/ASMR", "G": "Game", "C": "Manga/Comic",
    "M": "Music", "S": "Software", "VOI": "Voice", "VID": "Video",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip() if text else ""


@dataclass
class DLsiteItem:
    """Normalized DLsite product data (using API fields)."""
    work_id: str = ""
    title: str = ""              # work_name
    circle: str = ""             # maker_id → circle name
    circle_id: str = ""
    price_jpy: int | None = None   # price
    original_price_jpy: int | None = None  # official_price
    price_usd: float | None = None
    price_eur: float | None = None
    price_gbp: float | None = None
    discount_rate: int | None = None   # discount_rate (%)
    sale_active: bool = False          # is_sale
    release_date: str = ""            # regist_date
    voice_actor: str = ""
    age_rating: str = ""
    work_type: str = ""               # work_type → mapped
    work_type_code: str = ""
    file_format: str = ""             # options
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
    site_id: str = ""
    detail_level: str = "api"
    scraped_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.genres is None: d["genres"] = []
        if self.sample_images is None: d["sample_images"] = []
        d["scraped_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {k: v for k, v in d.items() if v is not None}


# ── HTTP Session ──

def create_session() -> httpx.Client:
    """Create HTTPX client with adult cookies for DLsite API access."""
    client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30, trust_env=False)
    # Set adult cookies directly (no initial page visit needed for API access)
    client.cookies.set("adult", "on", domain=".dlsite.com")
    client.cookies.set("ovad", "on", domain=".dlsite.com")
    return client


def _retry_get(session: httpx.Client, url: str) -> httpx.Response | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url)
            if resp.status_code == 200:
                return resp
            time.sleep(2 * attempt)
        except Exception:
            time.sleep(2 * attempt)
    return None


# ── Product Detail via API (primary) ──

def _prefix(wid: str) -> str:
    m = re.match(r"([A-Z]+)", wid)
    return m.group(1) if m else "RJ"


def scrape_detail_api(session: httpx.Client, work_id: str) -> DLsiteItem | None:
    """Scrape product detail via DLsite's internal JSON API (86 fields, no JS needed)."""
    prefix_str = _prefix(work_id)
    api_path = API_PATH_MAP.get(prefix_str, "pro")
    url = f"{BASE_URL}/{api_path}/product/info/ajax?product_id={work_id}"

    resp = _retry_get(session, url)
    if not resp:
        return None

    response_text = resp.text
    # Log first 200 chars on Apify for debugging
    try:
        data = json.loads(response_text)
    except Exception as e:
        return None

    if isinstance(data, list) and len(data) == 0:
        return None

    key = list(data.keys())[0] if isinstance(data, dict) else work_id
    p = data.get(key, {}) if isinstance(data, dict) else {}

    item = DLsiteItem(work_id=work_id)
    item.url = f"{BASE_URL}/home/work/=/product_id/{work_id}.html"

    # Basic info
    item.title = p.get("work_name", "")
    item.circle_id = p.get("maker_id", "")
    item.site_id = p.get("site_id", "")

    # Pricing
    item.price_jpy = p.get("price")
    item.original_price_jpy = p.get("official_price")
    item.discount_rate = p.get("discount_rate")
    item.sale_active = bool(p.get("is_sale"))

    # Multi-currency
    cp = p.get("currency_official_price", {})
    if isinstance(cp, dict):
        item.price_usd = cp.get("USD")
        item.price_eur = cp.get("EUR")
        item.price_gbp = cp.get("GBP")

    # Dates
    item.release_date = p.get("regist_date", "")

    # Work type
    wt = p.get("work_type", "")
    item.work_type_code = wt
    item.work_type = WORK_TYPE_MAP.get(wt, wt)

    # File info
    opts = p.get("options", "")
    if opts:
        opt_list = opts.split("#")
        item.file_format = ", ".join(opt_list)

    # Age rating
    age_map = {1: "All ages", 2: "R15", 3: "R18"}
    item.age_rating = age_map.get(p.get("age_category"), str(p.get("age_category", "")))

    # Downloads & ratings
    item.dl_count = p.get("dl_count")
    item.rating = p.get("rate_average_2dp")
    item.rating_count = p.get("rate_count")
    item.wishlist_count = p.get("wishlist_count")
    item.review_count = p.get("review_count")

    # Image
    img = p.get("work_image", "")
    if img:
        item.image_url = f"https:{img}" if img.startswith("//") else img

    # Empty fields that need HTML scraping
    # (voice_actor, file_size, genres, description are only in HTML)

    return item


# ── Product Detail via HTML (supplement for API-only fields) ──

def _scrape_detail_html_supplement(session: httpx.Client, item: DLsiteItem) -> DLsiteItem:
    """Supplement API data with HTML-only fields (voice_actor, genres, description, file_size)."""
    if not item.work_id:
        return item

    prefix_str = _prefix(item.work_id)
    base_path = "home" if prefix_str in ("RJ", "RE") else "maniax"
    url = f"{BASE_URL}/{base_path}/work/=/product_id/{item.work_id}.html"

    resp = _retry_get(session, url)
    if not resp:
        return item

    html = resp.text

    # Voice actor, genres, file_size from detail table
    for th, td in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", html, re.DOTALL):
        key = _clean(re.sub(r"<[^>]+>", " ", th))
        val = _clean(re.sub(r"<[^>]+>", " ", td))
        if "声優" in key and not item.voice_actor:
            item.voice_actor = val
        elif "ファイル容量" in key and not item.file_size:
            item.file_size = val
        elif "ジャンル" in key and not item.genres:
            item.genres = [g.strip() for g in re.split(r"[\n/]", val) if g.strip()]

    # Circle name from HTML (API only gives maker_id)
    if not item.circle:
        cm = re.search(r"(?:サークル名|ブランド|メーカー)[：:]?\s*</th>\s*<td[^>]*>(.*?)</td>", html, re.DOTALL)
        if cm:
            raw = re.sub(r"<[^>]+>", " ", cm.group(1))
            item.circle = _clean(re.sub(r"(?:フォロー|お気に入り|閉じる).*$", "", raw))

    # Description
    dm = re.search(r'class="work_description[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
    if dm:
        desc = re.sub(r"<[^>]+>", "\n", dm.group(1))
        item.description = _clean(desc.replace("&nbsp;", " "))[:3000]

    # Sample images
    samples = re.findall(r'<img[^>]*src="(https://[^"]*(?:sample|img)[^"]*)"', html)
    if samples:
        item.sample_images = samples[:10]

    return item


# ── Complete product scrape ──

def scrape_one(wid: str) -> DLsiteItem | None:
    """Scrape one product: API (fast) + HTML supplement (for extra fields)."""
    session = create_session()
    item = scrape_detail_api(session, wid)
    if not item:
        return None
    # Supplement with HTML-only fields
    item = _scrape_detail_html_supplement(session, item)
    return item


def scrape_many(work_ids: list[str], max_results: int) -> list[DLsiteItem]:
    """Batch scrape products in parallel."""
    if not work_ids:
        return []
    ids = work_ids[:max_results]
    results: list[DLsiteItem] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        fut_map = {pool.submit(scrape_one, wid): wid for wid in ids}
        for future in concurrent.futures.as_completed(fut_map):
            item = future.result()
            if item:
                results.append(item)
            time.sleep(CRAWL_DELAY / MAX_WORKERS)
    by_id = {r.work_id: r for r in results}
    return [by_id[wid] for wid in ids if wid in by_id]


# ── Ranking ──

def scrape_ranking(
    ranking_type: str = "daily",
    max_results: int = 50,
) -> list[DLsiteItem]:
    """Scrape rankings via HTML (get work IDs) + API (product details)."""
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

    work_ids = re.findall(r"/(?:RJ|VJ|BJ|RE|VE|SJ)\d{8}", html)
    work_ids = list(dict.fromkeys(wid.lstrip("/") for wid in work_ids))
    return scrape_many(work_ids, max_results) if work_ids else []


# ── Product IDs ──

def scrape_product_ids(product_ids: list[str]) -> list[DLsiteItem]:
    """Scrape specific product IDs via API (fastest)."""
    return scrape_many([pid.strip() for pid in product_ids if pid.strip()], len(product_ids))


# ── Search (Playwright) ──

async def _run_search_pw(url: str, max_results: int) -> list[DLsiteItem]:
    """Search via Playwright (for Vue.js rendering), then API for details."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-default-browser-check", "--disable-component-update"],
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
        await browser.close()

    links = re.findall(r'/work/=/product_id/((?:RJ|VJ|BJ|RE|VE|SJ)\d+)', html)
    links = list(dict.fromkeys(links))
    return scrape_many(links, max_results)


def scrape_search(keyword: str, max_results: int = 50) -> list[DLsiteItem]:
    """Search DLsite. Playwright for Vue.js + API for details."""
    url = f"{BASE_URL}/home/fs?keyword={quote(keyword)}"

    def _run():
        return asyncio.run(_run_search_pw(url, max_results))

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: _run()).result()
    except RuntimeError:
        return _run()
