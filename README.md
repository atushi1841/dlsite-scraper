# DLsite Scraper — Japan Doujin Marketplace Data Extractor

Scrape [DLsite](https://www.dlsite.com) — Japan's largest doujin (同人) digital marketplace with 600,000+ products. Extract structured product data from voice/ASMR, games, manga, music, and software categories.

**Data source**: DLsite internal JSON API (v2 + v1 hybrid). Returns **69 fields** per product with no JavaScript required.

## Features

- **Keyword Search** — Search by title or keyword (Playwright-powered for Vue.js rendering)
- **Ranking Scraping** — Daily, weekly, monthly, and all-time rankings (direct HTTP)
- **New Releases** — Latest products from home/new or maniax/new (direct HTTP)
- **Circle Works** — All products by a specific circle/brand (direct HTTP)
- **Product ID Mode** — Scrape specific RJ/VJ/BJ/RE/VE IDs directly (fastest)
- **69 Data Fields** per product including:
  - Title, circle/brand name, circle ID, work ID (RJ/VJ/BJ)
  - Price (sale + original), discount rate, sale status
  - **Multi-currency prices**: JPY, USD, EUR, GBP, TWD, CNY, KRW
  - Voice actors, genres, age rating
  - Work type, file format, file size
  - Sample images, full description
  - **Download count**, **rating (★4.92)**, rating count
  - **Wishlist count**, **review count**
  - **Bulk‑buy price**, **bulk‑buy discount**, **is_bulkbuy**
  - **Limit download count**, **remaining download count**
  - **is_reservable**, **is_timesale**
  - **is_original_work**, **original_work_id**, **translation_lang**
  - **locale_official_price**, **locale_price**
  - **sex_category**
  - Release date, maker ID, site ID
- **Adult Content** — Full support for both all-ages and R18 products
- **No Cloudflare** — Direct access with no blocking

## Use Cases

- **Price monitoring** — Track sales and discounts on doujin content
- **AI training data** — Build datasets for Japanese content recommendation
- **Market research** — Analyze trends by genre, circle, and pricing
- **Catalog building** — Create searchable databases of doujin products
- **Translation planning** — Identify popular untranslated works

## Quick Start

### Keyword Search
Search for ASMR voice products:
```json
{
  "searchMode": "search",
  "searchKeyword": "ASMR",
  "maxResults": 25
}
```

### Rankings
Get this week's top products:
```json
{
  "searchMode": "ranking",
  "rankingType": "weekly",
  "maxResults": 50
}
```

### Specific Products
Scrape specific works by their RJ/VJ/BJ ID:
```json
{
  "searchMode": "product_ids",
  "productIds": "RJ01678178,RJ01678210,VJ01000001"
}
```

## Input Parameters

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `searchMode` | enum | `ranking` | `search`, `ranking`, or `product_ids` |
| `searchKeyword` | string | `ASMR` | Keyword for search mode |
| `rankingType` | enum | `weekly` | `daily`, `weekly`, `monthly`, or `total` |
| `productIds` | string | — | Comma-separated RJ/VJ/BJ/RE/VE IDs (for product_ids mode) |
| `maxResults` | int | 50 | Max items to return (1–200) |

## Output Example

```json
{
  "work_id": "RJ01678178",
  "title": "【ブルーアーカイブ】ミヨASMR～先生が、悪いんですよ?～",
  "circle": "Yostar",
  "circle_id": "RG62982",
  "price_jpy": 1188,
  "original_price_jpy": 1320,
  "price_usd": 8.07,
  "price_eur": 7.06,
  "price_gbp": 6.05,
  "discount_rate": 10,
  "sale_active": true,
  "release_date": "2026-07-27 00:00:00",
  "voice_actor": "花岩香奈",
  "age_rating": "All ages",
  "work_type": "Voice/ASMR",
  "work_type_code": "SOU",
  "file_format": "SND, DLP, TRI, JPN, REV",
  "file_size": "1.12GB",
  "genres": ["ASMR", "癒し", "バイノーラル/ダミヘ", "萌え"],
  "dl_count": 20110,
  "rating": 4.92,
  "rating_count": 225,
  "wishlist_count": 4109,
  "review_count": 12,
  "description": "癒しのASMR作品。バイノーラル録音で…",
  "image_url": "https://img.dlsite.jp/...",
  "url": "https://www.dlsite.com/home/work/=/product_id/RJ01678178.html",
  "site_id": "home",
  "detail_level": "api"
}
```

## Technical Details

- **Product details**: DLsite internal JSON API (v2 product.json + v1 info/ajax hybrid) — 255+86 fields, no JS needed
- **Listings, rankings & new releases**: Direct HTTP — server-rendered HTML with work IDs
- **Circle profile**: Direct HTTP — all works from a circle profile page
- **Keyword search**: Playwright (Chromium) for Vue.js page rendering — extracts IDs, then API for details
- **Work type filter**: Post-filter by voice/game/manga/music/soft
- **Parallel scraping**: ThreadPoolExecutor with 8 workers for fast batch processing
- **Retry logic**: Automatic retry with backoff on network errors

## Tech Stack

Python, httpx, Playwright (search only), concurrent.futures, re (regex), Apify SDK.

## Pricing

- **Run start**: $0.00005 per run
- **Per result**: $0.005 per product (charged only for items actually captured)
- A default test run (e.g. 20 results) typically costs **under $0.02**

No free-tier complexity — you pay only for what you extract.

## Proxy Requirement

- **Free plan**: use `useApifyProxy: true` only (auto proxies). This works because DLsite does not block datacenter IPs.
- **`apifyProxyCountry: "JP"` requires a paid Apify plan** — on the free plan country-specific proxies return `407 Proxy Authentication Required`. Leave country unset unless you have a paid plan.

## Limitations

- **Keyword search** uses Playwright (Chromium) to render DLsite's Vue.js pages and is slower than the other modes (~30–60s warm-up).
- **Ranking / new releases / circle / product ID modes** use direct HTTP and are fast — prefer them for bulk scraping.
- `maxResults` is capped at 200 per run; run multiple times with different keywords/circles to scale.
- Site structure may change; if a mode returns 0 items, switch modes or check for DLsite updates.

## License

MIT
