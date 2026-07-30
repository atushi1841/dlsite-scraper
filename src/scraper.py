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
    "RJ": "maniax", "RE": "maniax",
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
    """Normalized DLsite product data (using v2 product.json API — 255 fields)."""
    work_id: str = ""
    title: str = ""              # product_name
    circle: str = ""             # maker_name
    circle_id: str = ""
    price_jpy: int | None = None
    original_price_jpy: int | None = None
    price_usd: float | None = None
    price_eur: float | None = None
    price_without_tax: int | None = None
    official_price_without_tax: int | None = None
    discount_rate: int | None = None
    sale_active: bool = False
    release_date: str = ""
    update_date: str = ""
    campaign_end_date: str = ""
    campaign_start_date: str = ""
    voice_actor: str = ""
    scenario_writer: str = ""
    age_rating: str = ""
    sex_category: int | None = None
    work_type: str = ""
    work_type_code: str = ""
    work_category: str = ""
    file_format: str = ""
    file_type: str = ""
    file_type_special: str = ""
    file_size_bytes: int | None = None
    file_size_display: str = ""
    contents_count: int | None = None
    genres: list[str] = None
    genre_ids: list[int] = None
    platform: list[str] = None
    language_editions: list[str] = None
    rank_day: int | None = None
    rank_week: int | None = None
    rank_month: int | None = None
    rank_total: int | None = None
    rating_count_detail: dict = None
    description: str = ""
    description_full: str = ""
    image_url: str = ""
    sample_images: list[str] = None
    work_name_kana: str = ""
    discount_campaign_price: int | None = None
    discount_start: str = ""
    discount_end: str = ""
    url: str = ""
    site_id: str = ""
    detail_level: str = "api_v2"
    scraped_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        for field in ("genres", "genre_ids", "platform", "language_editions", "sample_images"):
            if getattr(self, field) is None:
                d[field] = []
        if self.rating_count_detail is None:
            d["rating_count_detail"] = {}
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
    api_path = API_PATH_MAP.get(prefix_str, "maniax")
    url = f"{BASE_URL}/{api_path}/api/=/product.json?workno={work_id}"

    resp = _retry_get(session, url)
    if not resp:
        return None

    try:
        data = json.loads(resp.text)
    except Exception:
        return None

    # New API returns array with single product object
    if isinstance(data, list) and len(data) > 0:
        p = data[0]
    elif isinstance(data, dict):
        p = data
    else:
        return None

    item = DLsiteItem(work_id=work_id, detail_level="api_v2")
    item.url = f"{BASE_URL}/home/work/=/product_id/{work_id}.html"

    # Basic info
    item.title = p.get("product_name", "") or p.get("work_name", "")
    item.circle = p.get("maker_name", "")
    item.circle_id = p.get("maker_id", "")
    item.site_id = p.get("site_id", "")

    # Pricing — direct currency fields
    item.price_jpy = p.get("price")
    item.original_price_jpy = p.get("official_price")
    item.discount_rate = p.get("discount_rate")
    item.sale_active = bool(p.get("is_discount_work"))
    item.price_usd = p.get("price_en") or p.get("official_price_usd")
    item.price_eur = p.get("price_eur") or p.get("official_price_eur")

    # Price without tax (useful for Japan tax calculation)
    item.price_without_tax = p.get("price_without_tax")
    item.official_price_without_tax = p.get("official_price_without_tax")

    # Dates
    item.release_date = p.get("regist_date", "")
    item.update_date = p.get("update_date", "")
    item.campaign_end_date = p.get("campaign_end_date", "")
    item.campaign_start_date = p.get("campaign_start_date", "")

    # Work type
    wt = p.get("work_type", "")
    item.work_type_code = wt
    item.work_type = p.get("work_type_string", "")
    item.work_category = p.get("work_category", "")

    # File info
    opts = p.get("options", "")
    item.file_format = ", ".join(opts.split("#")) if opts else ""
    item.file_type = p.get("file_type", "")
    item.file_type_special = p.get("file_type_special", "")
    item.file_size_bytes = p.get("contents_file_size")
    item.contents_count = p.get("content_count")

    # Age rating
    age_map = {1: "All ages", 2: "R15", 3: "R18"}
    item.age_rating = age_map.get(p.get("age_category"), str(p.get("age_category", "")))
    item.sex_category = p.get("sex_category")

    # Downloads & ratings (from new API)
    # price/info/ajax had dl_count, this one doesn't — get from old API as supplement

    # Rating (from rate_count_detail)
    rcd = p.get("rate_count_detail")
    if isinstance(rcd, dict):
        item.rating_count_detail = rcd

    # Rankings!
    item.rank_day = p.get("rank_day")
    item.rank_week = p.get("rank_week")
    item.rank_month = p.get("rank_month")
    item.rank_total = p.get("rank_total")

    # Image (structured)
    img_main = p.get("image_main", {})
    if isinstance(img_main, dict):
        url = img_main.get("url", "")
        if url:
            item.image_url = f"https:{url}" if url.startswith("//") else url

    # Alternative: image_thumb
    if not item.image_url:
        thumb = p.get("image_thumb", "")
        if thumb:
            item.image_url = f"https:{thumb}" if thumb.startswith("//") else thumb

    # Sample images
    samples = p.get("image_samples", [])
    if isinstance(samples, list):
        item.sample_images = []
        for s in samples:
            if isinstance(s, dict):
                url = s.get("url", "")
                if url:
                    item.sample_images.append(f"https:{url}" if url.startswith("//") else url)

    # Description / intro
    item.description = p.get("intro_s", "") or p.get("intro", "") or ""
    item.description_full = p.get("introductions", "") or ""

    # Genres (structured)
    genres_data = p.get("genres", [])
    if isinstance(genres_data, list):
        item.genres = [g.get("name", "") for g in genres_data if isinstance(g, dict) and g.get("name")]

    # Creators
    creators = p.get("creaters", {})
    if isinstance(creators, dict):
        va = creators.get("voice_by", [])
        if va and isinstance(va, list):
            item.voice_actor = ", ".join(v.get("name", "") for v in va if isinstance(v, dict))
        scenario = creators.get("scenario_by", [])
        if scenario and isinstance(scenario, list):
            item.scenario_writer = ", ".join(s.get("name", "") for s in scenario if isinstance(s, dict))

    # Platform
    platform = p.get("platform", [])
    if isinstance(platform, list):
        item.platform = platform

    # Language editions
    lang_editions = p.get("language_editions", [])
    if isinstance(lang_editions, list):
        item.language_editions = [le.get("language", "") for le in lang_editions if isinstance(le, dict)]

    # Work name in kana (useful for search)
    item.work_name_kana = p.get("work_name_kana", "")

    # Discount info
    disc = p.get("discount", {})
    if isinstance(disc, dict):
        item.discount_start = disc.get("start_date")
        item.discount_end = disc.get("end_date")
        item.discount_campaign_price = disc.get("campaign_price")

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
        elif "ファイル容量" in key:
            item.file_size_display = val
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
